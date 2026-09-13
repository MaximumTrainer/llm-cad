"""SPEC N4: the same model exports byte-identically, every time.

The determinism that matters is across *processes*, not across two calls
in one interpreter. `test_export_stl_deterministic` only ever did the
latter, which is why the STEP header timestamp and the random 3MF UUIDs
survived so long: both differ per write, but a single-process test that
exports twice would have caught those and still missed anything seeded
once at import. `scripts/determinism_check.py` crosses the boundary and
is reused verbatim by CI, so the gate and the local check cannot drift.

Two claims are separated deliberately:

* *stable* -- two runs produce the same bytes;
* *valid* -- those bytes are still a loadable STEP/3MF/STL.

Pinning a timestamp or rewriting a UUID could satisfy the first while
destroying the second, so each canonicalised format is re-imported and
its volume compared against the geometry it came from.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cad_mcp._determinism import (
    STEP_TIMESTAMP,
    canonicalise_3mf,
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


# ------------------------------------------------------------------
# The container rewrite, with no geometry in sight.
# ------------------------------------------------------------------


def _write_3mf(path: Path, uuids: list[str], *, create_system: int = 3) -> None:
    """A minimal 3MF-shaped zip carrying *uuids*."""
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model><build p:UUID="{uuids[0]}">'
        f'<item objectid="1" p:UUID="{uuids[1]}"/>'
        f'<object id="1" p:UUID="{uuids[2]}"/>'
        "</build></model>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in (
            ("3D/3dmodel.model", model),
            ("[Content_Types].xml", "<Types/>"),
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 13, 11, 22, 33))
            info.create_system = create_system
            zf.writestr(info, payload)


def test_canonicalise_3mf_erases_random_uuids(tmp_path: Path) -> None:
    """Two documents differing only in their UUIDs converge."""
    a, b = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _write_3mf(a, [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ])
    _write_3mf(b, [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ])

    canonicalise_3mf(a)
    canonicalise_3mf(b)

    assert a.read_bytes() == b.read_bytes()


def test_canonicalise_3mf_keeps_uuids_distinct(tmp_path: Path) -> None:
    """3MF requires a unique UUID per element; collapsing them is invalid."""
    path = tmp_path / "m.3mf"
    _write_3mf(path, [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ])

    canonicalise_3mf(path)

    with zipfile.ZipFile(path) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    found = _UUID_RE.findall(xml)
    assert len(found) == 3
    assert len(set(found)) == 3, "distinct UUIDs must not be merged"


def test_canonicalise_3mf_survives_a_foreign_create_system(
    tmp_path: Path,
) -> None:
    """The same document packed on Windows and on Linux must converge.

    `create_system` is one byte per member and zipfile sets it from the
    host OS, so without pinning it the cross-platform half of N4 fails
    even when every other byte agrees.
    """
    uuids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    windows, unix = tmp_path / "w.3mf", tmp_path / "u.3mf"
    _write_3mf(windows, uuids, create_system=0)
    _write_3mf(unix, uuids, create_system=3)

    canonicalise_3mf(windows)
    canonicalise_3mf(unix)

    assert windows.read_bytes() == unix.read_bytes()


def test_canonicalise_3mf_preserves_cross_references(tmp_path: Path) -> None:
    """A UUID used twice must still be the same UUID afterwards."""
    shared = "44444444-4444-4444-8444-444444444444"
    path = tmp_path / "m.3mf"
    _write_3mf(path, [shared, shared, "55555555-5555-4555-8555-555555555555"])

    canonicalise_3mf(path)

    with zipfile.ZipFile(path) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    build, item, obj = _UUID_RE.findall(xml)
    assert build == item, "a shared reference was split into two UUIDs"
    assert obj != build


def test_canonicalise_3mf_leaves_a_readable_zip(tmp_path: Path) -> None:
    path = tmp_path / "m.3mf"
    _write_3mf(path, [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ])

    canonicalise_3mf(path)

    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == [
            "3D/3dmodel.model",
            "[Content_Types].xml",
        ]


# ------------------------------------------------------------------
# The real thing: real geometry, real writers, separate processes.
# ------------------------------------------------------------------


@pytest.mark.geometry
@pytest.mark.slow
def test_every_format_is_byte_stable_across_processes(
    tmp_path: Path,
) -> None:
    """SPEC N4, the whole claim: all four formats, two fresh interpreters."""
    from determinism_check import FORMATS, check

    hashes = check(runs=2, workdir=tmp_path / "det")

    assert set(hashes) == set(FORMATS)
    # A format that silently wrote nothing would also be "stable".
    assert len(set(hashes.values())) == len(FORMATS)


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One build, every format -- the validity tests all share it."""
    from determinism_check import FORMATS, _ext, _run_child, _sentinel

    root = tmp_path_factory.mktemp("det-valid")
    brep_dir = root / "brep"
    brep_dir.mkdir()
    brep = brep_dir / "model.brep"
    _run_child(["--build-brep", str(brep)], "brep build")
    assert _sentinel(brep_dir).exists(), "B-rep build child did not finish"

    out = root / "out"
    _run_child(["--export-all", str(brep), str(out)], "export")
    assert _sentinel(out).exists(), "export child did not finish"
    return {fmt: out / f"model{_ext(fmt)}" for fmt in FORMATS}


@pytest.mark.geometry
@pytest.mark.slow
def test_pinned_step_still_reimports(exported: dict[str, Path]) -> None:
    """The header is pinned; the geometry must be untouched."""
    import cadquery as cq

    text = exported["step"].read_text(encoding="utf-8", errors="replace")
    assert STEP_TIMESTAMP in text, "FILE_NAME timestamp was not pinned"

    shape = cq.importers.importStep(str(exported["step"]))
    assert len(shape.solids().vals()) == 1
    assert shape.val().Volume() > 0


@pytest.mark.geometry
@pytest.mark.slow
def test_canonical_3mf_is_still_loadable(exported: dict[str, Path]) -> None:
    """Rewriting the container must not cost us the model."""
    import trimesh

    mesh = trimesh.load(str(exported["3mf"]), force="mesh")
    reference = trimesh.load(str(exported["stl"]), force="mesh")

    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(reference.volume, rel=1e-6)


def test_stl_and_glb_apply_the_same_normal_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAD-017: `export_stl` skipped `fix_normals`; GLB and 3MF applied it.

    The asymmetry was real but, it turns out, unreachable through
    geometry: OCCT tessellates a valid B-rep with consistent winding, and
    `BRepBuilderAPI_Sewing` normalises orientation even when handed a
    deliberately mixed-winding mesh, so `fix_normals` changed 0 of 1032
    faces on the fixture shape and 0 of 80 on a sewn one. No B-rep this
    server can build exposes the difference.

    So the tessellator is replaced with a mesh that *does* have mixed
    winding, and the two exporters are compared on it. That tests the
    exporters' normal handling -- which is the thing that differed --
    rather than the kernel's, and it fails if either exporter drops
    `fix_normals` again.
    """
    import numpy as np
    import trimesh

    from cad_mcp import export

    sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    faces = np.asarray(sphere.faces).copy()
    faces[::3] = faces[::3][:, ::-1]  # flip every third triangle
    verts = np.asarray(sphere.vertices, dtype=np.float64)

    assert not trimesh.Trimesh(
        vertices=verts, faces=faces, process=False
    ).is_winding_consistent, "fixture is not actually mixed-winding"

    monkeypatch.setattr(
        export,
        "load_and_tessellate",
        lambda *a, **k: (verts, faces.astype(np.int32)),
    )

    stl_path = tmp_path / "m.stl"
    glb_path = tmp_path / "m.glb"
    export.export_stl(tmp_path / "unused.brep", stl_path)
    export.export_glb(tmp_path / "unused.brep", glb_path)

    stl = trimesh.load(str(stl_path), force="mesh")
    glb = trimesh.load(str(glb_path), force="mesh")

    assert stl.is_winding_consistent, "STL shipped inconsistent winding"
    assert glb.is_winding_consistent
    assert stl.volume > 0
    assert stl.volume == pytest.approx(glb.volume, rel=1e-6)
