"""A minimal chat-completions client for headless generation (SPEC 10.5).

The MCP server never calls an LLM: the host does, and drives the tools.
``llm-cad generate-course`` runs with no host in the loop, so it needs
its own client. It speaks the OpenAI-compatible ``/chat/completions``
shape, which OpenRouter (the default) and most providers accept, so the
model is a ``--model`` string rather than a code change.

The API key comes from ``LLM_CAD_MODEL_API_KEY`` and is only ever placed
in the ``Authorization`` header. It is never logged, never included in
an exception message, and never written to ``report.json``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Protocol

import httpx

API_KEY_ENV = "LLM_CAD_MODEL_API_KEY"
BASE_URL_ENV = "LLM_CAD_MODEL_BASE_URL"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# The same default the live LLM tests use (tests/llm_harness.py): a model
# that writes competent CadQuery. Callers that need reproducibility pin
# --model explicitly anyway.
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

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

    def complete(self, messages: list[Message], *, model: str, seed: int) -> str:
        ...


def api_key_from_env() -> str | None:
    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None


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
