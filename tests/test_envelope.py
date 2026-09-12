"""Every tool speaks one response envelope (CAD-019 / #20).

Before this, the 14 tools returned at least four different shapes:
prose from `execute_cad` on success but JSON on argument errors, a bare
report dict from `validate_mesh` for one part and a wrapper for several,
`{"ok": ...}` from most, and raw strings from `ping`/`reset_session`.

The parametrised conformance test below is the part that matters: a new
tool cannot quietly invent a fifth shape.
"""
from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from mcp.types import TextContent

from cad_mcp.envelope import fail, ok, ok_data, validate
from cad_mcp.server import mcp

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)"


def _text(result: Any) -> str:
    for block in result.content:
        if isinstance(block, TextContent) or block.type == "text":
            return str(block.text)
    msg = f"no text block in {result.content}"
    raise AssertionError(msg)


# ------------------------------------------------------------------
# The helpers themselves
# ------------------------------------------------------------------


def test_ok_shape() -> None:
    payload = validate(ok("did the thing", count=3))
    assert payload["ok"] is True
    assert payload["summary"] == "did the thing"
    assert payload["data"] == {"count": 3}
    assert "error" not in payload


def test_ok_without_data_omits_the_key() -> None:
    payload = validate(ok("nothing to add"))
    assert "data" not in payload


def test_fail_shape_carries_what_where_and_hint() -> None:
    """SPEC N3: what failed, where, and a hint."""
    payload = validate(
        fail(
            "KernelError",
            "fillet radius 5 exceeds edge length 3.2",
            line=12,
            snippet=".fillet(5)",
            hint="Reduce the radius.",
        )
    )
    assert payload["ok"] is False
    error = payload["error"]
    assert error["type"] == "KernelError"
    assert error["line"] == 12
    assert error["snippet"] == ".fillet(5)"
    assert error["hint"] == "Reduce the radius."
    # The summary must stand alone for a model that ignores structure.
    assert "KernelError" in payload["summary"]
    assert "line 12" in payload["summary"]
    assert "Reduce the radius." in payload["summary"]


def test_fail_omits_absent_optional_fields() -> None:
    payload = validate(fail("ValueError", "bad input"))
    assert set(payload["error"]) == {"type", "message"}


def test_ok_data_passes_a_dict_through() -> None:
    payload = validate(ok_data("summary", {"a": 1, "b": [2]}))
    assert payload["data"] == {"a": 1, "b": [2]}


# ------------------------------------------------------------------
# The validator must actually reject drift
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        "[1, 2, 3]",
        '{"summary": "no ok key"}',
        '{"ok": "yes", "summary": "ok is not a bool"}',
        '{"ok": true}',
        '{"ok": true, "summary": "   "}',
        '{"ok": true, "summary": "s", "error": {"type": "x", "message": "y"}}',
        '{"ok": false, "summary": "s"}',
        '{"ok": false, "summary": "s", "error": {"type": "x"}}',
        '{"ok": false, "summary": "s", "error": {"type":"x","message":"y","oops":1}}',
        '{"ok": true, "summary": "s", "extra_key": 1}',
    ],
)
def test_validator_rejects_non_conforming_payloads(bad: str) -> None:
    with pytest.raises(AssertionError):
        validate(bad)


# ------------------------------------------------------------------
# Conformance across every registered tool
# ------------------------------------------------------------------

# Arguments that make each tool succeed against a session holding a box.
SUCCESS_ARGS: dict[str, dict[str, Any]] = {
    "ping": {},
    "execute_cad": {"code": BOX},
    "validate_mesh": {},
    "measure": {"what": "bbox"},
    "export_model": {"format": "stl", "filename": "envtest"},
    "list_session": {},
    "list_parts": {},
    "create_part": {"name": "lid"},
    "set_active_part": {"name": "main"},
    "position_part": {"name": "main", "translate": [0, 0, 1]},
    "delete_part": {"name": "lid"},
    "reset_session": {},
}

# Arguments that make each tool fail, to exercise the error envelope.
FAILURE_ARGS: dict[str, dict[str, Any]] = {
    "execute_cad": {"code": BOX, "mode": "sideways"},
    "validate_mesh": {"part": "nope"},
    "measure": {"what": "colour"},
    "export_model": {"format": "dwg"},
    "create_part": {"name": "Not Valid"},
    "set_active_part": {"name": "nope"},
    "position_part": {"name": "nope"},
    "delete_part": {"name": "nope"},
    "gen_ai_mesh": {"prompt": "a pawn"},  # no MESHY_API_KEY in tests
}


def _tool_names() -> list[str]:
    return sorted(t.name for t in anyio.run(mcp.list_tools))


@pytest.mark.parametrize("name", sorted(SUCCESS_ARGS))
def test_success_responses_conform(name: str) -> None:
    async def scenario() -> str:
        if name not in ("execute_cad", "ping", "reset_session"):
            await mcp.call_tool("execute_cad", {"code": BOX})
        if name == "delete_part":
            await mcp.call_tool("create_part", {"name": "lid"})
        result = await mcp.call_tool(name, SUCCESS_ARGS[name])
        return _text(result)

    payload = validate(anyio.run(scenario))
    assert payload["ok"] is True, f"{name} did not succeed: {payload}"


@pytest.mark.parametrize("name", sorted(FAILURE_ARGS))
def test_failure_responses_conform(name: str) -> None:
    async def scenario() -> str:
        result = await mcp.call_tool(name, FAILURE_ARGS[name])
        return _text(result)

    payload = validate(anyio.run(scenario))
    assert payload["ok"] is False, (
        f"{name} was expected to fail but returned {payload}"
    )
    assert payload["error"]["type"], f"{name} gave no error type"


def test_render_views_error_path_conforms() -> None:
    """`render_views` returns content blocks; its text must still conform."""

    async def scenario() -> str:
        result = await mcp.call_tool("render_views", {})
        return _text(result)

    payload = validate(anyio.run(scenario))
    assert payload["ok"] is False
    assert payload["error"]["type"] == "NoModel"


def test_render_views_success_returns_an_image_and_a_summary() -> None:
    async def scenario() -> Any:
        await mcp.call_tool("execute_cad", {"code": BOX})
        return await mcp.call_tool("render_views", {})

    result = anyio.run(scenario)
    types = [c.type for c in result.content]
    assert "image" in types, f"no ImageContent returned: {types}"
    assert "text" in types


def test_every_tool_is_covered_by_this_file() -> None:
    """A new tool must be added here, not silently skipped."""
    covered = set(SUCCESS_ARGS) | set(FAILURE_ARGS) | {"render_views"}
    missing = set(_tool_names()) - covered
    assert not missing, (
        f"Tools with no envelope conformance coverage: {sorted(missing)}. "
        f"Add them to SUCCESS_ARGS/FAILURE_ARGS in tests/test_envelope.py."
    )


def test_execute_cad_error_reports_line_and_hint() -> None:
    """The envelope must not have lost SPEC N3's detail."""

    async def scenario() -> str:
        bad = (
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(40, 40, 10)"
            ".edges('|Z').fillet(20)"
        )
        result = await mcp.call_tool("execute_cad", {"code": bad})
        return _text(result)

    payload = validate(anyio.run(scenario))
    assert payload["ok"] is False
    error = payload["error"]
    assert error.get("line"), f"no line number: {error}"
    assert error.get("hint"), f"no hint: {error}"


def test_execute_cad_success_summary_is_still_readable() -> None:
    """The old prose summary is preserved inside the envelope."""

    async def scenario() -> str:
        result = await mcp.call_tool("execute_cad", {"code": BOX})
        return _text(result)

    payload = validate(anyio.run(scenario))
    assert payload["summary"].startswith("OK")
    assert "Bounding box" in payload["summary"]
    assert payload["data"]["solid_count"] == 1


def test_validate_mesh_shape_is_the_same_for_one_and_many_parts() -> None:
    """The single-part path used to return a bare report with no `ok`."""

    async def one() -> str:
        await mcp.call_tool("execute_cad", {"code": BOX})
        return _text(await mcp.call_tool("validate_mesh", {}))

    async def many() -> str:
        await mcp.call_tool("execute_cad", {"code": BOX})
        await mcp.call_tool("create_part", {"name": "second"})
        await mcp.call_tool("execute_cad", {"code": BOX})
        return _text(await mcp.call_tool("validate_mesh", {}))

    single = validate(anyio.run(one))
    multi = validate(anyio.run(many))

    assert set(single) == set(multi), (
        f"single-part keys {sorted(single)} != multi-part {sorted(multi)}"
    )
    assert set(single["data"]) == set(multi["data"])
    assert len(json.loads(json.dumps(multi["data"]))["parts"]) == 2
