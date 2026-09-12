"""Drive the cad-mcp server with a real LLM over OpenRouter.

This is test infrastructure, not a test module — it holds no ``test_*``
functions.  ``tests/test_llm_integration.py`` uses it to verify the
behaviour SPEC 9.1 actually specifies: *an LLM* designing a part through
the tool surface, seeing renders, and iterating — as opposed to the
existing acceptance tests, which replay hard-coded example code and so
never exercise the tool descriptions, the error wording, or the image
round-trip.

What it checks that unit tests cannot:

- the tool JSON schemas are consumable by a real function-calling model;
- ``render_views`` images survive the MCP -> base64 -> vision-API path
  and are genuinely readable (SPEC G3, "the LLM must be able to see what
  it built");
- the structured errors from SPEC N3 are actionable enough for a model
  to recover without human help.

Requires ``OPENROUTER_API_KEY``.  Never hard-code a key here: this file
is committed.  The model is configurable with ``OPENROUTER_MODEL`` and
must support both tool calling and image input.

Run the bracket loop manually and print a transcript::

    OPENROUTER_API_KEY=sk-or-... uv run python tests/llm_harness.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# A capable tool-calling vision model is the honest default: the SPEC 9.1
# test is meant to measure whether *this server's* tools, descriptions and
# errors let a competent model succeed.  Pointing it at a weak model
# conflates "the server is unclear" with "the model can't write CadQuery".
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

# A cheap vision+tools model, enough to prove schemas and the image path.
# Used by the plumbing tests so the suite stays affordable.
CHEAP_MODEL = "google/gemini-2.5-flash-lite"

# Keep a single tool result from eating the context window.  validate_mesh
# on a dense mesh is verbose; the head carries the verdict.
MAX_TOOL_RESULT_CHARS = 6000

# OpenRouter bills against the *requested* max_tokens, so leaving this
# unset makes a small balance fail with HTTP 402 before any work happens.
DEFAULT_MAX_TOKENS = 2048

REQUEST_TIMEOUT_S = 180.0


class CreditsExhausted(RuntimeError):
    """The key is valid but the account cannot fund the request.

    Distinguished from other failures so tests can skip rather than
    report a false defect in the server.
    """


def api_key() -> str | None:
    """The OpenRouter key, or None when unset."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return key or None


def model_name() -> str:
    return os.environ.get("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL


def max_tokens() -> int:
    raw = os.environ.get("OPENROUTER_MAX_TOKENS", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_MAX_TOKENS


def available() -> bool:
    return api_key() is not None


# ------------------------------------------------------------------
# MCP -> OpenAI tool schema translation
# ------------------------------------------------------------------


def mcp_tools_to_openai(
    tools: list[Any],
    include: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert MCP ``Tool`` objects to OpenAI function-tool definitions.

    The MCP SDK (v2) exposes ``input_schema`` as a JSON Schema object,
    which is already the shape OpenAI's ``function.parameters`` wants.
    Passing it through unmodified is deliberate: if a schema this server
    publishes is malformed, the provider rejects the request and the test
    fails — which is the point.
    """
    out: list[dict[str, Any]] = []
    for tool in tools:
        if include is not None and tool.name not in include:
            continue
        schema = dict(tool.input_schema or {"type": "object", "properties": {}})
        schema.pop("title", None)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (tool.description or "").strip(),
                    "parameters": schema,
                },
            }
        )
    return out


# ------------------------------------------------------------------
# Transcript records
# ------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    text_result: str
    image_count: int = 0
    failed: bool = False


@dataclass
class LoopResult:
    """Everything the assertions need to inspect after a run."""

    final_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    usage: dict[str, Any] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [c.name for c in self.tool_calls]

    def calls_to(self, name: str) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.name == name]

    def count(self, name: str) -> int:
        return len(self.calls_to(name))

    def images_returned(self) -> int:
        return sum(c.image_count for c in self.tool_calls)

    def transcript(self) -> str:
        lines = [f"turns={self.turns}  usage={self.usage}"]
        for i, c in enumerate(self.tool_calls, 1):
            args = json.dumps(c.arguments)
            if len(args) > 160:
                args = args[:160] + "…"
            flag = "ERR " if c.failed else ""
            lines.append(f"{i:2}. {flag}{c.name}({args})")
            head = c.text_result.strip().splitlines()
            if head:
                lines.append(f"      -> {head[0][:140]}")
            if c.image_count:
                lines.append(f"      -> [{c.image_count} image(s)]")
        lines.append(f"FINAL: {self.final_text[:600]}")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Tool result -> chat messages
# ------------------------------------------------------------------


def _split_content(content: list[Any]) -> tuple[str, list[tuple[str, str]]]:
    """Split MCP content blocks into (text, [(mime, base64), ...])."""
    texts: list[str] = []
    images: list[tuple[str, str]] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
        elif kind == "image":
            images.append(
                (getattr(block, "mime_type", "image/png"), block.data)
            )
    return "\n".join(texts), images


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return (
        text[:MAX_TOOL_RESULT_CHARS]
        + f"\n… [truncated, {len(text)} chars total]"
    )


# ------------------------------------------------------------------
# OpenRouter client
# ------------------------------------------------------------------


async def _chat(
    client: httpx.AsyncClient,
    key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tokens: int,
) -> dict[str, Any]:
    resp = await client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Optional OpenRouter attribution headers.
            "HTTP-Referer": "https://github.com/MaximumTrainer/llm-cad",
            "X-Title": "cad-mcp integration test",
        },
        json={
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": tokens,
        },
    )
    if resp.status_code == 402:
        raise CreditsExhausted(
            f"OpenRouter has insufficient credit for model {model!r}: "
            f"{resp.text[:300]}"
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter HTTP {resp.status_code}: {resp.text[:600]}"
        )
    data: dict[str, Any] = resp.json()
    # OpenRouter reports upstream provider failures inside a 200 body.
    if "error" in data and "choices" not in data:
        err = data["error"]
        if str(err.get("code")) == "402":
            raise CreditsExhausted(str(err.get("message", err)))
        raise RuntimeError(f"OpenRouter error: {err}")
    if "choices" not in data:
        raise RuntimeError(f"Unexpected OpenRouter payload: {data}")
    return data


# ------------------------------------------------------------------
# The loop
# ------------------------------------------------------------------


async def run_design_loop(
    server: Any,
    user_request: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    max_turns: int = 12,
    include_tools: set[str] | None = None,
    tokens: int | None = None,
) -> LoopResult:
    """Let the model drive the cad-mcp tools until it stops calling them.

    Args:
        server: the ``MCPServer`` instance (tools are called in-process,
            so this exercises the same code path the stdio transport
            reaches, without a subprocess).
        user_request: the design brief.
        system_prompt: defaults to the server's own ``design_workflow``
            prompt, so the test measures the pedagogy the server actually
            ships (SPEC G6) rather than instructions invented here.
        max_turns: hard cap on model round-trips; protects against a
            model that loops forever, and bounds cost.
        include_tools: restrict the exposed tool set (cheap smoke tests).
    """
    key = api_key()
    if key is None:
        msg = "OPENROUTER_API_KEY is not set"
        raise RuntimeError(msg)

    model = model or model_name()
    tokens = tokens or max_tokens()

    if system_prompt is None:
        got = await server.get_prompt("design_workflow", {})
        system_prompt = "\n\n".join(
            m.content.text
            for m in got.messages
            if getattr(m.content, "type", None) == "text"
        )

    mcp_tools = await server.list_tools()
    tools = mcp_tools_to_openai(mcp_tools, include=include_tools)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request},
    ]

    result = LoopResult(final_text="", messages=messages)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        for turn in range(max_turns):
            result.turns = turn + 1
            data = await _chat(client, key, model, messages, tools, tokens)

            usage = data.get("usage") or {}
            for field_name in ("prompt_tokens", "completion_tokens"):
                result.usage[field_name] = result.usage.get(
                    field_name, 0
                ) + usage.get(field_name, 0)

            choice = data["choices"][0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            # The assistant turn must go back verbatim, tool_calls included.
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                }
            )

            if not tool_calls:
                result.final_text = message.get("content") or ""
                return result

            pending_images: list[tuple[str, str, str]] = []

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}

                try:
                    tool_result = await server.call_tool(name, args)
                    text, images = _split_content(tool_result.content)
                    failed = bool(getattr(tool_result, "is_error", False))
                except Exception as exc:
                    text = f"Tool raised {type(exc).__name__}: {exc}"
                    images = []
                    failed = True

                result.tool_calls.append(
                    ToolCall(
                        name=name,
                        arguments=args,
                        text_result=text,
                        image_count=len(images),
                        failed=failed,
                    )
                )

                tool_text = text or "(no text content)"
                if images:
                    tool_text += (
                        f"\n[{len(images)} rendered image(s) follow "
                        f"in the next message]"
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": _truncate(tool_text),
                    }
                )

                for mime, b64 in images:
                    pending_images.append((name, mime, b64))

            # A `tool` message cannot carry an image in the OpenAI schema,
            # so renders come back as a user turn immediately afterwards.
            # This is the step that proves the visual feedback loop works.
            if pending_images:
                parts: list[dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": (
                            "Rendered output from "
                            f"{pending_images[0][0]}. Inspect it and "
                            "critique it against the requirements before "
                            "continuing."
                        ),
                    }
                ]
                for _name, mime, b64 in pending_images:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        }
                    )
                messages.append({"role": "user", "content": parts})

    result.final_text = (
        f"(stopped: hit max_turns={max_turns} without a final answer)"
    )
    return result


# ------------------------------------------------------------------
# Manual runner
# ------------------------------------------------------------------

SPEC_9_1_REQUEST = (
    "Design a wall-mount bracket for a 30mm pipe, with two M4 screw "
    "holes and 3mm walls. Follow the workflow exactly: execute, render, "
    "critique the image, measure, validate, then export an STL. Stop "
    "once validate_mesh reports a watertight mesh and you have exported."
)


async def _main() -> int:
    sys.path.insert(0, "src")
    from cad_mcp import session
    from cad_mcp.server import mcp

    if not available():
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2

    session.cleanup_all()
    print(f"model: {model_name()}\n")
    result = await run_design_loop(mcp, SPEC_9_1_REQUEST)
    print(result.transcript())
    session.cleanup_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
