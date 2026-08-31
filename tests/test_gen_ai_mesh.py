"""Tests for SPEC 10.2 — gen_ai_mesh tool (Meshy API).

All API calls are mocked.  Live integration tests require MESHY_API_KEY
and are gated behind a pytest marker.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import trimesh

from cad_mcp import session
from cad_mcp.mesh_to_brep import glb_to_brep
from cad_mcp.meshy import (
    MeshyError,
    MeshyResult,
    MeshyTimeout,
)
from cad_mcp.server import mcp


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


# ------------------------------------------------------------------
# Unit: mesh_to_brep
# ------------------------------------------------------------------


class TestMeshToBrep:
    def test_glb_roundtrip(self, tmp_path: Path) -> None:
        """A trimesh box exported as GLB converts to a valid BREP."""
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        glb_path = tmp_path / "box.glb"
        mesh.export(str(glb_path), file_type="glb")

        brep_path = tmp_path / "box.brep"
        stats = glb_to_brep(glb_path, brep_path)

        assert brep_path.exists()
        assert brep_path.stat().st_size > 100
        assert stats["vertex_count"] > 0
        assert stats["face_count"] > 0

    def test_brep_reloads(self, tmp_path: Path) -> None:
        """The BREP written by glb_to_brep can be loaded by OCP."""
        mesh = trimesh.creation.icosphere(radius=5.0)
        glb_path = tmp_path / "sphere.glb"
        mesh.export(str(glb_path), file_type="glb")

        brep_path = tmp_path / "sphere.brep"
        glb_to_brep(glb_path, brep_path)

        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape

        shape = TopoDS_Shape()
        builder = BRep_Builder()
        ok = BRepTools.Read_s(shape, str(brep_path), builder)
        assert ok
        assert not shape.IsNull()

    def test_empty_glb_raises(self, tmp_path: Path) -> None:
        """An empty/invalid GLB raises ValueError."""
        glb_path = tmp_path / "empty.glb"
        glb_path.write_bytes(b"")
        brep_path = tmp_path / "out.brep"

        with pytest.raises((ValueError, Exception)):
            glb_to_brep(glb_path, brep_path)


# ------------------------------------------------------------------
# Unit: meshy client (mocked)
# ------------------------------------------------------------------


class TestMeshyClient:
    @pytest.mark.anyio
    async def test_poll_succeeds(self) -> None:
        from cad_mcp import meshy

        mock_client = AsyncMock()

        # First poll: IN_PROGRESS, second: SUCCEEDED
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(
                    200,
                    {"status": "IN_PROGRESS", "progress": 50},
                ),
                _mock_response(
                    200,
                    {
                        "status": "SUCCEEDED",
                        "model_urls": {"glb": "https://example.com/model.glb"},
                        "thumbnail_url": "https://example.com/thumb.png",
                        "polycount": 4000,
                    },
                ),
            ]
        )

        result = await meshy.poll_until_done(
            mock_client, "key", "task-1", timeout_s=10
        )
        assert result.status == "SUCCEEDED"
        assert result.model_urls["glb"] == "https://example.com/model.glb"
        assert result.task_id == "task-1"

    @pytest.mark.anyio
    async def test_poll_timeout(self) -> None:
        from cad_mcp import meshy

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                200, {"status": "IN_PROGRESS", "progress": 10}
            )
        )

        with pytest.raises(MeshyTimeout) as exc_info:
            await meshy.poll_until_done(
                mock_client, "key", "task-2", timeout_s=1
            )
        assert exc_info.value.task_id == "task-2"

    @pytest.mark.anyio
    async def test_poll_failed_task(self) -> None:
        from cad_mcp import meshy

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                200,
                {"status": "FAILED", "message": "Content violation"},
            )
        )

        with pytest.raises(MeshyError) as exc_info:
            await meshy.poll_until_done(
                mock_client, "key", "task-3", timeout_s=10
            )
        assert "Content violation" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_rate_limit_429(self) -> None:
        from cad_mcp import meshy

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_response(429, {"message": "Too many requests"})
        )

        with pytest.raises(MeshyError) as exc_info:
            await meshy.create_preview(
                mock_client, "key", prompt="a cube"
            )
        assert exc_info.value.status == 429
        assert "rate limited" in exc_info.value.hint.lower()

    @pytest.mark.anyio
    async def test_api_error_4xx(self) -> None:
        from cad_mcp import meshy

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_response(400, {"message": "Invalid prompt"})
        )

        with pytest.raises(MeshyError) as exc_info:
            await meshy.create_preview(
                mock_client, "key", prompt=""
            )
        assert exc_info.value.status == 400


# ------------------------------------------------------------------
# Integration: gen_ai_mesh tool via MCP (mocked API)
# ------------------------------------------------------------------


def _make_glb_bytes() -> bytes:
    """Create a minimal valid GLB from a trimesh box."""
    mesh = trimesh.creation.box(extents=[5, 5, 5])
    return mesh.export(file_type="glb")  # type: ignore[return-value]


class TestGenAiMeshTool:
    @pytest.mark.anyio
    async def test_missing_api_key(self) -> None:
        """Without MESHY_API_KEY, returns structured error."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHY_API_KEY", None)
            result = await mcp.call_tool("gen_ai_mesh", {"prompt": "a cube"})

        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is False
        assert data["error"] == "missing_api_key"
        assert "MESHY_API_KEY" in data["hint"]

    @pytest.mark.anyio
    async def test_success_flow(self) -> None:
        """Full mock success: create preview, poll, download, import."""
        glb_data = _make_glb_bytes()

        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.REFINE_TIMEOUT_S = 180
            mock_meshy.create_preview = AsyncMock(return_value="task-ok")
            mock_meshy.poll_until_done = AsyncMock(
                return_value=MeshyResult(
                    task_id="task-ok",
                    status="SUCCEEDED",
                    model_urls={"glb": "https://example.com/model.glb"},
                    thumbnail_url="https://thumb.example.com/img.png",
                    polycount=4000,
                )
            )

            async def fake_download(
                client: Any, url: str, dest: Path
            ) -> Path:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(glb_data)
                return dest

            mock_meshy.download_glb = AsyncMock(side_effect=fake_download)

            result = await mcp.call_tool(
                "gen_ai_mesh", {"prompt": "a simple chess pawn"}
            )

        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is True
        assert data["task_id"] == "task-ok"
        assert data["vertex_count"] > 0
        assert data["face_count"] > 0

        # Session should have the shape
        sess = session.get_or_create()
        brep = sess.brep_path()
        assert brep.exists()

        # Code history should record the gen_ai_mesh call
        part = sess.get_active_part()
        assert any("gen_ai_mesh" in h for h in part.code_history)

    @pytest.mark.anyio
    async def test_timeout_returns_error(self) -> None:
        """Timeout during polling returns structured error."""
        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.create_preview = AsyncMock(return_value="task-slow")
            mock_meshy.poll_until_done = AsyncMock(
                side_effect=MeshyTimeout("task-slow")
            )

            result = await mcp.call_tool(
                "gen_ai_mesh", {"prompt": "something complex"}
            )

        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is False
        assert data["error"] == "generation_timeout"
        assert data["task_id"] == "task-slow"

    @pytest.mark.anyio
    async def test_api_error_returns_structured(self) -> None:
        """Meshy API error (429) returns structured error."""
        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.create_preview = AsyncMock(
                side_effect=MeshyError(
                    429, "Rate limited", "Rate limited, wait and retry"
                )
            )

            result = await mcp.call_tool(
                "gen_ai_mesh", {"prompt": "a cube"}
            )

        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is False
        assert data["error"] == "meshy_api_error"
        assert data["status"] == 429

    @pytest.mark.anyio
    async def test_render_after_gen_ai_mesh(self) -> None:
        """After gen_ai_mesh, render_views should work on the imported shape."""
        glb_data = _make_glb_bytes()

        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.REFINE_TIMEOUT_S = 180
            mock_meshy.create_preview = AsyncMock(return_value="task-render")
            mock_meshy.poll_until_done = AsyncMock(
                return_value=MeshyResult(
                    task_id="task-render",
                    status="SUCCEEDED",
                    model_urls={"glb": "https://example.com/model.glb"},
                    thumbnail_url="",
                    polycount=12,
                )
            )

            async def fake_download(
                client: Any, url: str, dest: Path
            ) -> Path:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(glb_data)
                return dest

            mock_meshy.download_glb = AsyncMock(side_effect=fake_download)

            await mcp.call_tool(
                "gen_ai_mesh", {"prompt": "test shape"}
            )

        # Now render should work
        result = await mcp.call_tool("render_views", {})
        types = [c.type for c in result.content]
        assert "image" in types

    @pytest.mark.anyio
    async def test_export_after_gen_ai_mesh(self) -> None:
        """After gen_ai_mesh, export_model should work."""
        glb_data = _make_glb_bytes()

        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.REFINE_TIMEOUT_S = 180
            mock_meshy.create_preview = AsyncMock(return_value="task-export")
            mock_meshy.poll_until_done = AsyncMock(
                return_value=MeshyResult(
                    task_id="task-export",
                    status="SUCCEEDED",
                    model_urls={"glb": "https://example.com/model.glb"},
                    thumbnail_url="",
                    polycount=12,
                )
            )

            async def fake_download(
                client: Any, url: str, dest: Path
            ) -> Path:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(glb_data)
                return dest

            mock_meshy.download_glb = AsyncMock(side_effect=fake_download)

            await mcp.call_tool(
                "gen_ai_mesh", {"prompt": "export test"}
            )

        result = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "ai_mesh"}
        )
        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is True
        assert Path(data["path"]).exists()

    @pytest.mark.anyio
    async def test_refine_flow(self) -> None:
        """refine=True triggers a second Meshy task."""
        glb_data = _make_glb_bytes()

        with (
            patch.dict(os.environ, {"MESHY_API_KEY": "msy_test"}),
            patch("cad_mcp.tools.gen_ai_mesh.meshy") as mock_meshy,
        ):
            mock_meshy.PREVIEW_TIMEOUT_S = 120
            mock_meshy.REFINE_TIMEOUT_S = 180
            mock_meshy.create_preview = AsyncMock(
                return_value="task-preview"
            )
            mock_meshy.create_refine = AsyncMock(
                return_value="task-refine"
            )

            preview_result = MeshyResult(
                task_id="task-preview",
                status="SUCCEEDED",
                model_urls={"glb": "https://example.com/preview.glb"},
                thumbnail_url="",
                polycount=4000,
            )
            refine_result = MeshyResult(
                task_id="task-refine",
                status="SUCCEEDED",
                model_urls={"glb": "https://example.com/refined.glb"},
                thumbnail_url="https://example.com/refined_thumb.png",
                polycount=8000,
            )
            mock_meshy.poll_until_done = AsyncMock(
                side_effect=[preview_result, refine_result]
            )

            async def fake_download(
                client: Any, url: str, dest: Path
            ) -> Path:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(glb_data)
                return dest

            mock_meshy.download_glb = AsyncMock(side_effect=fake_download)

            result = await mcp.call_tool(
                "gen_ai_mesh",
                {"prompt": "a detailed figurine", "refine": True},
            )

        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
        assert data["ok"] is True
        assert data["task_id"] == "task-refine"
        mock_meshy.create_refine.assert_called_once_with(
            mock_meshy.create_refine.call_args[0][0],
            "msy_test",
            "task-preview",
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_response(status: int, body: dict[str, Any]) -> Any:
    """Create a mock httpx.Response."""

    class FakeResponse:
        status_code = status
        text = json.dumps(body)

        def json(self) -> dict[str, Any]:
            return body

        def raise_for_status(self) -> None:
            if status >= 400:
                msg = f"HTTP {status}"
                raise Exception(msg)

    return FakeResponse()
