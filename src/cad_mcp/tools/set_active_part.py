"""set_active_part tool — switch which part execute_cad targets (SPEC 10.3)."""
from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("set_active_part")
    def set_active_part(name: str,
        ctx: Context | None = None,
    ) -> str:
        """Switch the active part so ``execute_cad`` writes to it.

        Args:
            name: Name of an existing part.

        Returns:
            JSON confirmation with active part details.
        """
        sess = session.for_context(ctx)

        if name not in sess.parts:
            names = list(sess.parts.keys())
            return fail(
                "PartNotFound",
                f"Part '{name}' does not exist.",
                hint=f"Available parts: {names}. Create one with create_part.",
            )

        sess.active_part = name
        part = sess.parts[name]

        return ok(
            f"Active part is now '{name}' "
            f"({len(part.code_history)} code block(s))",
            active_part=name,
            color=part.color,
            code_blocks=len(part.code_history),
            has_model=sess.brep_path(name).exists(),
        )
