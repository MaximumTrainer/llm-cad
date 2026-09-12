"""gen_ai_mesh tool — generate 3D mesh via Meshy API (SPEC 10.2)."""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import meshy, session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok
from cad_mcp.mesh_to_brep import glb_to_brep
from cad_mcp.meshy import MeshyError, MeshyTimeout


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("gen_ai_mesh")
    async def gen_ai_mesh(
        prompt: str,
        negative_prompt: str = "",
        art_style: str = "realistic",
        topology: str = "triangle",
        target_polycount: int = 4000,
        refine: bool = False,
        ai_model: str = "latest",
        ctx: Context | None = None,
    ) -> str:
        """Generate a 3D mesh from a text description using the Meshy API.

        Best for organic shapes (figurines, characters, terrain) that are
        hard to model parametrically with CadQuery.  The result becomes the
        session's current shape -- render_views, validate_mesh, and
        export_model all work on it.

        WARNING: the imported shape is a tessellated B-rep (triangles, not
        NURBS).  Parametric operations like fillet() and shell() will fail.

        Requires MESHY_API_KEY env var.

        Args:
            prompt: Text description of the desired 3D model.
            negative_prompt: Things to avoid in the generation.
            art_style: "realistic" or "sculpture".
            topology: "triangle" or "quad".
            target_polycount: Target polygon count (100-15000, default 4000).
            refine: If True, run a second pass to add textures (slower).
            ai_model: Meshy model version ("latest", "meshy-7", etc.).

        Returns:
            JSON report with task_id, mesh stats, and thumbnail URL on
            success; structured error on failure.
        """
        api_key = os.environ.get("MESHY_API_KEY", "")
        if not api_key:
            return fail(
                "MissingAPIKey",
                "MESHY_API_KEY is not set.",
                hint=(
                    "Set the MESHY_API_KEY environment variable "
                    "(get a key at https://meshy.ai)."
                ),
            )

        sess = session.for_context(ctx)
        part = sess.get_active_part()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                task_id = await meshy.create_preview(
                    client,
                    api_key,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    art_style=art_style,
                    topology=topology,
                    target_polycount=target_polycount,
                    ai_model=ai_model,
                )

                result = await meshy.poll_until_done(
                    client, api_key, task_id, meshy.PREVIEW_TIMEOUT_S
                )

                glb_url = result.model_urls.get("glb", "")
                if not glb_url:
                    return _err("No GLB URL in Meshy response", task_id)

                meshy_dir = sess.tmpdir / "meshy"
                glb_path = meshy_dir / f"{task_id}.glb"
                await meshy.download_glb(client, glb_url, glb_path)

                if refine:
                    refine_id = await meshy.create_refine(
                        client, api_key, task_id
                    )
                    result = await meshy.poll_until_done(
                        client, api_key, refine_id, meshy.REFINE_TIMEOUT_S
                    )
                    refined_glb_url = result.model_urls.get("glb", "")
                    if refined_glb_url:
                        glb_path = meshy_dir / f"{refine_id}.glb"
                        await meshy.download_glb(
                            client, refined_glb_url, glb_path
                        )
                    task_id = refine_id

        except MeshyTimeout as exc:
            return fail(
                "GenerationTimeout",
                f"Meshy task {exc.task_id} did not finish in time.",
                hint="Retry, or simplify the prompt.",
                task_id=exc.task_id,
            )
        except MeshyError as exc:
            return fail(
                "MeshyAPIError",
                f"HTTP {exc.status}: {exc}",
                hint=exc.hint,
                status=exc.status,
            )

        brep_path = sess.brep_path()
        try:
            stats = glb_to_brep(glb_path, brep_path)
        except Exception as exc:
            return _err(f"Mesh import failed: {exc}", task_id)

        part.code_history.append(
            f'# gen_ai_mesh: "{prompt}" (task_id: {task_id})'
        )
        part.bbox = None

        report: dict[str, Any] = {
            "ok": True,
            "task_id": task_id,
            "status": result.status,
            "thumbnail_url": result.thumbnail_url,
            "vertex_count": stats["vertex_count"],
            "face_count": stats["face_count"],
            "format": "glb",
            "glb_path": str(glb_path),
            "note": (
                "Shape imported as tessellated B-rep. "
                "Do NOT use fillet/shell/chamfer on this shape."
            ),
        }
        return ok(
            f"Generated mesh for {prompt!r}: {stats['face_count']} faces, "
            f"{stats['vertex_count']} vertices (task {task_id}). "
            f"Tessellated B-rep — do NOT use fillet/shell/chamfer.",
            **report,
        )


def _err(message: str, task_id: str) -> str:
    return fail("ImportError", message, task_id=task_id)
