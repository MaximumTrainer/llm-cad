"""Model clients for headless generation (SPEC 10.5).

The MCP server never calls an LLM: the host does, and drives the tools.
``llm-cad generate-course`` runs with no host in the loop, so it needs
its own client. Two are provided:

* ``AnthropicClient`` calls Claude through the Anthropic Messages API
  with the official ``anthropic`` SDK. Chosen for an Anthropic key
  (``sk-ant-...``) or ``LLM_CAD_MODEL_PROVIDER=anthropic``.
* ``OpenAICompatibleClient`` speaks the OpenAI-compatible
  ``/chat/completions`` shape, which OpenRouter (the default for any
  other key) and most providers accept.

The API key comes from ``LLM_CAD_MODEL_API_KEY`` and is only ever placed
in the ``Authorization`` header. It is never logged, never included in
an exception message, and never written to ``report.json``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Final, Protocol

import anthropic
import httpx
from anthropic.types.beta import BetaMessageParam

API_KEY_ENV = "LLM_CAD_MODEL_API_KEY"
BASE_URL_ENV = "LLM_CAD_MODEL_BASE_URL"
PROVIDER_ENV = "LLM_CAD_MODEL_PROVIDER"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_COMPATIBLE = "openai-compatible"
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_OPENAI_COMPATIBLE)
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# The same default the live LLM tests use (tests/llm_harness.py): a model
# that writes competent CadQuery. Callers that need reproducibility pin
# --model explicitly anyway.
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
# The Anthropic default: the current Opus.
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
# Room for adaptive thinking plus a CadQuery reply, still non-streaming.
ANTHROPIC_MAX_TOKENS = 16000
# Writing CadQuery that runs first time is intelligence-sensitive work.
ANTHROPIC_EFFORT: Final = "high"
# Server-side fallback on a policy decline (Messages API beta).
ANTHROPIC_FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"

REQUEST_TIMEOUT_S = 180.0
MAX_TOKENS = 8192
# Transient provider failures (rate limit, overload) are retried; a 4xx
# that is the caller's fault is not, because retrying cannot fix it.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
_RETRY_DELAYS_S = (2.0, 8.0)

Message = dict[str, str]


class ModelError(RuntimeError):
    """The model call failed in a way the caller should report."""


class ModelClient(Protocol):
    """Anything that turns a conversation into the assistant's reply."""

    def complete(self, messages: list[Message], *, model: str, seed: int) -> str: ...


def api_key_from_env() -> str | None:
    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None


def provider_for(api_key: str) -> str:
    """Which client a key needs: ``LLM_CAD_MODEL_PROVIDER`` wins, else an
    Anthropic key (``sk-ant-``) means the Messages API."""
    chosen = os.environ.get(PROVIDER_ENV, "").strip().lower()
    if chosen:
        if chosen not in PROVIDERS:
            raise ValueError(
                f"{PROVIDER_ENV} must be one of {PROVIDERS}, got {chosen!r}"
            )
        return chosen
    return (
        PROVIDER_ANTHROPIC
        if api_key.startswith("sk-ant-")
        else PROVIDER_OPENAI_COMPATIBLE
    )


def default_model_for(provider: str) -> str:
    return ANTHROPIC_DEFAULT_MODEL if provider == PROVIDER_ANTHROPIC else DEFAULT_MODEL


class AnthropicClient:
    """Claude through the Anthropic Messages API.

    System turns become the top-level ``system`` prompt; the rest of the
    conversation passes through unchanged. Thinking is adaptive (always on
    for the current Opus) at ``high`` effort, and a policy decline falls
    back server-side to the model's default fallback. The Messages API has
    no ``seed``, so *seed* is accepted for the protocol and not sent: as
    with any provider, byte-identical output is guaranteed for the
    pipeline after the reply, not for the reply itself.

    The SDK retries 408/409/429/5xx and connection errors itself. Errors
    are reported by type and status only, so nothing from the request
    headers can reach a log or ``report.json``.
    """

    def __init__(
        self, api_key: str, *, client: anthropic.Anthropic | None = None
    ) -> None:
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=len(_RETRY_DELAYS_S)
        )

    def complete(self, messages: list[Message], *, model: str, seed: int) -> str:
        del seed  # not a Messages API parameter; see the class docstring
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        conversation: list[BetaMessageParam] = [
            {
                "role": "assistant" if m["role"] == "assistant" else "user",
                "content": m["content"],
            }
            for m in messages
            if m["role"] != "system"
        ]
        try:
            response = self._client.beta.messages.create(
                model=model,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                messages=conversation,
                output_config={"effort": ANTHROPIC_EFFORT},
                betas=[ANTHROPIC_FALLBACK_BETA],
                fallbacks="default",
                system=system or anthropic.omit,
            )
        except anthropic.APIStatusError as exc:
            raise ModelError(
                f"model request returned HTTP {exc.status_code} ({type(exc).__name__})"
            ) from None
        except anthropic.APIError as exc:
            raise ModelError(f"model request failed: {type(exc).__name__}") from None

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise ModelError(f"model declined the request (category: {category})")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ModelError(
                f"model returned an empty reply (stop_reason: {response.stop_reason})"
            )
        return text


class OpenAICompatibleClient:
    """``POST {base_url}/chat/completions`` with temperature 0 and a seed.

    ``seed`` is forwarded to the provider, which makes sampling
    reproducible where the provider supports it. Providers only promise
    best effort, so byte-identical output across runs is guaranteed for
    the pipeline *after* the model reply, not for the reply itself.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (
            base_url or os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL
        ).rstrip("/")
        self._transport = transport

    def complete(self, messages: list[Message], *, model: str, seed: int) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "seed": seed,
            "max_tokens": MAX_TOKENS,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/chat/completions"

        last_status: int | None = None
        with httpx.Client(
            timeout=REQUEST_TIMEOUT_S, transport=self._transport
        ) as client:
            for delay in (*_RETRY_DELAYS_S, None):
                try:
                    resp = client.post(url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    # The exception text can echo the request; keep only
                    # its type so nothing from the headers can leak.
                    if delay is None:
                        raise ModelError(
                            f"model request failed: {type(exc).__name__}"
                        ) from None
                    time.sleep(delay)
                    continue
                last_status = resp.status_code
                if resp.status_code in _RETRY_STATUSES and delay is not None:
                    time.sleep(delay)
                    continue
                if resp.status_code != 200:
                    raise ModelError(
                        f"model request returned HTTP {resp.status_code}: "
                        f"{resp.text[:300]}"
                    )
                return _content_of(resp.json())
        raise ModelError(f"model request failed after retries (HTTP {last_status})")


def _content_of(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ModelError("model response had no choices[0].message.content") from None
    if not isinstance(content, str) or not content.strip():
        raise ModelError("model returned an empty reply")
    return content
