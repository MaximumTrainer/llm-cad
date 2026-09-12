"""list_parts tool — overview of assembly parts (SPEC 10.3)."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool


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
                "is_active": name == sess.active_part,
            }
            parts.append(info)

        return json.dumps({
            "ok": True,
            "active_part": sess.active_part,
            "part_count": len(parts),
            "parts": parts,
        })
