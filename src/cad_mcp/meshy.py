"""Async Meshy API client for text-to-3D generation (SPEC 10.2).

Handles the two-stage workflow: preview (geometry) then optional refine
(textures).  All network calls use httpx with Bearer token auth.
"""
from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

BASE_URL = "https://api.meshy.ai/openapi/v2/text-to-3d"

POLL_INTERVAL_S = 3
PREVIEW_TIMEOUT_S = 120
REFINE_TIMEOUT_S = 180

# A generated GLB is a few MB. The cap stops an unexpected response — or a
# redirect to something else entirely — being pulled into memory whole.
MAX_DOWNLOAD_BYTES = (
    int(os.environ.get("CAD_MCP_MESHY_MAX_MB", "128")) * 1024 * 1024
)

# Only these hosts are fetched. follow_redirects=True on a URL that came
# from an API response is otherwise an open fetch primitive.
ALLOWED_DOWNLOAD_HOSTS = (
    "meshy.ai",
    "amazonaws.com",
    "cloudfront.net",
)

# Transient failures get a bounded retry: one blip used to fail the whole
# generation after the user had already spent credits.
MAX_RETRIES = 3
RETRY_BASE_DELAY_S = 1.0
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


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
    """Poll a task until SUCCEEDED or FAILED.

    Timed against a monotonic clock. Counting only the sleeps ignored
    request latency, so real elapsed time could far exceed the budget
    SPEC 10.2 M3 specifies (CAD-027).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        resp = await _get_with_retry(
            client, f"{BASE_URL}/{task_id}", _auth_header(api_key)
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

        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL_S, remaining))

    raise MeshyTimeout(task_id)


def _check_download_host(url: str) -> None:
    """Refuse to fetch from anywhere but Meshy and its CDN."""
    host = (urlparse(url).hostname or "").lower()
    allowed = any(
        host == name or host.endswith("." + name)
        for name in ALLOWED_DOWNLOAD_HOSTS
    )
    if not host or not allowed:
        raise MeshyError(
            0,
            f"Refusing to download from unexpected host {host!r}",
            "Meshy model URLs should be on meshy.ai or its CDN.",
        )


async def download_glb(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Stream a GLB to disk, bounded in size and restricted by host.

    resp.content on an arbitrary URL with follow_redirects=True read the
    whole body into memory with no size cap at all (CAD-027).
    """
    _check_download_host(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit_mb = max_bytes // (1024 * 1024)

    written = 0
    async with client.stream("GET", url, follow_redirects=True) as resp:
        if resp.status_code >= 400:
            raise MeshyError(
                resp.status_code,
                f"Download failed with HTTP {resp.status_code}",
                "Retry, or regenerate the model.",
            )

        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise MeshyError(
                0,
                f"Model is {int(declared) // (1024 * 1024)}MB, over the "
                f"{limit_mb}MB limit",
                "Lower target_polycount, or raise CAD_MCP_MESHY_MAX_MB.",
            )

        with dest.open("wb") as handle:
            async for chunk in resp.aiter_bytes(chunk_size=1 << 16):
                written += len(chunk)
                if written > max_bytes:
                    handle.close()
                    dest.unlink(missing_ok=True)
                    raise MeshyError(
                        0,
                        f"Download exceeded the {limit_mb}MB limit",
                        "Lower target_polycount, or raise "
                        "CAD_MCP_MESHY_MAX_MB.",
                    )
                handle.write(chunk)

    if written == 0:
        dest.unlink(missing_ok=True)
        raise MeshyError(0, "Downloaded model was empty", "Retry.")
    return dest


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    """GET with bounded exponential backoff on transient failures.

    4xx is never retried; it will not become a 200. 429 and 5xx are,
    honouring Retry-After when present.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt == MAX_RETRIES - 1:
                break
            await _backoff(attempt, None)
            continue

        transient = resp.status_code in RETRYABLE_STATUS or (
            resp.status_code == 429
        )
        if transient and attempt < MAX_RETRIES - 1:
            await _backoff(attempt, resp.headers.get("retry-after"))
            continue
        return resp

    raise MeshyError(
        0,
        f"Meshy request failed after {MAX_RETRIES} attempts: {last_error}",
        "Check connectivity and retry.",
    )


async def _backoff(attempt: int, retry_after: str | None) -> None:
    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), 30.0))
            return
        except ValueError:
            pass
    await asyncio.sleep(
        RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, 0.25)
    )


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _check_response(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after", "")
        suffix = f" Retry after {retry_after}s." if retry_after else ""
        raise MeshyError(
            429,
            "Rate limited by the Meshy API",
            f"Rate limited, wait and retry.{suffix}",
        )
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
