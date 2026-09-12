"""position_part tool — set translation/rotation for a part (SPEC 10.3)."""
from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("position_part")
    def position_part(
        name: str,
        translate: list[float] | None = None,
        rotate: list[float] | None = None,
        ctx: Context | None = None,
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
        sess = session.for_context(ctx)

        if name not in sess.parts:
            names = list(sess.parts.keys())
            return fail(
                "PartNotFound",
                f"Part '{name}' does not exist.",
                hint=f"Available parts: {names}.",
            )

        part = sess.parts[name]

        if translate is not None:
            if len(translate) != 3:
                return fail(
                    "ValueError",
                    f"translate has {len(translate)} values, expected 3.",
                    hint="Pass translate=[x, y, z] in mm.",
                )
            part.translate = (translate[0], translate[1], translate[2])

        if rotate is not None:
            if len(rotate) != 3:
                return fail(
                    "ValueError",
                    f"rotate has {len(rotate)} values, expected 3.",
                    hint="Pass rotate=[rx, ry, rz] in degrees.",
                )
            part.rotate = (rotate[0], rotate[1], rotate[2])

        return ok(
            f"Part '{name}' positioned at "
            f"translate={list(part.translate)} rotate={list(part.rotate)}",
            name=name,
            translate=list(part.translate),
            rotate=list(part.rotate),
        )
