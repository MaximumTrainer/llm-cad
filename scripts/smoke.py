#!/usr/bin/env python3
"""End-to-end smoke test: build bracket, render, validate, measure, export.

Drives the full cad-mcp loop programmatically via ``mcp.call_tool()``,
verifying every step.  Exits 0 on success, 1 on any failure.

Usage::

    uv run python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Ensure src is importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cad_mcp import session
from cad_mcp.envelope import validate
from cad_mcp.resources import EXAMPLES
from cad_mcp.server import mcp

# SPEC 9.1 bracket code
BRACKET_CODE = EXAMPLES["bracket"]


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


def _payload(result: object) -> dict[str, Any]:
    """Validated envelope, flattened to one level (CAD-019)."""
    text = ""
    for block in result.content:  # type: ignore[attr-defined]
        if getattr(block, "type", None) == "text":
            text = str(block.text)
            break
    env = validate(text)
    out: dict[str, Any] = {"ok": env["ok"], "summary": env["summary"]}
    out.update(env.get("data") or {})
    if env.get("error"):
        out["error"] = env["error"].get("message", "")
        out["error_type"] = env["error"].get("type", "")
    return out


async def main() -> None:
    session.cleanup_all()
    print("=== cad-mcp smoke test ===\n")

    # ── Step 1: execute_cad ──────────────────────────────────────
    print("Step 1: execute_cad (bracket)")
    built = _payload(
        await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})
    )
    if not built["ok"]:
        _fail(f"execute_cad failed: {built}")
    _ok(built["summary"].split("\n")[0])

    # ── Step 2: render_views ─────────────────────────────────────
    print("\nStep 2: render_views")
    result = await mcp.call_tool("render_views", {})
    types = [c.type for c in result.content]
    if "image" not in types:
        _fail(f"render_views returned no image: {types}")
    _ok(f"got image + text ({len(types)} content blocks)")

    # ── Step 3: measure ──────────────────────────────────────────
    print("\nStep 3: measure (bbox + volume)")
    bbox = _payload(await mcp.call_tool("measure", {"what": "bbox"}))
    if not bbox.get("ok"):
        _fail(f"measure bbox failed: {bbox}")
    dims = bbox["dimensions"]
    _ok(
        f"bbox: {dims['x']:.1f} x {dims['y']:.1f} x {dims['z']:.1f} mm"
    )

    vol = _payload(await mcp.call_tool("measure", {"what": "volume"}))
    if not vol.get("ok"):
        _fail(f"measure volume failed: {vol}")
    _ok(f"volume: {vol['volume_mm3']:.1f} mm³")

    # ── Step 4: validate_mesh ────────────────────────────────────
    print("\nStep 4: validate_mesh")
    checked = _payload(await mcp.call_tool("validate_mesh", {}))
    if not checked["ok"] or not checked.get("parts"):
        _fail(f"validate_mesh returned error: {checked}")
    report = checked["parts"][0]
    _ok(
        f"watertight={report['watertight']}  "
        f"manifold={report['manifold']}  "
        f"mass={report['mass_pla_g']}g PLA"
    )
    if checked.get("issues"):
        print(f"  warnings: {checked['issues']}")

    # ── Step 5: export_model (STL + STEP) ────────────────────────
    print("\nStep 5: export_model")
    # Keep the paths the tool actually returned. Reconstructing them from
    # the session tmpdir broke silently the moment exports moved to the
    # durable output dir (CAD-023) — the pre-push hook caught it.
    exported: dict[str, Path] = {}
    for fmt in ("stl", "step"):
        exp = _payload(
            await mcp.call_tool(
                "export_model", {"format": fmt, "filename": "bracket"}
            )
        )
        if not exp.get("ok"):
            _fail(f"export {fmt} failed: {exp}")
        p = Path(exp["path"])
        if not p.exists():
            _fail(f"exported file missing: {p}")
        exported[fmt] = p
        _ok(f"{fmt.upper()}: {p.name} ({exp['size_bytes']} bytes)")

    # ── Step 6: verify STL determinism ───────────────────────────
    print("\nStep 6: STL determinism check")
    import hashlib

    det1 = _payload(
        await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "det1"}
        )
    )
    det2 = _payload(
        await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "det2"}
        )
    )
    h1 = hashlib.sha256(Path(det1["path"]).read_bytes()).hexdigest()
    h2 = hashlib.sha256(Path(det2["path"]).read_bytes()).hexdigest()
    if h1 != h2:
        _fail(f"STL hashes differ: {h1} vs {h2}")
    _ok(f"SHA-256 stable: {h1[:16]}...")

    # ── Step 7: STEP re-import ───────────────────────────────────
    print("\nStep 7: STEP re-import")
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    step_path = exported["step"]
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


def _run() -> None:
    """Run the loop, then exit without native static destructors.

    OCP/VTK segfault on interpreter teardown, which turned a fully
    successful smoke run into a non-zero exit (see issue #10). Flush and
    hard-exit so the status means what it says.
    """
    # Exports are durable by design (CAD-023), but a smoke run should not
    # litter the working tree or accumulate bracket-1..bracket-N.
    if not os.environ.get("CAD_MCP_OUTPUT_DIR"):
        import tempfile

        os.environ["CAD_MCP_OUTPUT_DIR"] = tempfile.mkdtemp(
            prefix="cad-mcp-smoke-"
        )

    code = 0
    try:
        asyncio.run(main())
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        import traceback

        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    _run()
