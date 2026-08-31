"""execute_cad tool — run CadQuery code in a sandbox."""
from __future__ import annotations

import json
import shutil
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import sandbox, session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("execute_cad")
    def execute_cad(code: str, mode: str = "replace") -> str:
        """Execute CadQuery Python code in a sandboxed subprocess.

        The code MUST assign its final shape to a variable named ``result``.
        ``cadquery`` (also available as ``cq``), ``math``, and ``numpy``
        are pre-imported.

        Code is written to the **active part**.  Use ``set_active_part``
        to switch which part this targets.

        Args:
            code: Python/CadQuery source to execute.
            mode: ``"replace"`` starts fresh; ``"append"`` adds to the
                  existing code history and re-runs everything.

        Returns:
            A structured report: solid count and bounding box on success,
            or error type, line number, code snippet, and a hint on failure.
        """
        if mode not in ("replace", "append"):
            return _err(
                "ValueError",
                f"Invalid mode '{mode}'. Use 'replace' or 'append'.",
            )

        sess = session.get_or_create()
        part = sess.get_active_part()
        prev_history = list(part.code_history)

        if mode == "replace":
            part.code_history = [code]
        else:
            part.code_history.append(code)

        result = sandbox.run(part.accumulated_code(), sess.tmpdir)

        if result.ok:
            part.bbox = result.bbox
            sandbox_brep = sess.tmpdir / "current.brep"
            part_brep = sess.brep_path()
            if sandbox_brep.exists() and sandbox_brep != part_brep:
                shutil.copy2(str(sandbox_brep), str(part_brep))
        else:
            part.code_history = prev_history

        return result.format_for_llm()


def _err(error_type: str, message: str) -> str:
    d: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
    return json.dumps(d)
