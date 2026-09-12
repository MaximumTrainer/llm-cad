"""execute_cad tool — run CadQuery code in a sandbox."""
from __future__ import annotations

import os
import uuid

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import sandbox, session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok


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
            return fail(
                "ValueError",
                f"Invalid mode '{mode}'.",
                hint="Use mode='replace' to start fresh or 'append' to add.",
            )

        sess = session.for_context(ctx)

        # Serialise mutations within a session: tools are dispatched on a
        # thread pool, so two execute_cad calls could otherwise interleave
        # and cross-contaminate history and geometry (CAD-008).
        with sess.lock:
            part = sess.get_active_part()

            if part.source != "cadquery" and part.code_history:
                return fail(
                    "NotReproducible",
                    f"Part '{part.name}' holds AI-generated mesh geometry, "
                    f"which cannot be reproduced from code — running "
                    f"execute_cad here would destroy it.",
                    hint=(
                        "create_part(name=...) to model alongside it, "
                        "set_active_part to target a CadQuery part, or "
                        "delete_part first if you meant to replace it."
                    ),
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

            if not result.ok:
                return fail(
                    result.error_type or "ExecutionError",
                    result.message or "Execution failed.",
                    line=result.line,
                    snippet=result.snippet,
                    hint=result.hint,
                )
            return ok(
                result.format_for_llm(),
                solid_count=result.solid_count,
                bbox=result.bbox,
                part=part.name,
                code_blocks=len(part.code_history),
            )
