"""render_views tool -- render multi-angle previews of the assembly."""
from __future__ import annotations

import base64

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ImageContent, TextContent

from cad_mcp import render, session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail
from cad_mcp.render import PartMesh, apply_transform, color_rgb


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("render_views")
    def render_views(
        views: list[str] | None = None,
        width: int = 800,
        height: int = 600,
        parts: list[str] | None = None,
        ctx: Context | None = None,
    ) -> list[TextContent | ImageContent]:
        """Render the current assembly as a multi-view PNG grid.

        Call this after every ``execute_cad`` to visually inspect the
        result.  All parts are rendered in a single scene with distinct
        colors.

        Args:
            views: Which views to include. Default: front, right, top, iso.
                   Choices: ``front``, ``right``, ``top``, ``iso``.
            width:  Total image width in pixels (default 800).
            height: Total image height in pixels (default 600).
            parts:  Part names to render. Default: all parts with geometry.
        """
        sess = session.for_context(ctx)

        if parts is not None:
            bad = [n for n in parts if n not in sess.parts]
            if bad:
                return [TextContent(
                    type="text",
                    text=fail(
                        "PartNotFound",
                        f"Unknown parts: {bad}.",
                        hint=f"Available: {list(sess.parts)}.",
                    ),
                )]
            render_parts = [sess.parts[n] for n in parts]
        else:
            render_parts = list(sess.parts.values())

        part_meshes: list[PartMesh] = []
        for part in render_parts:
            brep = sess.brep_path(part.name)
            if not brep.exists():
                continue
            try:
                verts, faces = render.load_and_tessellate(brep)
                verts = apply_transform(verts, part.translate, part.rotate)
                part_meshes.append(PartMesh(
                    verts=verts,
                    faces=faces,
                    color=color_rgb(part.color),
                ))
            except Exception:
                continue

        if not part_meshes:
            return [
                TextContent(
                    type="text",
                    text=fail(
                        "NoModel",
                        "No model to render.",
                        hint="Run execute_cad to create geometry first.",
                    ),
                )
            ]

        try:
            png_bytes = render.render_assembly(
                part_meshes, views, width, height
            )
        except Exception as exc:
            return [
                TextContent(
                    type="text",
                    text=fail(
                        type(exc).__name__,
                        f"Render failed: {exc}",
                        hint=(
                            "Try fewer views, a smaller width/height, or a "
                            "simpler model."
                        ),
                    ),
                )
            ]

        view_list = views or render.DEFAULT_VIEWS
        b64 = base64.b64encode(png_bytes).decode("ascii")

        rendered_names = [
            p.name for p in render_parts if sess.has_model(p.name)
        ]
        return [
            ImageContent(type="image", data=b64, mime_type="image/png"),
            TextContent(
                type="text",
                text=(
                    f"Rendered {len(view_list)} view(s): "
                    f"{', '.join(view_list)}  "
                    f"({width}x{height} px)  "
                    f"Parts: {', '.join(rendered_names)}"
                ),
            ),
        ]
