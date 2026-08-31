"""create_part tool — add a new named part to the assembly (SPEC 10.3)."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.session import DEFAULT_COLORS, MAX_PARTS, PART_NAME_RE, Part


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("create_part")
    def create_part(
        name: str,
        color: str = "",
    ) -> str:
        """Create a new named part and set it as the active part.

        The new part starts empty — call ``execute_cad`` to add geometry.
        Part names must be lowercase, start with a letter, and contain
        only letters, digits, and underscores (max 32 chars).

        Args:
            name: Unique name for the part (e.g. ``"lid"``, ``"base"``).
            color: Display color. One of: steel, blue, red, green,
                   orange, purple. Auto-assigned if omitted.

        Returns:
            JSON with the created part and updated part list.
        """
        if not PART_NAME_RE.match(name):
            return _err(
                f"Invalid part name '{name}'. Must match "
                f"[a-z][a-z0-9_]{{0,31}}."
            )

        sess = session.get_or_create()

        if name in sess.parts:
            return _err(f"Part '{name}' already exists.")

        if len(sess.parts) >= MAX_PARTS:
            return _err(
                f"Maximum {MAX_PARTS} parts per assembly. "
                f"Delete a part first."
            )

        if not color:
            color = sess.next_color()
        elif color not in DEFAULT_COLORS:
            return _err(
                f"Unknown color '{color}'. "
                f"Choose from: {DEFAULT_COLORS}"
            )

        part = Part(name=name, color=color)
        sess.parts[name] = part
        sess.active_part = name

        parts_list = _parts_summary(sess)
        return json.dumps({
            "ok": True,
            "created": name,
            "color": color,
            "active_part": name,
            "parts": parts_list,
        })


def _parts_summary(sess: session.Session) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for n, p in sess.parts.items():
        result.append({
            "name": n,
            "color": p.color,
            "has_model": sess.brep_path(n).exists(),
            "is_active": n == sess.active_part,
        })
    return result


def _err(message: str) -> str:
    d: dict[str, Any] = {"ok": False, "error": message}
    return json.dumps(d)
