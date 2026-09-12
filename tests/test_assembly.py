"""Tests for SPEC 10.3 — Assembly support.

Gate: create box + lid, position lid above box, render shows both
with different colors, export produces 3 STLs, validate flags
interference when overlapping, clearance returns 0 when touching
and >0 when separated; all v1 tests still pass.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cad_mcp import session
from cad_mcp.server import mcp

from .envelope_helpers import flat, part_report, summary

BOX_CODE = """\
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 10)
"""

LID_CODE = """\
import cadquery as cq
result = cq.Workplane("XY").box(22, 22, 3)
"""


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


def _data(result: Any) -> dict[str, Any]:
    """The tool's payload, flattened. Validates the envelope on the way."""
    return flat(result)


# ------------------------------------------------------------------
# Part CRUD
# ------------------------------------------------------------------


class TestPartCRUD:
    @pytest.mark.anyio
    async def test_create_part(self) -> None:
        """A1: create_part creates a new part and sets it active."""
        data = _data(
            await mcp.call_tool("create_part", {"name": "lid"})
        )
        assert data["ok"] is True
        assert data["created"] == "lid"
        assert data["active_part"] == "lid"
        assert len(data["parts"]) == 2

    @pytest.mark.anyio
    async def test_create_part_auto_color(self) -> None:
        """Auto-assigned color differs from the default 'main' color."""
        data = _data(
            await mcp.call_tool("create_part", {"name": "lid"})
        )
        assert data["color"] != "steel"

    @pytest.mark.anyio
    async def test_create_part_invalid_name(self) -> None:
        """A9: invalid names are rejected."""
        data = _data(
            await mcp.call_tool("create_part", {"name": "Bad-Name!"})
        )
        assert data["ok"] is False

    @pytest.mark.anyio
    async def test_create_part_duplicate(self) -> None:
        """Duplicate names are rejected."""
        await mcp.call_tool("create_part", {"name": "lid"})
        data = _data(
            await mcp.call_tool("create_part", {"name": "lid"})
        )
        assert data["ok"] is False

    @pytest.mark.anyio
    async def test_create_part_max_16(self) -> None:
        """A10: max 16 parts."""
        for i in range(15):
            await mcp.call_tool(
                "create_part", {"name": f"p{i:02d}"}
            )
        data = _data(
            await mcp.call_tool("create_part", {"name": "p15"})
        )
        assert data["ok"] is False
        assert "16" in data["error"]

    @pytest.mark.anyio
    async def test_set_active_part(self) -> None:
        """A2: set_active_part switches the active part."""
        await mcp.call_tool("create_part", {"name": "lid"})
        data = _data(
            await mcp.call_tool("set_active_part", {"name": "main"})
        )
        assert data["ok"] is True
        assert data["active_part"] == "main"

    @pytest.mark.anyio
    async def test_set_active_part_not_found(self) -> None:
        data = _data(
            await mcp.call_tool("set_active_part", {"name": "nope"})
        )
        assert data["ok"] is False

    @pytest.mark.anyio
    async def test_position_part(self) -> None:
        """A3: position_part stores translate/rotate on the part."""
        data = _data(
            await mcp.call_tool(
                "position_part",
                {"name": "main", "translate": [0, 0, 10]},
            )
        )
        assert data["ok"] is True
        assert data["translate"] == [0, 0, 10]

    @pytest.mark.anyio
    async def test_position_part_not_found(self) -> None:
        data = _data(
            await mcp.call_tool(
                "position_part", {"name": "nope", "translate": [0, 0, 0]}
            )
        )
        assert data["ok"] is False

    @pytest.mark.anyio
    async def test_list_parts(self) -> None:
        """list_parts returns all parts."""
        await mcp.call_tool("create_part", {"name": "lid"})
        data = _data(await mcp.call_tool("list_parts", {}))
        assert data["ok"] is True
        assert data["part_count"] == 2
        names = [p["name"] for p in data["parts"]]
        assert "main" in names
        assert "lid" in names

    @pytest.mark.anyio
    async def test_delete_part(self) -> None:
        """delete_part removes a part and switches active if needed."""
        await mcp.call_tool("create_part", {"name": "lid"})
        data = _data(
            await mcp.call_tool("delete_part", {"name": "lid"})
        )
        assert data["ok"] is True
        assert data["deleted"] == "lid"
        assert len(data["parts"]) == 1

    @pytest.mark.anyio
    async def test_delete_last_part_fails(self) -> None:
        data = _data(
            await mcp.call_tool("delete_part", {"name": "main"})
        )
        assert data["ok"] is False


# ------------------------------------------------------------------
# Execute targets active part
# ------------------------------------------------------------------


class TestExecuteActivePart:
    @pytest.mark.anyio
    async def test_execute_writes_to_active_part(self) -> None:
        """execute_cad writes geometry to the active part's brep."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})

        sess = session.get_or_create()
        assert sess.brep_path("main").exists()

        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        assert sess.brep_path("lid").exists()
        assert sess.brep_path("main").exists()

    @pytest.mark.anyio
    async def test_active_part_code_history_independent(self) -> None:
        """Each part has its own code history."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        sess = session.get_or_create()
        assert len(sess.parts["main"].code_history) == 1
        assert len(sess.parts["lid"].code_history) == 1
        assert "box(20" in sess.parts["main"].accumulated_code()
        assert "box(22" in sess.parts["lid"].accumulated_code()


# ------------------------------------------------------------------
# Multi-part render
# ------------------------------------------------------------------


class TestMultiPartRender:
    @pytest.mark.anyio
    async def test_render_shows_all_parts(self) -> None:
        """A4: render_views composes all parts into one scene."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        await mcp.call_tool(
            "position_part",
            {"name": "lid", "translate": [0, 0, 10]},
        )

        result = await mcp.call_tool("render_views", {})
        types = [c.type for c in result.content]
        assert "image" in types
        text = result.content[1].text  # type: ignore[union-attr]
        assert "main" in text
        assert "lid" in text

    @pytest.mark.anyio
    async def test_render_parts_filter(self) -> None:
        """render_views with parts filter renders only specified parts."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        result = await mcp.call_tool(
            "render_views", {"parts": ["lid"]}
        )
        types = [c.type for c in result.content]
        assert "image" in types


# ------------------------------------------------------------------
# Multi-part export
# ------------------------------------------------------------------


class TestMultiPartExport:
    @pytest.mark.anyio
    async def test_export_produces_3_stls(self) -> None:
        """A5: export with 2 parts produces part + part + assembly."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        result = await mcp.call_tool(
            "export_model",
            {"format": "stl", "filename": "box_lid"},
        )
        data = _data(result)
        assert data["ok"] is True
        assert data["total_files"] == 3

        paths = [
            f["path"] for f in data["files"] if "path" in f
        ]
        assert any("box_lid_main" in p for p in paths)
        assert any("box_lid_lid" in p for p in paths)
        assert any("box_lid_assembly" in p for p in paths)

        for p in paths:
            assert Path(p).exists()

    @pytest.mark.anyio
    async def test_export_active_only(self) -> None:
        """parts='active' exports only the active part."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        result = await mcp.call_tool(
            "export_model",
            {"format": "stl", "filename": "just_lid", "parts": "active"},
        )
        data = _data(result)
        assert data["ok"] is True
        assert Path(data["path"]).exists()

    @pytest.mark.anyio
    async def test_single_part_export_unchanged(self) -> None:
        """A8: single-part session exports like v1 (no _assembly suffix)."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})

        result = await mcp.call_tool(
            "export_model",
            {"format": "stl", "filename": "box"},
        )
        data = _data(result)
        assert data["ok"] is True
        assert "box.stl" in data["filename"]

    @pytest.mark.anyio
    async def test_step_assembly_export(self) -> None:
        """A5: STEP assembly export writes a single file."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        result = await mcp.call_tool(
            "export_model",
            {"format": "step", "filename": "assembly"},
        )
        data = _data(result)
        assert data["ok"] is True
        paths = [f["path"] for f in data["files"] if "path" in f]
        assert any("assembly_assembly.step" in p for p in paths)


# ------------------------------------------------------------------
# Interference detection
# ------------------------------------------------------------------


class TestInterference:
    @pytest.mark.anyio
    async def test_interference_when_overlapping(self) -> None:
        """A6: validate flags interference when parts overlap."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        # lid at origin overlaps box at origin
        result = await mcp.call_tool("validate_mesh", {})
        data = _data(result)
        assert any("interference" in str(i).lower() for i in data.get("issues", []))

    @pytest.mark.anyio
    async def test_no_interference_when_separated(self) -> None:
        """A6: no interference when parts are separated."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        await mcp.call_tool(
            "position_part",
            {"name": "lid", "translate": [0, 0, 20]},
        )

        result = await mcp.call_tool("validate_mesh", {})
        data = _data(result)
        interference_issues = [
            i for i in data.get("issues", [])
            if "interference" in str(i).lower()
        ]
        assert len(interference_issues) == 0


# ------------------------------------------------------------------
# Clearance measurement
# ------------------------------------------------------------------


class TestClearance:
    @pytest.mark.anyio
    async def test_clearance_separated(self) -> None:
        """A7: clearance > 0 when parts are separated."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        await mcp.call_tool(
            "position_part",
            {"name": "lid", "translate": [0, 0, 20]},
        )

        result = await mcp.call_tool(
            "measure",
            {"what": "clearance", "parts": ["main", "lid"]},
        )
        data = _data(result)
        assert data["ok"] is True
        assert data["clearance_mm"] > 0

    @pytest.mark.anyio
    async def test_clearance_touching(self) -> None:
        """A7: clearance ~0 when parts are touching."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        # box is 20x20x10 centered at origin -> top face at z=5
        # lid is 22x22x3 centered at origin -> bottom face at z=-1.5
        # To make them touch: move lid up by 5 + 1.5 = 6.5
        await mcp.call_tool(
            "position_part",
            {"name": "lid", "translate": [0, 0, 6.5]},
        )

        result = await mcp.call_tool(
            "measure",
            {"what": "clearance", "parts": ["main", "lid"]},
        )
        data = _data(result)
        assert data["ok"] is True
        assert data["clearance_mm"] < 0.1

    @pytest.mark.anyio
    async def test_clearance_requires_two_parts(self) -> None:
        data = _data(
            await mcp.call_tool(
                "measure", {"what": "clearance", "parts": ["main"]}
            )
        )
        assert data["ok"] is False


# ------------------------------------------------------------------
# Backward compatibility
# ------------------------------------------------------------------


class TestBackwardCompat:
    @pytest.mark.anyio
    async def test_single_part_session_works_like_v1(self) -> None:
        """A8: session without create_part behaves as v1."""
        result = await mcp.call_tool(
            "execute_cad", {"code": BOX_CODE}
        )
        text = summary(result)
        assert "OK" in text

        sess = session.get_or_create()
        assert "main" in sess.parts
        assert sess.active_part == "main"

    @pytest.mark.anyio
    async def test_list_session_includes_parts(self) -> None:
        """list_session shows parts info."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})

        result = await mcp.call_tool("list_session", {})
        data = _data(result)
        assert "parts" in data
        assert len(data["parts"]) == 2

    @pytest.mark.anyio
    async def test_reset_clears_all_parts(self) -> None:
        """reset_session clears all parts."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("reset_session", {})

        sess = session.get_or_create()
        assert len(sess.parts) == 1
        assert "main" in sess.parts


# ------------------------------------------------------------------
# Part validation edge cases
# ------------------------------------------------------------------


class TestPartValidation:
    @pytest.mark.anyio
    async def test_validate_single_part(self) -> None:
        """validate_mesh(part='main') validates only that part."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})

        result = await mcp.call_tool(
            "validate_mesh", {"part": "main"}
        )
        data = _data(result)
        # One part and many now return the same shape (CAD-019), so the
        # per-part report lives under data.parts rather than at the top.
        assert [p["part"] for p in data["parts"]] == ["main"]
        assert part_report(result, "main")["watertight"] is True

    @pytest.mark.anyio
    async def test_position_then_render(self) -> None:
        """Positioning a part affects the rendered scene."""
        await mcp.call_tool("execute_cad", {"code": BOX_CODE})
        await mcp.call_tool("create_part", {"name": "lid"})
        await mcp.call_tool("execute_cad", {"code": LID_CODE})
        await mcp.call_tool(
            "position_part",
            {"name": "lid", "translate": [0, 0, 10], "rotate": [0, 0, 45]},
        )

        result = await mcp.call_tool("render_views", {})
        types = [c.type for c in result.content]
        assert "image" in types
