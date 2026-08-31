"""Async Meshy API client for text-to-3D generation (SPEC 10.2).

Handles the two-stage workflow: preview (geometry) then optional refine
(textures).  All network calls use httpx with Bearer token auth.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.meshy.ai/openapi/v2/text-to-3d"

POLL_INTERVAL_S = 3
PREVIEW_TIMEOUT_S = 120
REFINE_TIMEOUT_S = 180


class MeshyError(Exception):
    """Raised on Meshy API errors with structured details."""

    def __init__(self, status: int, message: str, hint: str) -> None:
        super().__init__(message)
        self.status = status
        self.hint = hint


class MeshyTimeout(Exception):
    """Raised when a task does not complete within its timeout."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task {task_id} timed out")
        self.task_id = task_id


@dataclass
class MeshyResult:
    task_id: str
    status: str
    model_urls: dict[str, str]
    thumbnail_url: str
    polycount: int


async def create_preview(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    prompt: str,
    negative_prompt: str = "",
    art_style: str = "realistic",
    topology: str = "triangle",
    target_polycount: int = 4000,
    ai_model: str = "latest",
) -> str:
    """Create a preview task. Returns the task ID."""
    body: dict[str, Any] = {
        "mode": "preview",
        "prompt": prompt,
        "ai_model": ai_model,
        "topology": topology,
        "target_polycount": target_polycount,
        "target_formats": ["glb"],
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    if art_style in ("realistic", "sculpture"):
        body["art_style"] = art_style

    resp = await client.post(
        BASE_URL,
        json=body,
        headers=_auth_header(api_key),
    )
    _check_response(resp)
    return str(resp.json()["result"])


async def create_refine(
    client: httpx.AsyncClient,
    api_key: str,
    preview_task_id: str,
) -> str:
    """Create a refine task from a completed preview. Returns the task ID."""
    resp = await client.post(
        BASE_URL,
        json={"mode": "refine", "preview_task_id": preview_task_id},
        headers=_auth_header(api_key),
    )
    _check_response(resp)
    return str(resp.json()["result"])


async def poll_until_done(
    client: httpx.AsyncClient,
    api_key: str,
    task_id: str,
    timeout_s: float,
) -> MeshyResult:
    """Poll a task until SUCCEEDED or FAILED."""
    elapsed = 0.0
    while elapsed < timeout_s:
        resp = await client.get(
            f"{BASE_URL}/{task_id}",
            headers=_auth_header(api_key),
        )
        _check_response(resp)
        data = resp.json()
        status = data.get("status", "UNKNOWN")

        if status == "SUCCEEDED":
            return MeshyResult(
                task_id=task_id,
                status=status,
                model_urls=data.get("model_urls", {}),
                thumbnail_url=data.get("thumbnail_url", ""),
                polycount=data.get("polycount", 0),
            )
        if status == "FAILED":
            msg = data.get("message", "Task failed")
            raise MeshyError(500, msg, "Try a different prompt or retry")

        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S

    raise MeshyTimeout(task_id)


async def download_glb(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
) -> Path:
    """Download a GLB file from a model URL."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _check_response(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise MeshyError(429, "Rate limited", "Rate limited, wait and retry")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise MeshyError(
            resp.status_code,
            f"Meshy API error: {detail}",
            f"HTTP {resp.status_code}. Check prompt and API key.",
        )
