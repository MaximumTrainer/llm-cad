"""delete_part tool — remove a part from the assembly (SPEC 10.3)."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("delete_part")
    def delete_part(name: str,
        ctx: Context | None = None,
    ) -> str:
        """Delete a part from the assembly.

        Cannot delete the last remaining part.

        Args:
            name: Name of the part to delete.

        Returns:
            JSON confirmation with the remaining parts.
        """
        sess = session.for_context(ctx)

        if name not in sess.parts:
            names = list(sess.parts.keys())
            return fail(
                "PartNotFound",
                f"Part '{name}' does not exist.",
                hint=f"Available parts: {names}.",
            )

        if len(sess.parts) <= 1:
            return fail(
                "LastPart",
                "Cannot delete the last remaining part.",
                hint="Use reset_session to clear the whole assembly.",
            )

        brep = sess.brep_path(name)
        if brep.exists():
            brep.unlink()

        del sess.parts[name]

        if sess.active_part == name:
            sess.active_part = next(iter(sess.parts))

        remaining: list[dict[str, Any]] = []
        for n, p in sess.parts.items():
            remaining.append({
                "name": n,
                "color": p.color,
                "has_model": sess.brep_path(n).exists(),
                "is_active": n == sess.active_part,
            })

        return ok(
            f"Deleted part '{name}'. Active part is now "
            f"'{sess.active_part}'; {len(remaining)} part(s) remain.",
            deleted=name,
            active_part=sess.active_part,
            parts=remaining,
        )
