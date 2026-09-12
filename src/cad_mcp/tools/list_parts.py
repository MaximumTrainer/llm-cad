"""list_parts tool — overview of assembly parts (SPEC 10.3)."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import ok


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("list_parts")
    def list_parts(ctx: Context) -> str:
        """List all parts in the current assembly.

        Returns:
            JSON array of parts with name, color, position, bbox,
            and whether each is the active part.
        """
        sess = session.for_context(ctx)
        parts: list[dict[str, Any]] = []

        for name, part in sess.parts.items():
            info: dict[str, Any] = {
                "name": name,
                "color": part.color,
                "code_blocks": len(part.code_history),
                "has_model": sess.brep_path(name).exists(),
                "translate": list(part.translate),
                "rotate": list(part.rotate),
                "bbox": part.bbox,
                "source": part.source,
                "reproducible_from_code": part.source == "cadquery",
                "ai_prompt": part.ai_prompt,
                "is_active": name == sess.active_part,
            }
            parts.append(info)

        names = ", ".join(p["name"] for p in parts) or "none"
        return ok(
            f"{len(parts)} part(s): {names} (active: {sess.active_part})",
            active_part=sess.active_part,
            part_count=len(parts),
            parts=parts,
        )
