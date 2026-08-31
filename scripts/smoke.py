#!/usr/bin/env python3
"""End-to-end smoke test: build bracket, render, validate, measure, export.

Drives the full cad-mcp loop programmatically via ``mcp.call_tool()``,
verifying every step.  Exits 0 on success, 1 on any failure.

Usage::

    uv run python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Ensure src is importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cad_mcp import session
from cad_mcp.resources import EXAMPLES
from cad_mcp.server import mcp

# SPEC 9.1 bracket code
BRACKET_CODE = EXAMPLES["bracket"]


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


async def main() -> None:
    session.cleanup_all()
    print("=== cad-mcp smoke test ===\n")

    # ── Step 1: execute_cad ──────────────────────────────────────
    print("Step 1: execute_cad (bracket)")
    result = await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})
    text = result.content[0].text  # type: ignore[union-attr]
    if not text.startswith("OK"):
        _fail(f"execute_cad failed:\n{text}")
    _ok(text.split("\n")[0])

    # ── Step 2: render_views ─────────────────────────────────────
    print("\nStep 2: render_views")
    result = await mcp.call_tool("render_views", {})
    types = [c.type for c in result.content]
    if "image" not in types:
        _fail(f"render_views returned no image: {types}")
    _ok(f"got image + text ({len(types)} content blocks)")

    # ── Step 3: measure ──────────────────────────────────────────
    print("\nStep 3: measure (bbox + volume)")
    result = await mcp.call_tool("measure", {"what": "bbox"})
    bbox = json.loads(result.content[0].text)  # type: ignore[union-attr]
    if not bbox.get("ok"):
        _fail(f"measure bbox failed: {bbox}")
    dims = bbox["dimensions"]
    _ok(
        f"bbox: {dims['x']:.1f} x {dims['y']:.1f} x {dims['z']:.1f} mm"
    )

    result = await mcp.call_tool("measure", {"what": "volume"})
    vol = json.loads(result.content[0].text)  # type: ignore[union-attr]
    if not vol.get("ok"):
        _fail(f"measure volume failed: {vol}")
    _ok(f"volume: {vol['volume_mm3']:.1f} mm³")

    # ── Step 4: validate_mesh ────────────────────────────────────
    print("\nStep 4: validate_mesh")
    result = await mcp.call_tool("validate_mesh", {})
    report = json.loads(result.content[0].text)  # type: ignore[union-attr]
    if "watertight" not in report:
        _fail(f"validate_mesh returned error: {report}")
    _ok(
        f"watertight={report['watertight']}  "
        f"manifold={report['manifold']}  "
        f"mass={report['mass_pla_g']}g PLA"
    )
    if report.get("issues"):
        print(f"  warnings: {report['issues']}")

    # ── Step 5: export_model (STL + STEP) ────────────────────────
    print("\nStep 5: export_model")
    for fmt in ("stl", "step"):
        result = await mcp.call_tool(
            "export_model", {"format": fmt, "filename": "bracket"}
        )
        exp = json.loads(result.content[0].text)  # type: ignore[union-attr]
        if not exp.get("ok"):
            _fail(f"export {fmt} failed: {exp}")
        p = Path(exp["path"])
        if not p.exists():
            _fail(f"exported file missing: {p}")
        _ok(f"{fmt.upper()}: {p.name} ({exp['size_bytes']} bytes)")

    # ── Step 6: verify STL determinism ───────────────────────────
    print("\nStep 6: STL determinism check")
    import hashlib

    result1 = await mcp.call_tool(
        "export_model", {"format": "stl", "filename": "det1"}
    )
    result2 = await mcp.call_tool(
        "export_model", {"format": "stl", "filename": "det2"}
    )
    h1 = hashlib.sha256(
        Path(
            json.loads(result1.content[0].text)["path"]  # type: ignore[union-attr]
        ).read_bytes()
    ).hexdigest()
    h2 = hashlib.sha256(
        Path(
            json.loads(result2.content[0].text)["path"]  # type: ignore[union-attr]
        ).read_bytes()
    ).hexdigest()
    if h1 != h2:
        _fail(f"STL hashes differ: {h1} vs {h2}")
    _ok(f"SHA-256 stable: {h1[:16]}...")

    # ── Step 7: STEP re-import ───────────────────────────────────
    print("\nStep 7: STEP re-import")
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    sess = session.get_or_create()
    step_path = sess.tmpdir / "output" / "bracket.step"
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    if status != IFSelect_RetDone:
        _fail(f"STEP re-import failed: status={status}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        _fail("STEP re-imported as null shape")
    _ok("STEP re-imports successfully")

    # ── Done ─────────────────────────────────────────────────────
    print("\n=== ALL SMOKE CHECKS PASSED ===")
    session.cleanup_all()


if __name__ == "__main__":
    asyncio.run(main())
