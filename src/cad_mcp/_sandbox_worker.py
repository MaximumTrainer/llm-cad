#!/usr/bin/env python
"""Sandbox worker — executed as a subprocess, never imported by the server.

Receives the session tmpdir as sys.argv[1]. Reads user_code.py from that
directory, executes it in a restricted environment, and prints a single
JSON line to stdout with the result.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import sys
import traceback


def _block_sockets() -> None:
    import socket as _sock

    class _Blocked:
        def __init__(self, *a: object, **kw: object) -> None:
            raise OSError("Network access is disabled in the CAD sandbox")

    _sock.socket = _Blocked  # type: ignore[assignment,misc]


def _block_dangerous_os() -> None:
    from collections.abc import Callable

    def _denied(name: str) -> Callable[..., None]:
        def _raise(*a: object, **kw: object) -> None:
            raise PermissionError(f"{name}() is blocked in the CAD sandbox")

        return _raise

    for fn in (
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    ):
        if hasattr(os, fn):
            setattr(os, fn, _denied(fn))


def _install_import_restriction() -> None:
    allowed_top = frozenset({"cadquery", "math", "numpy"})
    denied_top = frozenset(
        {
            "subprocess",
            "multiprocessing",
            "ctypes",
            "shutil",
            "http",
            "urllib",
            "requests",
            "webbrowser",
            "xmlrpc",
            "ftplib",
            "smtplib",
            "poplib",
            "imaplib",
            "socketserver",
            "asyncio",
            "concurrent",
        }
    )
    snapshot = frozenset(sys.modules.keys())
    real_import = builtins.__import__

    def restricted(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if level != 0:
            return real_import(name, globals, locals, fromlist, level)
        top = name.split(".")[0]
        if top in denied_top:
            raise ImportError(
                f"Import of '{name}' is not allowed in the sandbox."
            )
        if top in allowed_top or top in snapshot:
            return real_import(name, globals, locals, fromlist, level)
        raise ImportError(
            f"Import of '{name}' is not allowed. "
            f"Available modules: cadquery, math, numpy."
        )

    builtins.__import__ = restricted  # type: ignore[assignment]


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
    os.chdir(tmpdir)

    # 1. Block sockets before any other import
    _block_sockets()

    # 2. Import cadquery (needs to happen before import restriction)
    import cadquery

    # 3. Block dangerous os functions
    _block_dangerous_os()

    # 4. Install import restriction
    _install_import_restriction()

    # 5. Read user code
    code_path = os.path.join(tmpdir, "user_code.py")
    with open(code_path) as f:
        user_code = f.read()

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
        print(json.dumps(err))
        return

    sys.stdout = real_stdout

    # 8. Check for result variable
    if "result" not in ns:
        print(
            json.dumps(
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
        )
        return

    result = ns["result"]

    # 9. Validate result type — accept Workplane or Shape
    if isinstance(result, cadquery.Workplane):
        wp = result
    elif hasattr(result, "wrapped"):
        wp = cadquery.Workplane().newObject([result])
    else:
        print(
            json.dumps(
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
    brep_path = os.path.join(tmpdir, "current.brep")
    try:
        wp.val().exportBrep(brep_path)
    except Exception as export_err:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "ExportError",
                    "message": f"Failed to serialize shape to BREP: {export_err}",
                }
            )
        )
        return

    print(
        json.dumps(
            {
                "ok": True,
                "solid_count": solid_count,
                "bbox": bbox,
            }
        )
    )


if __name__ == "__main__":
    main()
