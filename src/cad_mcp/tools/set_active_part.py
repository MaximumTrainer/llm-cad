"""set_active_part tool — switch which part execute_cad targets (SPEC 10.3)."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("set_active_part")
    def set_active_part(name: str) -> str:
        """Switch the active part so ``execute_cad`` writes to it.

        Args:
            name: Name of an existing part.

        Returns:
            JSON confirmation with active part details.
        """
        sess = session.get_or_create()

        if name not in sess.parts:
            names = list(sess.parts.keys())
            return _err(
                f"Part '{name}' does not exist. "
                f"Available parts: {names}"
            )

        sess.active_part = name
        part = sess.parts[name]

        return json.dumps({
            "ok": True,
            "active_part": name,
            "color": part.color,
            "code_blocks": len(part.code_history),
            "has_model": sess.brep_path(name).exists(),
        })


def _err(message: str) -> str:
    d: dict[str, Any] = {"ok": False, "error": message}
    return json.dumps(d)
