"""execute_cad tool — run CadQuery code in a sandbox."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import sandbox, session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("execute_cad")
    def execute_cad(code: str, mode: str = "replace",
        ctx: Context | None = None,
    ) -> str:
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

        sess = session.for_context(ctx)

        # Serialise mutations within a session: tools are dispatched on a
        # thread pool, so two execute_cad calls could otherwise interleave
        # and cross-contaminate history and geometry (CAD-008).
        with sess.lock:
            part = sess.get_active_part()

            if part.source != "cadquery" and part.code_history:
                return _err(
                    "NotReproducible",
                    f"Part '{part.name}' holds AI-generated mesh geometry, "
                    f"which is not reproducible from code, so running "
                    f"execute_cad here would destroy it. Use "
                    f"create_part(name=...) to model alongside it, "
                    f"set_active_part to target a CadQuery part, or "
                    f"delete_part first if you meant to replace it.",
                )

            prev_history = list(part.code_history)

            if mode == "replace":
                part.code_history = [code]
            else:
                part.code_history.append(code)

            # The worker writes to a per-run path; only a successful run is
            # promoted onto the part, so a failure never clobbers good
            # geometry and concurrent runs cannot collide (CAD-008).
            staged = sess.tmpdir / f"staged-{uuid.uuid4().hex[:12]}.brep"
            result = sandbox.run(
                part.accumulated_code(), sess.tmpdir, brep_out=staged
            )

            if result.ok:
                part.bbox = result.bbox
                part.source = "cadquery"
                if staged.exists():
                    os.replace(str(staged), str(sess.brep_path()))
            else:
                part.code_history = prev_history
                staged.unlink(missing_ok=True)

            return result.format_for_llm()


def _err(error_type: str, message: str) -> str:
    d: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
    return json.dumps(d)
