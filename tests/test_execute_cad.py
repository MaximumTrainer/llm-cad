"""Tests for execute_cad, session management, and sandbox security.

Covers:
  - Basic CadQuery execution (box, bracket)
  - Append mode and code history
  - Session list / reset lifecycle
  - SPEC 9.3 malicious-code suite: network, file escape, fork bomb, infinite loop
  - SPEC N3 structured errors with line numbers and hints
"""
from __future__ import annotations

import json

import pytest

from cad_mcp import session
from cad_mcp.server import mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BRACKET_CODE = """\
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 3)
    .faces(">Z")
    .workplane()
    .rect(40, 20, forConstruction=True)
    .vertices()
    .hole(4.5)
    .faces("<Y")
    .workplane()
    .transformed(offset=(0, 1.5, 15))
    .rect(50, 30)
    .extrude(3)
)
"""

BOX_CODE = 'result = cq.Workplane("XY").box(10, 20, 30)'


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    """Reset all sessions before each test."""
    session.cleanup_all()


def _text(result: object) -> str:
    return result.content[0].text  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_box_executes() -> None:
    text = _text(await mcp.call_tool("execute_cad", {"code": BOX_CODE}))
    assert "OK" in text
    assert "1 solid" in text


@pytest.mark.anyio
async def test_bracket_executes() -> None:
    text = _text(await mcp.call_tool("execute_cad", {"code": BRACKET_CODE}))
    assert "OK" in text


@pytest.mark.anyio
async def test_missing_result_variable() -> None:
    code = 'x = cq.Workplane("XY").box(5, 5, 5)'
    text = _text(await mcp.call_tool("execute_cad", {"code": code}))
    assert "NameError" in text
    assert "result" in text.lower()


@pytest.mark.anyio
async def test_bad_result_type() -> None:
    code = "result = 42"
    text = _text(await mcp.call_tool("execute_cad", {"code": code}))
    assert "TypeError" in text


# ---------------------------------------------------------------------------
# Append mode + history
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_append_mode() -> None:
    await mcp.call_tool("execute_cad", {"code": BOX_CODE})

    append_code = (
        'result = result.faces(">Z").workplane().hole(3)'
    )
    text = _text(
        await mcp.call_tool(
            "execute_cad", {"code": append_code, "mode": "append"}
        )
    )
    assert "OK" in text


@pytest.mark.anyio
async def test_append_rollback_on_failure() -> None:
    await mcp.call_tool("execute_cad", {"code": BOX_CODE})

    bad = "result = result.fillet(999)"
    text = _text(
        await mcp.call_tool("execute_cad", {"code": bad, "mode": "append"})
    )
    assert "OK" not in text

    sess = session.get_or_create()
    part = sess.get_active_part()
    assert len(part.code_history) == 1, "Failed append should be rolled back"


@pytest.mark.anyio
async def test_invalid_mode() -> None:
    text = _text(
        await mcp.call_tool(
            "execute_cad", {"code": BOX_CODE, "mode": "invalid"}
        )
    )
    assert "ValueError" in text


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_session() -> None:
    await mcp.call_tool("execute_cad", {"code": BOX_CODE})
    text = _text(await mcp.call_tool("list_session", {}))
    data = json.loads(text)
    assert data["code_blocks"] == 1
    assert data["has_model"] is True
    assert data["current_bbox"] is not None


@pytest.mark.anyio
async def test_reset_session() -> None:
    await mcp.call_tool("execute_cad", {"code": BOX_CODE})
    await mcp.call_tool("reset_session", {})
    text = _text(await mcp.call_tool("list_session", {}))
    data = json.loads(text)
    assert data["code_blocks"] == 0
    assert data["has_model"] is False


# ---------------------------------------------------------------------------
# SPEC N3 — structured errors with line number + hint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_error_has_line_number() -> None:
    code = "x = 1\ny = 2\nresult = cq.Workplane('XY').box(10,10,10).fillet(999)"
    text = _text(await mcp.call_tool("execute_cad", {"code": code}))
    assert "line 3" in text


@pytest.mark.anyio
async def test_fillet_error_has_hint() -> None:
    code = "result = cq.Workplane('XY').box(10,10,10).edges().fillet(999)"
    text = _text(await mcp.call_tool("execute_cad", {"code": code}))
    assert "Hint" in text or "hint" in text
    assert "fillet" in text.lower() or "radius" in text.lower()


# ---------------------------------------------------------------------------
# SPEC 9.3 — Malicious-code suite
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Containment
#
# The SPEC 9.3 vectors live in tests/test_sandbox_containment.py, which
# asserts observable effects (no file written, no connection accepted, no
# surviving process) instead of the error wording. The weak versions that
# used to live here asserted `"OK" not in text` and passed while the
# sandbox was wide open.
# ---------------------------------------------------------------------------
