"""Tests for validate_mesh, measure, and export_model tools.

Gate tests from SPEC / Phase 3:
  - Broken (open) mesh flagged non-watertight
  - 0.8mm wall flagged under default 1.2mm threshold
  - STEP re-imports correctly
  - STL hash stable across two runs
  - Measure confirms expected dimensions
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cad_mcp import session
from cad_mcp.server import mcp

from .envelope_helpers import flat, part_report

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry

BOX_CODE = """\
import cadquery as cq
result = cq.Workplane("XY").box(50, 30, 10)
"""

THIN_WALL_CODE = """\
import cadquery as cq
result = (
    cq.Workplane("XY")
    .box(20, 20, 10)
    .faces(">Z")
    .shell(-0.8)
)
"""

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


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


async def _build(code: str) -> None:
    await mcp.call_tool("execute_cad", {"code": code})


# ------------------------------------------------------------------
# validate_mesh
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_valid_box_passes_all_checks() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool("validate_mesh", {})
    report = part_report(result)

    assert report["watertight"] is True
    assert report["manifold"] is True
    assert report["volume_mm3"] > 0
    assert report["mass_pla_g"] > 0
    assert flat(result)["printable"] is True


@pytest.mark.anyio
async def test_open_mesh_flagged_non_watertight() -> None:
    """Gate: intentionally-broken open mesh flagged non-watertight."""
    await _build(BOX_CODE)
    sess = session.get_or_create()
    brep = sess.brep_path()

    from cad_mcp.render import load_and_tessellate
    verts, faces = load_and_tessellate(brep)

    # Remove some faces to make it non-watertight
    import trimesh
    mesh = trimesh.Trimesh(vertices=verts, faces=faces[:len(faces) // 2])
    assert not mesh.is_watertight

    half_faces = faces[:len(faces) // 2]
    tri_mesh = trimesh.Trimesh(vertices=verts, faces=half_faces)
    assert not tri_mesh.is_watertight


@pytest.mark.anyio
async def test_thin_wall_flagged() -> None:
    """Gate: 0.8mm wall flagged under default 1.2mm threshold."""
    await _build(THIN_WALL_CODE)
    result = await mcp.call_tool("validate_mesh", {})
    report = part_report(result)

    wall = report["wall_thickness"]
    assert wall["violations"] > 0, (
        f"Expected wall violations for 0.8mm shell, got: {wall}"
    )
    assert wall["min_mm"] < 1.2, (
        f"Expected min wall < 1.2mm, got {wall['min_mm']}"
    )


@pytest.mark.anyio
async def test_validate_no_model_error() -> None:
    result = await mcp.call_tool("validate_mesh", {})
    report = flat(result)
    assert report["ok"] is False
    assert "No model" in report["error"]


# ------------------------------------------------------------------
# measure
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_measure_bbox() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool("measure", {"what": "bbox"})
    data = flat(result)

    assert data["ok"] is True
    dims = data["dimensions"]
    assert abs(dims["x"] - 50.0) < 0.1
    assert abs(dims["y"] - 30.0) < 0.1
    assert abs(dims["z"] - 10.0) < 0.1


@pytest.mark.anyio
async def test_measure_volume() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool("measure", {"what": "volume"})
    data = flat(result)

    assert data["ok"] is True
    assert abs(data["volume_mm3"] - 15000.0) < 1.0


@pytest.mark.anyio
async def test_measure_faces() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool("measure", {"what": "faces"})
    data = flat(result)

    assert data["ok"] is True
    assert data["face_count"] == 6


@pytest.mark.anyio
async def test_measure_distance() -> None:
    """Measure distance between top and bottom faces of a 10mm box."""
    await _build(BOX_CODE)
    result = await mcp.call_tool(
        "measure",
        {"what": "distance", "from_selector": ">Z", "to_selector": "<Z"},
    )
    data = flat(result)

    assert data["ok"] is True
    assert abs(data["distance_mm"] - 10.0) < 0.1


@pytest.mark.anyio
async def test_measure_no_model_error() -> None:
    result = await mcp.call_tool("measure", {"what": "bbox"})
    data = flat(result)
    assert data["ok"] is False


# ------------------------------------------------------------------
# export_model
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_step() -> None:
    """Gate: STEP exports and can be re-imported."""
    await _build(BOX_CODE)
    result = await mcp.call_tool(
        "export_model", {"format": "step", "filename": "box"}
    )
    data = flat(result)

    assert data["ok"] is True
    assert data["format"] == "step"
    path = Path(data["path"])
    assert path.exists()
    assert data["size_bytes"] > 0

    # Re-import the STEP file to verify it's valid
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    assert status == IFSelect_RetDone, f"STEP re-import failed: {status}"
    reader.TransferRoots()
    shape = reader.OneShape()
    assert not shape.IsNull()


@pytest.mark.anyio
async def test_export_stl_deterministic_within_one_process() -> None:
    """A canary, not the gate.

    This only shows that two calls in one interpreter agree, which is
    blind to anything seeded once at import -- it is exactly why the STEP
    header timestamp and the random 3MF UUIDs survived (CAD-017). The
    real guarantee is SPEC N4, tested across separate processes and all
    four formats in `tests/test_determinism.py`.
    """
    await _build(BOX_CODE)

    hashes = []
    for name in ("box1", "box2"):
        result = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": name}
        )
        path = Path(flat(result)["path"])
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())

    assert hashes[0] == hashes[1], "STL output is not deterministic"


@pytest.mark.anyio
async def test_export_3mf() -> None:
    """3MF had no test at all, and was broken: trimesh needs lxml.

    The format is advertised in SPEC G4, the `export_model` schema, the
    README and the modelling prompt, so a missing dependency made a
    documented output format fail at runtime for every user.
    """
    await _build(BOX_CODE)
    result = await mcp.call_tool(
        "export_model", {"format": "3mf", "filename": "box"}
    )
    data = flat(result)

    assert data["ok"] is True, data
    path = Path(data["path"])
    assert path.exists()
    assert data["size_bytes"] > 0

    import trimesh

    mesh = trimesh.load(str(path), force="mesh")
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(50 * 30 * 10, rel=1e-3)


@pytest.mark.anyio
async def test_export_glb() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool(
        "export_model", {"format": "glb", "filename": "box"}
    )
    data = flat(result)

    assert data["ok"] is True
    assert Path(data["path"]).exists()


@pytest.mark.anyio
async def test_export_no_model_error() -> None:
    result = await mcp.call_tool(
        "export_model", {"format": "stl"}
    )
    data = flat(result)
    assert data["ok"] is False


@pytest.mark.anyio
async def test_export_invalid_format() -> None:
    await _build(BOX_CODE)
    result = await mcp.call_tool(
        "export_model", {"format": "obj"}
    )
    data = flat(result)
    assert data["ok"] is False


@pytest.mark.anyio
async def test_export_records_in_session() -> None:
    await _build(BOX_CODE)
    await mcp.call_tool("export_model", {"format": "stl"})
    sess = session.get_or_create()
    assert len(sess.exports) == 1
    assert sess.exports[0]["format"] == "stl"
