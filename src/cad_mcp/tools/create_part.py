"""create_part tool — add a new named part to the assembly (SPEC 10.3)."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok
from cad_mcp.session import DEFAULT_COLORS, MAX_PARTS, PART_NAME_RE, Part


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("create_part")
    def create_part(
        name: str,
        color: str = "",
        ctx: Context | None = None,
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
            return fail(
                "ValueError",
                f"Invalid part name '{name}'.",
                hint=(
                    "Names must match [a-z][a-z0-9_]{0,31}: lowercase, "
                    "start with a letter, max 32 chars. Try 'lid'."
                ),
            )

        sess = session.for_context(ctx)

        if name in sess.parts:
            return fail(
                "DuplicatePart",
                f"Part '{name}' already exists.",
                hint="Use set_active_part to target it, or pick a new name.",
            )

        if len(sess.parts) >= MAX_PARTS:
            return fail(
                "TooManyParts",
                f"Maximum {MAX_PARTS} parts per assembly.",
                hint="Delete a part with delete_part first.",
            )

        if not color:
            color = sess.next_color()
        elif color not in DEFAULT_COLORS:
            return fail(
                "ValueError",
                f"Unknown color '{color}'.",
                hint=f"Choose from: {DEFAULT_COLORS}.",
            )

        # Check-then-insert must be atomic: two concurrent create_part
        # calls could otherwise both pass the uniqueness check (CAD-008).
        with sess.lock:
            if name in sess.parts:
                return fail(
                "DuplicatePart",
                f"Part '{name}' already exists.",
                hint="Use set_active_part to target it, or pick a new name.",
            )
            if len(sess.parts) >= MAX_PARTS:
                return fail(
                    "TooManyParts",
                    f"Maximum {MAX_PARTS} parts per assembly.",
                    hint="Delete a part with delete_part first.",
                )
            sess.parts[name] = Part(name=name, color=color)
            sess.active_part = name

        parts_list = _parts_summary(sess)
        return ok(
            f"Created part '{name}' ({color}) and made it active. "
            f"Assembly now has {len(parts_list)} part(s).",
            created=name,
            color=color,
            active_part=name,
            parts=parts_list,
        )


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
