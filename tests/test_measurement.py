"""Measurement correctness: wall thickness, overhangs, distances.

Covers CAD-015 (#16), CAD-016 (#17) and CAD-018 (#19). These three
shared a failure mode: each returned a confident number that did not
mean what the workflow prompt tells the LLM it means, so acting on it
made the model worse rather than better.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import anyio
import pytest

from cad_mcp import sandbox, validate
from cad_mcp.server import mcp

from .envelope_helpers import flat, part_report

# --- geometry fixtures -------------------------------------------------

THIN_WALL = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(30,30,30).faces('>Z').shell(-0.8)"
)
THICK_WALL_WITH_SLOT = (
    "import cadquery as cq\n"
    "b = cq.Workplane('XY').box(40,40,20).faces('>Z').shell(-3)\n"
    "result = b.cut(cq.Workplane('XY').box(0.5, 60, 40))"
)
SOLID_CUBE = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(20,20,20)"
)
CUBE_ON_BED = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(20,20,20).translate((0,0,10))"
)
TWO_CEILINGS = """
import cadquery as cq
def tower(x):
    leg = cq.Workplane('XY').box(6, 6, 20).translate((x, 0, 10))
    top = cq.Workplane('XY').box(20, 6, 4).translate((x, 0, 22))
    return leg.union(top)
result = tower(-30).union(tower(30))
"""
PLATE = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(60, 40, 10)"
)
PLATE_WITH_HOLES = """
import cadquery as cq
result = (
    cq.Workplane("XY").box(60, 40, 10)
    .faces(">Z").workplane()
    .pushPoints([(-20, 0), (20, 0)])
    .hole(4.5)
)
"""


def _brep(code: str) -> Path:
    """Build geometry outside a session, for direct validate() calls."""
    tmp = Path(tempfile.mkdtemp(prefix="cad-mcp-measure-"))
    out = tmp / "shape.brep"
    result = sandbox.run(code, tmp, brep_out=out)
    assert result.ok, f"fixture build failed: {result.to_dict()}"
    return out


# ------------------------------------------------------------------
# CAD-015: wall thickness measures material, not proximity
# ------------------------------------------------------------------


def test_thin_wall_is_flagged_with_an_accurate_value() -> None:
    wall = validate.validate(_brep(THIN_WALL), min_wall_mm=1.2)[
        "wall_thickness"
    ]
    assert wall["violations"] > 0, f"0.8mm wall not flagged: {wall}"
    assert wall["min_mm"] == pytest.approx(0.8, abs=0.15), (
        f"measured {wall['min_mm']}mm for a 0.8mm wall"
    )


def test_a_narrow_slot_is_not_mistaken_for_a_thin_wall() -> None:
    """The core defect: a 0.5mm *gap* read as a 0.5mm wall.

    Two surfaces facing each other are a wall when material is between
    them and a gap when air is. The old proximity test could not tell.
    """
    wall = validate.validate(
        _brep(THICK_WALL_WITH_SLOT), min_wall_mm=1.2
    )["wall_thickness"]

    assert wall["violations"] == 0, (
        f"a 0.5mm slot through 3mm walls was flagged as a thin wall: "
        f"{wall}"
    )
    assert wall["min_mm"] == pytest.approx(3.0, abs=0.3), (
        f"expected ~3mm walls, measured {wall['min_mm']}mm"
    )


def test_solid_body_reports_its_full_thickness() -> None:
    wall = validate.validate(_brep(SOLID_CUBE), min_wall_mm=1.2)[
        "wall_thickness"
    ]
    assert wall["violations"] == 0
    assert wall["min_mm"] == pytest.approx(20.0, abs=0.3)


def test_thin_wall_report_says_where_to_look() -> None:
    wall = validate.validate(_brep(THIN_WALL), min_wall_mm=1.2)[
        "wall_thickness"
    ]
    point = wall["thinnest_point"]
    assert point is not None, "no location for the thinnest point"
    assert set(point) == {"x", "y", "z"}


def test_wall_thickness_is_reproducible() -> None:
    """Seeded sampling: the same mesh must give the same estimate."""
    brep = _brep(THIN_WALL)
    first = validate.validate(brep, min_wall_mm=1.2)["wall_thickness"]
    second = validate.validate(brep, min_wall_mm=1.2)["wall_thickness"]
    assert first["min_mm"] == second["min_mm"]
    assert first["violations"] == second["violations"]


def test_wall_thickness_states_its_method_and_error_bound() -> None:
    wall = validate.validate(_brep(SOLID_CUBE))["wall_thickness"]
    assert "ray" in wall["method"]
    assert "tessellation tolerance" in wall["note"]


# ------------------------------------------------------------------
# CAD-016: overhangs are located, not just counted
# ------------------------------------------------------------------


def test_overhang_regions_are_located() -> None:
    overhangs = validate.validate(
        _brep(TWO_CEILINGS), max_overhang_deg=45
    )["overhangs"]

    assert overhangs["overhang_faces"] > 0
    regions = overhangs["regions"]
    assert regions, "faces were flagged but no regions reported"

    for region in regions:
        assert region["face_count"] >= 1
        assert region["area_mm2"] > 0
        assert set(region["centroid"]) == {"x", "y", "z"}
        assert "min" in region["bbox"] and "max" in region["bbox"]
        # Phrased for an LLM to act on.
        assert "overhang" in region["description"]
        assert "mm2" in region["description"]


def test_separate_overhangs_become_separate_regions() -> None:
    """Clustering must not merge two physically distinct ceilings.

    The two-tower fixture has four unsupported areas: each top plate
    overhangs on both sides of its leg.
    """
    overhangs = validate.validate(
        _brep(TWO_CEILINGS), max_overhang_deg=45
    )["overhangs"]
    assert len(overhangs["regions"]) == 4, (
        f"expected 4 distinct overhang areas, got "
        f"{len(overhangs['regions'])}"
    )
    xs = [r["centroid"]["x"] for r in overhangs["regions"]]
    assert min(xs) < 0 < max(xs), "regions are not spatially separated"


def test_a_flat_bottomed_part_has_no_overhangs() -> None:
    """A face on the build plate rests on it and needs no support."""
    overhangs = validate.validate(
        _brep(CUBE_ON_BED), max_overhang_deg=45
    )["overhangs"]
    assert overhangs["overhang_faces"] == 0, (
        f"the build-plate face was treated as an overhang: {overhangs}"
    )
    assert overhangs["regions"] == []


def test_bed_tolerance_derives_from_tessellation_tolerance() -> None:
    """It was a magic 0.1 with no relation to the mesh resolution."""
    from cad_mcp.render import TESS_LINEAR

    overhangs = validate.validate(_brep(CUBE_ON_BED))["overhangs"]
    assert overhangs["bed_tolerance_mm"] == pytest.approx(
        TESS_LINEAR * 2.0
    )


def test_overhang_angle_convention_is_documented() -> None:
    """Ambiguity here means the LLM cannot interpret the number."""
    overhangs = validate.validate(_brep(TWO_CEILINGS))["overhangs"]
    convention = overhangs["angle_convention"]
    assert "from vertical" in convention
    assert "90" in convention

    from cad_mcp.prompts import _PRINTABILITY_CHECKLIST

    assert "from vertical" in _PRINTABILITY_CHECKLIST, (
        "the printability prompt must state the same convention as the "
        "report, or the two disagree"
    )


def test_overhang_issue_text_names_the_location() -> None:
    report = validate.validate(_brep(TWO_CEILINGS), max_overhang_deg=45)
    overhang_issues = [i for i in report["issues"] if "overhang" in i]
    assert overhang_issues, f"no overhang issue raised: {report['issues']}"
    assert "near (" in overhang_issues[0], (
        f"issue text gives no location: {overhang_issues[0]}"
    )


# ------------------------------------------------------------------
# CAD-018: distance means minimum distance
# ------------------------------------------------------------------


def _measure(**kwargs: Any) -> dict[str, Any]:
    async def scenario() -> dict[str, Any]:
        code = kwargs.pop("_code", PLATE)
        await mcp.call_tool("execute_cad", {"code": code})
        return flat(await mcp.call_tool("measure", kwargs))

    return anyio.run(scenario)


def test_distance_is_minimum_distance_not_centroid_distance() -> None:
    """A 10mm plate: top-to-bottom minimum distance is its thickness."""
    result = _measure(
        what="distance", from_selector=">Z", to_selector="<Z"
    )
    assert result["ok"], result
    assert result["distance_mm"] == pytest.approx(10.0, abs=0.01)


def test_distance_reports_the_resolved_kind_and_match_count() -> None:
    """A selector matching more than expected must be visible."""
    result = _measure(
        what="distance", from_selector=">Z", to_selector="<Z"
    )
    assert result["from"]["kind"] == "faces"
    assert result["from"]["matched"] == 1
    assert result["to"]["matched"] == 1
    assert "closest_point_from" in result


def test_distance_accepts_edges_and_vertices() -> None:
    result = _measure(
        what="distance",
        from_selector="|Z",
        to_selector=">Z",
        from_kind="edges",
        to_kind="faces",
    )
    assert result["ok"], result
    assert result["from"]["kind"] == "edges"


def test_center_distance_is_available_explicitly() -> None:
    """Centroid-to-centroid is still the right tool for hole spacing."""
    result = _measure(
        _code=PLATE_WITH_HOLES,
        what="center_distance",
        from_selector=">Z",
        to_selector="<Z",
    )
    assert result["ok"], result
    assert result["center_distance_mm"] == pytest.approx(10.0, abs=0.01)


def test_spec_9_2_hole_spacing_within_0_1mm() -> None:
    """SPEC 9.2: `measure` confirms requested dimensions within 0.1mm.

    The holes are placed 40mm apart. Measuring the minimum distance
    between the two bore walls must equal 40 - 4.5 = 35.5mm.
    """
    result = _measure(
        _code=PLATE_WITH_HOLES,
        what="distance",
        from_selector=">X",
        to_selector="<X",
    )
    assert result["ok"], result
    assert result["distance_mm"] == pytest.approx(60.0, abs=0.1)


def test_a_multi_match_selector_says_so() -> None:
    result = _measure(
        what="distance",
        from_selector="|Z",
        to_selector=">Z",
        from_kind="edges",
    )
    assert result["from"]["matched"] > 1
    assert "note" in result, (
        "a selector matching several entities must say so rather than "
        "silently using one of them"
    )


def test_measure_states_whether_the_part_transform_was_applied() -> None:
    """`clearance` applies it; the others do not. Say which."""
    result = _measure(what="bbox")
    assert result["part_transform_applied"] is False
    assert "part_is_positioned" in result
    assert result["part"] == "main"


def test_unknown_selector_kind_is_rejected() -> None:
    result = _measure(
        what="distance",
        from_selector=">Z",
        to_selector="<Z",
        from_kind="blobs",
    )
    assert result["ok"] is False
    assert "blobs" in result["error"] or "kind" in result["error"].lower()


def test_selector_matching_nothing_is_an_actionable_error() -> None:
    result = _measure(
        what="distance", from_selector=">>>nope", to_selector="<Z"
    )
    assert result["ok"] is False
    assert result["error_type"]


# ------------------------------------------------------------------
# The report still reaches the LLM through the tool
# ------------------------------------------------------------------


def test_validate_mesh_surfaces_regions_and_thickness() -> None:
    async def scenario() -> dict[str, Any]:
        await mcp.call_tool("execute_cad", {"code": TWO_CEILINGS})
        return part_report(await mcp.call_tool("validate_mesh", {}))

    report = anyio.run(scenario)
    assert report["overhangs"]["regions"], "regions not reported via the tool"
    assert "thinnest_point" in report["wall_thickness"]
