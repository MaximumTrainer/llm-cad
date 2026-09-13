"""End-to-end smoke test against a *deployed* cad-mcp (SPEC 10.4, FLY-7).

    uv run python scripts/smoke_remote.py --url https://cad-mcp.fly.dev
    uv run python scripts/smoke_remote.py --url http://127.0.0.1:8000 \
        --token dev-token --repeat 10

`scripts/smoke.py` calls the tools in-process, which proves the geometry
works and nothing about the deployment. This one goes over the wire, so
it also exercises the things only a real server has: bearer auth, session
headers, and -- the point of FLY-3 -- whether a second request lands on
the machine that holds the first one's model.

A green deploy with a broken render is the exact failure this exists to
catch, so the render step asserts that image *bytes* came back rather
than that the call returned.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from typing import Any

BRACKET = """
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 3)
    .faces(">Z")
    .workplane()
    .rect(40, 20, forConstruction=True)
    .vertices()
    .hole(4.5)
)
"""

# SPEC 5.1. A deploy that silently lost a tool is a broken deploy.
EXPECTED_TOOLS = {
    "ping",
    "execute_cad",
    "render_views",
    "validate_mesh",
    "measure",
    "export_model",
    "gen_ai_mesh",
    "list_session",
    "reset_session",
    "create_part",
    "set_active_part",
    "position_part",
    "list_parts",
    "delete_part",
}

_failures: list[str] = []


def _ok(msg: str) -> None:
    print(f"  [ok] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    _failures.append(msg)


def _text(result: Any) -> str:
    parts = []
    for block in result.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


def _images(result: Any) -> list[bytes]:
    out = []
    for block in result.content:
        if getattr(block, "type", None) == "image":
            out.append(base64.b64decode(block.data))
    return out


def _envelope_ok(result: Any) -> bool:
    """Read the tool's own `ok` flag rather than guessing from prose.

    Every tool answers in one envelope (CAD-019), so substring-matching
    the summary is both unnecessary and wrong: `execute_cad` reporting
    `ok: false` still contains the word "ok".
    """
    import json as _json

    text = _text(result).strip()
    try:
        payload = _json.loads(text)
    except _json.JSONDecodeError:
        # execute_cad answers in prose on success.
        return not text.lower().startswith(("error", "traceback"))
    return bool(payload.get("ok"))


async def _check_health(url: str) -> None:
    """A8: the deployment must *state* its backend, not leave it assumed.

    `/health` is unauthenticated by design -- a platform health check
    carries no token -- and 200 means the geometry kernel is up, not
    merely that the port is open.
    """
    import httpx

    print("\nStep 0: /health (unauthenticated, readiness)")
    # A machine woken from stop pays the kernel import before it is
    # ready, so a 503 here is expected briefly and fatal if it persists.
    deadline = asyncio.get_running_loop().time() + 90
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(f"{url}/health")
            if resp.status_code != 503:
                break
            if asyncio.get_running_loop().time() >= deadline:
                _fail(
                    "the deployment never became ready: /health still says "
                    f"{resp.text.strip()}"
                )
                return
            await asyncio.sleep(2)
    if resp.status_code != 200:
        _fail(f"/health returned {resp.status_code}: {resp.text[:200]}")
        return
    body = resp.json()
    _ok(f"ready: kernel={body.get('kernel')} version={body.get('version')}")
    backend = body.get("render_backend")
    if backend in ("pyrender", "matplotlib"):
        _ok(f"render backend in production: {backend}")
    else:
        _fail(f"/health did not name a render backend: {body}")


async def _check_unauthenticated(url: str) -> None:
    """A2/A3: no bearer token must mean 401, not a working session."""
    import httpx

    print("\nStep 1: unauthenticated request is refused")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{url}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
    if resp.status_code == 401:
        _ok("401 without a bearer token")
    elif resp.status_code == 200:
        _fail(
            "the server accepted an unauthenticated request -- either "
            "CAD_MCP_AUTH_TOKEN is unset on the deployment, or auth is "
            "not wired into this transport"
        )
    else:
        _ok(f"unauthenticated request refused with {resp.status_code}")


async def _core_loop(url: str, token: str | None, repeat: int) -> None:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # The SDK takes a whole httpx client rather than a headers mapping,
    # so the bearer token rides on that.
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Generous: a cold machine pays the ~3.3s CadQuery import, and a
    # render on a shared vCPU is not instant.
    http_client = httpx.AsyncClient(headers=headers, timeout=180)

    print("\nStep 2: initialize + list tools")
    # The transport yields (read, write); this SDK version does not hand
    # back a session-id accessor, so read it off the client's own headers.
    async with http_client, streamable_http_client(
        f"{url}/mcp", http_client=http_client
    ) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        _ok("initialized")

        listed = {t.name for t in (await session.list_tools()).tools}
        missing = EXPECTED_TOOLS - listed
        if missing:
            _fail(f"tools missing from the deployment: {sorted(missing)}")
        else:
            _ok(f"all {len(EXPECTED_TOOLS)} SPEC 5.1 tools present")

        # FLY-3: each iteration is a fresh pair of HTTP requests on one
        # session. If affinity is broken, execute_cad lands on one
        # machine and render_views on another, and the render finds no
        # model -- which is why the render is repeated rather than done
        # once.
        print(f"\nStep 3: core loop x{repeat} (affinity under load)")
        for attempt in range(1, repeat + 1):
            result = await session.call_tool("execute_cad", {"code": BRACKET})
            if not _envelope_ok(result):
                _fail(
                    f"iteration {attempt}: execute_cad failed: "
                    f"{_text(result)[:500]}"
                )
                break

            rendered = await session.call_tool("render_views", {})
            images = _images(rendered)
            if not images:
                _fail(
                    f"iteration {attempt}: render_views returned no image "
                    f"content -- the state written by execute_cad was not "
                    f"visible to this request (session affinity?): "
                    f"{_text(rendered)[:300]}"
                )
                break
            if not any(img.startswith(b"\x89PNG") for img in images):
                _fail(f"iteration {attempt}: image content is not a PNG")
                break
        else:
            _ok(f"{repeat}/{repeat} iterations returned a PNG render")

        print("\nStep 4: validate + export")
        validated = await session.call_tool("validate_mesh", {})
        if "watertight" in _text(validated).lower():
            _ok("validate_mesh reported on watertightness")
        else:
            _fail(f"validate_mesh said: {_text(validated)[:300]}")

        for fmt in ("stl", "step", "3mf", "glb"):
            exported = await session.call_tool(
                "export_model", {"format": fmt, "filename": "smoke"}
            )
            if _envelope_ok(exported):
                _ok(f"exported {fmt}")
            else:
                _fail(f"export {fmt} failed: {_text(exported)[:300]}")


def _explain(exc: BaseException, depth: int = 0) -> list[str]:
    """Flatten an exception, including anyio's TaskGroup ExceptionGroups.

    "unhandled errors in a TaskGroup (1 sub-exception)" tells nobody
    anything, and saying what broke is this script's whole job.
    """
    prefix = "  " * depth
    inner = getattr(exc, "exceptions", None)
    if inner:
        out = [f"{prefix}{type(exc).__name__}:"]
        for sub in inner:
            out.extend(_explain(sub, depth + 1))
        return out
    out = [f"{prefix}{type(exc).__name__}: {exc}"]
    if exc.__cause__ is not None:
        out.extend(_explain(exc.__cause__, depth + 1))
    return out


async def _main(url: str, token: str | None, repeat: int) -> int:
    url = url.rstrip("/")
    print(f"cad-mcp remote smoke test against {url}")

    await _check_health(url)

    if token:
        await _check_unauthenticated(url)
    else:
        print("\nStep 1: skipped (no --token given, so 401 cannot be asserted)")

    try:
        await _core_loop(url, token, repeat)
    except Exception as exc:
        for line in _explain(exc):
            _fail(line)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("PASSED: the full core loop works against the deployment")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="base URL, no /mcp suffix")
    parser.add_argument("--token", default=None, help="CAD_MCP_AUTH_TOKEN")
    parser.add_argument(
        "--repeat",
        type=int,
        default=10,
        help="core-loop iterations; >1 is what makes FLY-3 meaningful",
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.url, args.token, args.repeat))


if __name__ == "__main__":
    sys.exit(main())
