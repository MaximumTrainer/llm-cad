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


# (matcher, hint) pairs, tried in order. A table rather than a chain of
# ifs so adding a rule is a one-line change.
#
# Written because the live-LLM test showed a model make 16 tool calls and
# never recover: "Cannot find a solid on the stack or in the parent chain"
# fired seven times with no hint attached, so each attempt gave it no new
# information (CAD-034). Hints are prescriptive — what to do next — not
# descriptive.
_HINTS: tuple[tuple[str, str], ...] = (
    (
        "nth element of an empty list",
        "The chain is empty: a selector such as .faces()/.edges() "
        "matched nothing, or there is no solid yet. Build geometry first "
        "with .box()/.extrude()/.revolve(), and check the selector with "
        "measure(what='faces').",
    ),
    (
        "supported names are",
        "Invalid plane name. Use 'XY', 'XZ', 'YZ' (or 'front', 'top', "
        "'right'). Selectors are different: '>Z', '<Z', '|Z', '#Z'.",
    ),
    (
        "cannot find a solid on the stack",
        "The chain has no solid yet: .faces()/.workplane()/.hole()/"
        ".shell() need an existing solid. Build one first with .box(), "
        ".extrude() or .revolve(), and keep a single chain rather than "
        "reassigning `result` from a fresh cq.Workplane().",
    ),
    (
        "cannot find a solid",
        "No solid in the chain. Create geometry with .box(), .extrude() "
        "or .revolve() before selecting faces or cutting holes.",
    ),
    (
        "expected {'xy' | 'xz'",
        "Invalid plane or selector string. Planes are 'XY', 'XZ', 'YZ'. "
        "Selectors look like '>Z', '<Z', '|Z', '#Z', '>X[1]'.",
    ),
    (
        "line continuation character",
        "The code contains stray escape characters. Send plain Python "
        "source, not an escaped string: real newlines, no backslash-n.",
    ),
    (
        "unexpected character after line continuation",
        "The code contains stray escape characters. Send plain Python "
        "source with real newlines.",
    ),
    (
        "brep_api",
        "An OCP kernel operation failed. Check fillet/chamfer radii "
        "against the available edge length, and make sure boolean "
        "operands actually overlap.",
    ),
    (
        "standard_nullobject",
        "An operation produced an empty shape. A boolean whose operands "
        "do not overlap, or a cut that removed everything, gives a null "
        "result.",
    ),
)

_FILLET_WORDS = ("radius", "exceed", "impossible", "failed", "command not done")
_CHAMFER_WORDS = ("distance", "exceed", "failed", "command not done")

# Methods LLMs commonly hallucinate, and what they usually meant.
_METHOD_SUGGESTIONS = {
    "pushtotop": "pushPoints",
    "cutthrough": "cutThruAll",
    "cutout": "cutBlind or cut",
    "addhole": "hole or cboreHole",
    "makebox": "box",
    "extrudelinear": "extrude",
}


def _workplane_suggestions(name: str) -> str:
    """Near-matches for a hallucinated Workplane method."""
    lowered = name.lower()
    if lowered in _METHOD_SUGGESTIONS:
        return _METHOD_SUGGESTIONS[lowered]
    try:
        import difflib

        import cadquery

        candidates = [
            attr
            for attr in dir(cadquery.Workplane)
            if not attr.startswith("_")
        ]
        close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.6)
        return ", ".join(close)
    except Exception:
        return ""


def _add_hint(
    msg: str,
    snippet: str | None = None,
    source: str | None = None,
) -> str | None:
    """A prescriptive next step for a failure, per SPEC N3.

    *source* is the user's whole submission. A chained CadQuery
    expression can report a line that does not contain the failing call,
    so matching on the snippet alone missed e.g. fillet errors.
    """
    ml = msg.lower()
    ctx = " ".join(
        part.lower()
        for part in (ml, snippet or "", source or "")
        if part
    ).strip()

    # Fillet/chamfer keep their dimension-specific wording.
    if "fillet" in ctx and any(w in ctx for w in _FILLET_WORDS):
        return (
            "Reduce the fillet radius or select fewer edges: the radius "
            "must be smaller than half the shortest adjoining edge."
        )
    if "chamfer" in ctx and any(w in ctx for w in _CHAMFER_WORDS):
        return (
            "Reduce the chamfer distance - it exceeds the available edge "
            "length."
        )

    # Hallucinated API, with near-matches from the real Workplane.
    if "object has no attribute" in ml:
        bad = msg.split("attribute")[-1].strip().strip("'\"")
        suggestions = _workplane_suggestions(bad)
        base = (
            f"'{bad}' is not a CadQuery method. Check the cadquery_primer "
            f"prompt or the cad://examples resources for the real name."
        )
        return f"{base} Did you mean: {suggestions}?" if suggestions else base

    for needle, hint in _HINTS:
        if needle in ctx:
            return hint

    if "selector" in ml or "does not exist" in ml:
        return (
            "Check the selector string - the face or edge may not exist "
            "after earlier operations. Use measure(what='faces') to see "
            "how many faces the shape has."
        )
    if ml.startswith("takes") or "positional argument" in ml:
        return (
            "Wrong number of arguments. Check the method signature in the "
            "cadquery_primer prompt."
        )
    return None


def _context_lines(source: str, line: int, radius: int = 2) -> list[str]:
    """The failing line plus its neighbours.

    The root cause is often the statement *before* the one that raised —
    a chain that lost its solid, for instance.
    """
    lines = source.splitlines()
    start = max(0, line - 1 - radius)
    end = min(len(lines), line + radius)
    out = []
    for i in range(start, end):
        marker = ">>" if i == line - 1 else "  "
        out.append(f"{marker} {i + 1:3}| {lines[i]}")
    return out


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
        if user_line is not None:
            err["context"] = _context_lines(user_code, user_line)
        hint = _add_hint(str(exc), user_text, user_code)
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
