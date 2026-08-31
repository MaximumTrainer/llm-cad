"""position_part tool — set translation/rotation for a part (SPEC 10.3)."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("position_part")
    def position_part(
        name: str,
        translate: list[float] | None = None,
        rotate: list[float] | None = None,
    ) -> str:
        """Set the position of a part in the assembly.

        The transform is applied during rendering and export, not baked
        into the geometry.  This means you can reposition parts without
        re-running ``execute_cad``.

        Args:
            name: Name of the part to position.
            translate: ``[x, y, z]`` translation in mm. Default ``[0, 0, 0]``.
            rotate: ``[rx, ry, rz]`` Euler XYZ rotation in degrees.
                    Default ``[0, 0, 0]``.

        Returns:
            JSON confirmation with the new position.
        """
        sess = session.get_or_create()

        if name not in sess.parts:
            names = list(sess.parts.keys())
            return _err(
                f"Part '{name}' does not exist. "
                f"Available parts: {names}"
            )

        part = sess.parts[name]

        if translate is not None:
            if len(translate) != 3:
                return _err("translate must be [x, y, z] (3 values).")
            part.translate = (translate[0], translate[1], translate[2])

        if rotate is not None:
            if len(rotate) != 3:
                return _err("rotate must be [rx, ry, rz] (3 values).")
            part.rotate = (rotate[0], rotate[1], rotate[2])

        return json.dumps({
            "ok": True,
            "name": name,
            "translate": list(part.translate),
            "rotate": list(part.rotate),
        })


def _err(message: str) -> str:
    d: dict[str, Any] = {"ok": False, "error": message}
    return json.dumps(d)
