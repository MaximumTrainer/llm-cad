"""Read tool responses in tests without restating the envelope everywhere.

`tests/test_envelope.py` is what pins the wire format down strictly. These
helpers exist so the *behavioural* tests can stay about behaviour: they
validate the envelope on the way through (so a malformed response still
fails loudly) and then hand back a flat dict, which is what those
assertions were always really asking for.
"""
from __future__ import annotations

from typing import Any

from cad_mcp.envelope import validate


def text_of(result: Any) -> str:
    """The first text block of a tool result."""
    for block in result.content:
        if getattr(block, "type", None) == "text":
            return str(block.text)
    msg = f"no text block in tool result: {result.content}"
    raise AssertionError(msg)


def image_count(result: Any) -> int:
    return sum(1 for b in result.content if getattr(b, "type", None) == "image")


def envelope(result: Any) -> dict[str, Any]:
    """The validated envelope. Raises if the tool drifted off-format."""
    return validate(text_of(result))


def summary(result: Any) -> str:
    return str(envelope(result)["summary"])


def succeeded(result: Any) -> bool:
    return bool(envelope(result)["ok"])


def flat(result: Any) -> dict[str, Any]:
    """Envelope flattened to one level: `ok`, `summary`, and the payload.

    On failure, `error` is the message and the error's own fields (`type`,
    `line`, `snippet`, `hint`) are lifted alongside it.
    """
    payload = envelope(result)
    out: dict[str, Any] = {
        "ok": payload["ok"],
        "summary": payload["summary"],
    }
    out.update(payload.get("data") or {})

    err = payload.get("error")
    if err:
        out["error"] = err.get("message", "")
        out["error_type"] = err.get("type", "")
        for key in ("line", "snippet", "hint"):
            if key in err:
                out[key] = err[key]
    return out


def part_report(result: Any, part: str | None = None) -> dict[str, Any]:
    """One part's validation report out of a `validate_mesh` response.

    `validate_mesh` now returns the same shape for one part and for many
    (it used to return a bare report for one), so a per-part assertion
    has to say which part it means.
    """
    payload = envelope(result)
    assert payload["ok"], f"validate_mesh failed: {payload}"
    parts = (payload.get("data") or {}).get("parts") or []
    if not parts:
        msg = f"no per-part reports in {payload}"
        raise AssertionError(msg)
    if part is None:
        return dict(parts[0])
    for entry in parts:
        if entry.get("part") == part:
            return dict(entry)
    msg = f"part {part!r} not in report; have {[p.get('part') for p in parts]}"
    raise AssertionError(msg)
