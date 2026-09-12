"""Tests for SPEC 10.2 — gen_ai_mesh tool (Meshy API).

All API calls are mocked.  Live integration tests require MESHY_API_KEY
and are gated behind a pytest marker.
"""
from __future__ import annotations

import asyncio
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

from .envelope_helpers import flat


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
        # create_preview POSTs, which is not retried: a duplicate
        # generation would cost credits twice.
        assert mock_client.post.await_count == 1

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

        data = flat(result)
        assert data["ok"] is False
        assert data["error_type"] == "MissingAPIKey"
        assert "MESHY_API_KEY" in data["hint"]
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

        data = flat(result)
        assert data["ok"] is True
        assert data["task_id"] == "task-ok"
        assert data["vertex_count"] > 0
        assert data["face_count"] > 0

        # Session should have the shape
        sess = session.get_or_create()
        brep = sess.brep_path()
        assert brep.exists()

        # Provenance, not a synthetic code-history entry. Recording a
        # comment in the history meant a later execute_cad(append)
        # replayed a comment plus new code and silently destroyed the
        # mesh (CAD-022).
        part = sess.get_active_part()
        assert part.source == "ai_mesh"
        assert part.ai_prompt
        assert part.ai_glb_path
        assert part.code_history == []
        assert part.bbox is not None
        assert data["reproducible_from_code"] is False

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

        data = flat(result)
        assert data["ok"] is False
        assert data["error_type"] == "GenerationTimeout"
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

        data = flat(result)
        assert data["ok"] is False
        assert data["error_type"] == "MeshyAPIError"
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
        data = flat(result)
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

        data = flat(result)
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


def _mock_response(
    status: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    """Create a mock httpx.Response.

    A real `httpx.Response` always has `headers`, and the client reads
    `Retry-After` from them, so the fake carries them too.
    """

    # Bound outside the class body: a class attribute named `headers`
    # cannot read the enclosing function's `headers` parameter.
    header_map = dict(headers or {})

    class FakeResponse:
        status_code = status
        text = json.dumps(body)
        headers = header_map

        def json(self) -> dict[str, Any]:
            return body

        def raise_for_status(self) -> None:
            if status >= 400:
                msg = f"HTTP {status}"
                raise Exception(msg)

    return FakeResponse()


# ------------------------------------------------------------------
# CAD-027: download cap, host allowlist, monotonic clock, retry
# ------------------------------------------------------------------


def _streaming_client(chunks: list[bytes], headers: dict[str, str]) -> Any:
    """An AsyncMock client whose `.stream()` yields *chunks*."""

    header_map = dict(headers)

    class FakeStream:
        status_code = 200
        headers = header_map

        async def aiter_bytes(self, chunk_size: int = 0) -> Any:
            for chunk in chunks:
                yield chunk

    class Ctx:
        async def __aenter__(self) -> Any:
            return FakeStream()

        async def __aexit__(self, *exc: object) -> None:
            return None

    client = AsyncMock()
    client.stream = lambda *a, **kw: Ctx()
    return client


class TestDownloadSafety:
    @pytest.mark.anyio
    async def test_download_rejects_an_unexpected_host(
        self, tmp_path: Path
    ) -> None:
        """follow_redirects on an API-supplied URL is a fetch primitive."""
        from cad_mcp import meshy

        with pytest.raises(MeshyError) as exc:
            await meshy.download_glb(
                AsyncMock(),
                "https://evil.example.com/x.glb",
                tmp_path / "x.glb",
            )
        assert "unexpected host" in str(exc.value)
        assert not (tmp_path / "x.glb").exists()

    def test_download_accepts_meshy_and_its_cdn(self) -> None:
        from cad_mcp import meshy

        for url in (
            "https://assets.meshy.ai/x/model.glb",
            "https://meshy.ai/model.glb",
            "https://bucket.s3.amazonaws.com/model.glb",
            "https://d1.cloudfront.net/model.glb",
        ):
            meshy._check_download_host(url)

    @pytest.mark.anyio
    async def test_download_refuses_an_oversized_declared_length(
        self, tmp_path: Path
    ) -> None:
        from cad_mcp import meshy

        client = _streaming_client(
            chunks=[b"x" * 10],
            headers={"content-length": str(999 * 1024 * 1024)},
        )
        with pytest.raises(MeshyError) as exc:
            await meshy.download_glb(
                client,
                "https://assets.meshy.ai/big.glb",
                tmp_path / "big.glb",
                max_bytes=1024,
            )
        assert "limit" in str(exc.value)

    @pytest.mark.anyio
    async def test_download_stops_mid_stream_at_the_cap(
        self, tmp_path: Path
    ) -> None:
        """A content-length that understates the body must not get past."""
        from cad_mcp import meshy

        client = _streaming_client(chunks=[b"x" * 512] * 10, headers={})
        dest = tmp_path / "big.glb"
        with pytest.raises(MeshyError) as exc:
            await meshy.download_glb(
                client,
                "https://assets.meshy.ai/big.glb",
                dest,
                max_bytes=1024,
            )
        assert "exceeded" in str(exc.value)
        assert not dest.exists(), "a partial download was left behind"

    @pytest.mark.anyio
    async def test_download_writes_a_small_file(
        self, tmp_path: Path
    ) -> None:
        from cad_mcp import meshy

        client = _streaming_client(chunks=[b"glb-bytes"], headers={})
        dest = await meshy.download_glb(
            client, "https://assets.meshy.ai/ok.glb", tmp_path / "ok.glb"
        )
        assert dest.read_bytes() == b"glb-bytes"

    @pytest.mark.anyio
    async def test_empty_download_is_an_error(self, tmp_path: Path) -> None:
        from cad_mcp import meshy

        client = _streaming_client(chunks=[], headers={})
        with pytest.raises(MeshyError) as exc:
            await meshy.download_glb(
                client, "https://assets.meshy.ai/e.glb", tmp_path / "e.glb"
            )
        assert "empty" in str(exc.value)


class TestPollingBudget:
    @pytest.mark.anyio
    async def test_timeout_is_measured_against_a_monotonic_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slow responses used to extend the budget indefinitely.

        The old loop added only the sleep interval to `elapsed`, so a
        request that took 30 seconds counted as 3 seconds of the 120
        second budget.
        """
        from cad_mcp import meshy

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_mock_response(200, {"status": "PENDING"})
        )

        clock = {"t": 0.0}
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds: float) -> None:
            clock["t"] += 30.0
            await real_sleep(0)

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(
            type(loop), "time", lambda _self: clock["t"], raising=False
        )

        with pytest.raises(MeshyTimeout):
            await meshy.poll_until_done(client, "key", "task-1", 10.0)

        assert client.get.await_count <= 2, (
            f"polled {client.get.await_count} times on a 10 second budget"
        )


class TestRetry:
    @pytest.mark.anyio
    async def test_transient_5xx_is_retried_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cad_mcp import meshy

        monkeypatch.setattr(meshy, "RETRY_BASE_DELAY_S", 0.0)
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _mock_response(503, {"message": "unavailable"}),
                _mock_response(200, {"status": "SUCCEEDED"}),
            ]
        )

        resp = await meshy._get_with_retry(client, "https://x/y", {})
        assert resp.status_code == 200
        assert client.get.await_count == 2

    @pytest.mark.anyio
    async def test_4xx_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 400 will not become a 200; retrying only wastes time."""
        from cad_mcp import meshy

        monkeypatch.setattr(meshy, "RETRY_BASE_DELAY_S", 0.0)
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_mock_response(400, {"message": "bad prompt"})
        )

        resp = await meshy._get_with_retry(client, "https://x/y", {})
        assert resp.status_code == 400
        assert client.get.await_count == 1

    @pytest.mark.anyio
    async def test_retry_gives_up_with_a_structured_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from cad_mcp import meshy

        monkeypatch.setattr(meshy, "RETRY_BASE_DELAY_S", 0.0)
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(MeshyError) as exc:
            await meshy._get_with_retry(client, "https://x/y", {})
        assert "after 3 attempts" in str(exc.value)

    @pytest.mark.anyio
    async def test_429_hint_mentions_retry_after(self) -> None:
        from cad_mcp import meshy

        client = AsyncMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                429, {"message": "slow down"}, {"retry-after": "12"}
            )
        )
        with pytest.raises(MeshyError) as exc:
            await meshy.create_preview(client, "key", prompt="a cube")
        assert "12" in exc.value.hint


# ------------------------------------------------------------------
# CAD-028: mesh import is bounded
# ------------------------------------------------------------------


class TestSewBudget:
    @pytest.mark.anyio
    async def test_target_polycount_range_is_enforced(self) -> None:
        """The docstring said 100-15000; the code never checked."""
        for bad in (1, 99, 15001, 500_000):
            payload = flat(
                await mcp.call_tool(
                    "gen_ai_mesh",
                    {"prompt": "a pawn", "target_polycount": bad},
                )
            )
            assert payload["ok"] is False, f"{bad} was accepted"
            assert payload["error_type"] == "ValueError"

    @pytest.mark.anyio
    async def test_art_style_and_topology_are_validated(self) -> None:
        for args in (
            {"prompt": "x", "art_style": "cubist"},
            {"prompt": "x", "topology": "hexagon"},
        ):
            payload = flat(await mcp.call_tool("gen_ai_mesh", args))
            assert payload["ok"] is False
            assert payload["error_type"] == "ValueError"

    def test_dense_mesh_is_simplified_before_sewing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sewing is one OCP face per triangle in a Python loop."""
        import trimesh

        from cad_mcp import mesh_to_brep

        monkeypatch.setattr(mesh_to_brep, "MAX_SEW_TRIANGLES", 200)

        dense = trimesh.creation.icosphere(subdivisions=4)
        assert len(dense.faces) > 200
        glb = tmp_path / "dense.glb"
        dense.export(str(glb))

        stats = mesh_to_brep.glb_to_brep(glb, tmp_path / "dense.brep")

        assert stats["simplified"] is True
        assert stats["face_count"] <= 200
        assert stats["original_face_count"] > 200

    def test_small_mesh_is_not_simplified(self, tmp_path: Path) -> None:
        import trimesh

        from cad_mcp import mesh_to_brep

        glb = tmp_path / "cube.glb"
        trimesh.creation.box(extents=(2, 2, 2)).export(str(glb))
        stats = mesh_to_brep.glb_to_brep(glb, tmp_path / "cube.brep")

        assert stats["simplified"] is False
        assert stats["face_count"] == stats["original_face_count"]
