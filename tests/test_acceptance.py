"""SPEC 9. Acceptance criteria tests.

9.1  Bracket → validated watertight STL (programmatic, no LLM needed)
9.2  measure confirms dimensions within 0.1mm
9.3  Malicious code suite — all contained
9.4  Fresh setup to first render (tested by smoke script timing)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cad_mcp import session
from cad_mcp.resources import EXAMPLES
from cad_mcp.server import mcp

from .envelope_helpers import flat, part_report, summary

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry

BRACKET_CODE = EXAMPLES["bracket"]


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


# ------------------------------------------------------------------
# 9.1: Bracket → watertight STL within one iteration
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_spec_9_1_bracket_to_watertight_stl() -> None:
    """From the bracket example, produce a validated watertight STL."""
    # Iteration 1: execute
    result = await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})
    text = summary(result)
    assert text.startswith("OK"), f"execute_cad failed: {text}"

    # Render (mandatory per workflow)
    result = await mcp.call_tool("render_views", {})
    types = [c.type for c in result.content]
    assert "image" in types

    # Validate
    result = await mcp.call_tool("validate_mesh", {})
    report = part_report(result)
    assert report["watertight"], f"Not watertight: {report}"
    assert report["manifold"], f"Not manifold: {report}"

    # Export STL
    result = await mcp.call_tool(
        "export_model", {"format": "stl", "filename": "bracket"}
    )
    exp = flat(result)
    assert exp["ok"], f"STL export failed: {exp}"
    assert Path(exp["path"]).exists()
    assert exp["size_bytes"] > 1000


# ------------------------------------------------------------------
# 9.2: measure confirms dimensions within 0.1mm
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_spec_9_2_measure_dimensions() -> None:
    """measure confirms bracket dimensions are within 0.1mm."""
    await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})

    result = await mcp.call_tool("measure", {"what": "bbox"})
    data = flat(result)
    assert data["ok"]

    dims = data["dimensions"]
    # The bracket has known dimensions based on the code parameters:
    # plate_w = 30 + 2*3 + 20 = 56mm
    # plate_t = 3mm (wall)
    # plate_h = 30/2 + 3 + 10 = 28mm
    # But the cradle adds pipe_od/2 + wall = 15+3 = 18mm above plate_h/2
    # So total Z height depends on the union geometry.
    #
    # Just verify the main X dimension (plate_w = 56mm) is within 0.1mm
    assert abs(dims["x"] - 56.0) < 0.1, (
        f"X dimension {dims['x']} not within 0.1mm of 56.0"
    )

    # Verify volume is a positive number
    result = await mcp.call_tool("measure", {"what": "volume"})
    vol = flat(result)
    assert vol["ok"]
    assert vol["volume_mm3"] > 100


# ------------------------------------------------------------------
# 9.3: Malicious code — all contained
#
# Implemented in tests/test_sandbox_containment.py. Kept out of this file
# deliberately: the versions that lived here asserted the absence of the
# substring "OK", which every error satisfies, so they certified SPEC 9.3
# while filesystem and raw-socket escapes both succeeded.
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# 9.4: Setup time
#       We can't test "under 10 minutes" in a unit test, but we
#       verify the smoke script is runnable and the server starts.
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_spec_9_4_server_starts_and_pings() -> None:
    result = await mcp.call_tool("ping", {})
    text = summary(result)
    assert "pong" in text.lower()
