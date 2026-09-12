#!/usr/bin/env python
"""Sandbox worker — executed as a subprocess, never imported by the server.

Receives the session tmpdir as sys.argv[1]. Reads user_code.py from that
directory, executes it in a restricted environment, and prints a single
JSON line to stdout with the result.
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

_RESULT_PATH = os.environ.get("CAD_MCP_RESULT_OUT", "")


def emit(payload: dict[str, object]) -> None:
    """Write the result where the parent can read it unambiguously.

    stdout is shared with anything CadQuery, VTK or OCP decide to
    print ('VTK not installed' is a real example), so using it as the
    result channel is the stdout-corruption hazard PLAN warns about.
    A dedicated file has no such collisions.
    """
    blob = json.dumps(payload)
    if _RESULT_PATH:
        with open(_RESULT_PATH, "w", encoding="utf-8") as fh:
            fh.write(blob)
    print(blob)


def _add_hint(msg: str, snippet: str | None = None) -> str | None:
    ml = msg.lower()
    ctx = (ml + " " + (snippet or "").lower()).strip()
    if "fillet" in ctx and any(
        w in ctx
        for w in ("radius", "exceed", "impossible", "failed", "command not done")
    ):
        return "Reduce fillet radius or select fewer edges."
    if "chamfer" in ctx and any(
        w in ctx for w in ("distance", "exceed", "failed", "command not done")
    ):
        return "Reduce chamfer distance — it exceeds the available edge length."
    if "brep_api" in ml and "command not done" in ml:
        return (
            "An OCP kernel operation failed — check fillet/chamfer radii "
            "and boolean operand sizes."
        )
    if "selector" in ml or "does not exist" in ml:
        return (
            "Check selector string — the face/edge may not exist "
            "after prior operations."
        )
    return None


def main() -> None:
    tmpdir = sys.argv[1]
    code_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        tmpdir, "user_code.py"
    )
    os.chdir(tmpdir)

    # Import cadquery fully before any guard is installed: it pulls in a
    # large lazy dependency tree, and the guards must not fight it.
    import cadquery

    # Read the user's code before the path guard exists — the code file
    # lives in the session dir, so this is also legal afterwards, but
    # doing it first keeps the guard's allowed set minimal.
    with open(code_path, encoding="utf-8") as f:
        user_code = f.read()

    # Everything after this line runs confined (SPEC N1).
    from cad_mcp._sandbox_policy import install_all

    install_all(tmpdir)

    # 6. Redirect stdout to capture user prints during exec
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()

    # 7. Execute user code
    ns: dict[str, object] = {"cadquery": cadquery, "cq": cadquery}
    try:
        exec(compile(user_code, "<cad>", "exec"), ns)
    except Exception as exc:
        sys.stdout = real_stdout
        tb_entries = traceback.extract_tb(exc.__traceback__)
        user_line: int | None = None
        user_text: str | None = None
        for frame in reversed(tb_entries):
            if frame.filename == "<cad>":
                user_line = frame.lineno
                user_text = frame.line
                break

        err: dict[str, object] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        if user_line is not None:
            err["line"] = user_line
        if user_text:
            err["snippet"] = user_text
        hint = _add_hint(str(exc), user_text)
        if hint:
            err["hint"] = hint
        emit(err)
        return

    sys.stdout = real_stdout

    # 8. Check for result variable
    if "result" not in ns:
        emit(
                {
                    "ok": False,
                    "error_type": "NameError",
                    "message": (
                        "Code must assign the final shape to a variable "
                        "named 'result'."
                    ),
                    "hint": "Add: result = cq.Workplane('XY').box(10, 10, 10)",
                }
            
        )
        return

    result = ns["result"]

    # 9. Validate result type — accept Workplane or Shape
    if isinstance(result, cadquery.Workplane):
        wp = result
    elif hasattr(result, "wrapped"):
        wp = cadquery.Workplane().newObject([result])
    else:
        emit(
                {
                    "ok": False,
                    "error_type": "TypeError",
                    "message": (
                        f"'result' must be a cadquery Workplane or Shape, "
                        f"got {type(result).__name__}"
                    ),
                    "hint": "result = cq.Workplane('XY').box(10, 10, 10)",
                }
            
        )
        return

    # 10. Count solids
    try:
        solid_count = len(wp.solids().vals())
    except Exception:
        solid_count = 1

    # 11. Bounding box
    bbox: dict[str, float] | None = None
    try:
        bb = wp.val().BoundingBox()
        bbox = {
            "xmin": round(bb.xmin, 4),
            "ymin": round(bb.ymin, 4),
            "zmin": round(bb.zmin, 4),
            "xmax": round(bb.xmax, 4),
            "ymax": round(bb.ymax, 4),
            "zmax": round(bb.zmax, 4),
        }
    except Exception:
        pass

    # 12. Serialize to BREP
    brep_path = os.environ.get("CAD_MCP_BREP_OUT") or os.path.join(
        tmpdir, "current.brep"
    )
    try:
        wp.val().exportBrep(brep_path)
    except Exception as export_err:
        emit(
                {
                    "ok": False,
                    "error_type": "ExportError",
                    "message": f"Failed to serialize shape to BREP: {export_err}",
                }
            
        )
        return

    emit(
            {
                "ok": True,
                "solid_count": solid_count,
                "bbox": bbox,
            }
        
    )


if __name__ == "__main__":
    main()
