"""One response shape for every tool (SPEC 4, N3).

The tools used to return at least four different shapes: plain prose from
`execute_cad` on success but JSON on argument errors, a bare report dict
from `validate_mesh` for one part but a wrapper for several, `{"ok": ...}`
JSON from most others, and raw strings from `ping`/`reset_session`.

The tell was in the test suite, which checked `text.startswith("OK")` and
*also* fell back to `json.loads`, because the caller genuinely could not
know which it would get. Every one of those shapes is something an LLM
has to parse, and the inconsistency bites hardest on the error path —
exactly when reliability matters most.

The envelope::

    {
      "ok": true,
      "summary": "OK -- 1 solid(s) ...",   # short, human/LLM readable
      "data": {...}                        # optional structured payload
    }

    {
      "ok": false,
      "summary": "ValueError: Invalid mode 'foo'.",
      "error": {
        "type": "ValueError",
        "message": "...",
        "line": 12,          # optional, user code line
        "snippet": "...",    # optional, the offending source line
        "hint": "..."        # optional, what to do about it
      }
    }

`summary` always exists and always reads well on its own, so a model that
ignores the structure still gets something actionable.
"""
from __future__ import annotations

import json
from typing import Any

SCHEMA_KEYS = frozenset({"ok", "summary", "data", "error"})
ERROR_KEYS = frozenset(
    {"type", "message", "line", "snippet", "hint", "context"}
)


def ok(summary: str, **data: Any) -> str:
    """A successful tool response."""
    payload: dict[str, Any] = {"ok": True, "summary": summary}
    if data:
        payload["data"] = data
    return json.dumps(payload, indent=2, default=str)


def ok_data(summary: str, data: dict[str, Any]) -> str:
    """A successful response whose payload is already a dict."""
    payload: dict[str, Any] = {"ok": True, "summary": summary}
    if data:
        payload["data"] = data
    return json.dumps(payload, indent=2, default=str)


def fail(
    error_type: str,
    message: str,
    *,
    line: int | None = None,
    snippet: str | None = None,
    hint: str | None = None,
    context: list[str] | None = None,
    **data: Any,
) -> str:
    """A failed tool response, in SPEC N3 shape: what, where, and a hint."""
    error: dict[str, Any] = {"type": error_type, "message": message}
    if line is not None:
        error["line"] = line
    if snippet:
        error["snippet"] = snippet
    if hint:
        error["hint"] = hint
    if context:
        error["context"] = context

    summary = f"{error_type}: {message}"
    if line is not None:
        summary += f" (line {line})"
    if hint:
        summary += f" Hint: {hint}"

    payload: dict[str, Any] = {"ok": False, "summary": summary, "error": error}
    if data:
        payload["data"] = data
    return json.dumps(payload, indent=2, default=str)


def validate(raw: str) -> dict[str, Any]:
    """Parse and check a response against the envelope. Raises on drift.

    Used by the conformance test so a new tool cannot quietly invent a
    fifth response shape.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Response is not JSON: {raw[:200]!r}"
        raise AssertionError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"Response is not an object: {type(payload).__name__}"
        raise AssertionError(msg)

    unknown = set(payload) - SCHEMA_KEYS
    if unknown:
        msg = f"Response has keys outside the envelope: {sorted(unknown)}"
        raise AssertionError(msg)

    if "ok" not in payload or not isinstance(payload["ok"], bool):
        msg = "Response is missing a boolean 'ok'"
        raise AssertionError(msg)

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        msg = "Response is missing a non-empty 'summary'"
        raise AssertionError(msg)

    if payload["ok"]:
        if "error" in payload:
            msg = "Successful response carries an 'error'"
            raise AssertionError(msg)
    else:
        error = payload.get("error")
        if not isinstance(error, dict):
            msg = "Failed response is missing an 'error' object"
            raise AssertionError(msg)
        missing = {"type", "message"} - set(error)
        if missing:
            msg = f"Error object is missing {sorted(missing)}"
            raise AssertionError(msg)
        extra = set(error) - ERROR_KEYS
        if extra:
            msg = f"Error object has unknown keys: {sorted(extra)}"
            raise AssertionError(msg)

    return payload
