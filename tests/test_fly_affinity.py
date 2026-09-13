"""SPEC 10.4: an MCP session must keep reaching the machine that owns it.

The state that makes the core loop work -- parts, code history, the
cached `.brep` -- lives on one machine's local disk, so a request routed
to a different instance does not see a slower model, it sees no model.
These tests drive the middleware as raw ASGI, which means the whole
routing decision is exercised without Fly, without a container and
without a network.

No geometry marker anywhere in this file: none of it builds anything.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from cad_mcp.fly import (
    SEPARATOR,
    FlyReplayMiddleware,
    unwrap_session_id,
    wrap_session_id,
)

OURS = "148e392a7d1685"
THEIRS = "3d8d9214b44e83"
RAW = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"


class Recorder:
    """A minimal ASGI app that records what it was handed."""

    def __init__(self, issue_session: str | None = None) -> None:
        self.seen_headers: list[tuple[bytes, bytes]] = []
        self.called = False
        self._issue = issue_session

    async def __call__(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None:
        self.called = True
        self.seen_headers = list(scope["headers"])
        headers = [(b"content-type", b"application/json")]
        if self._issue:
            headers.append((b"mcp-session-id", self._issue.encode()))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})


async def _call(
    app: Any, headers: list[tuple[bytes, bytes]]
) -> tuple[int, dict[bytes, bytes], bytes]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(
        {"type": "http", "method": "POST", "path": "/mcp", "headers": headers},
        receive,
        send,
    )
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return start["status"], {k.lower(): v for k, v in start["headers"]}, body


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode()
    return None


# ------------------------------------------------------------------
# The id wrapper
# ------------------------------------------------------------------


def test_wrap_and_unwrap_round_trip() -> None:
    assert unwrap_session_id(wrap_session_id(RAW, OURS)) == (OURS, RAW)


def test_an_unwrapped_id_has_no_owner() -> None:
    """Sessions issued before the wrapper shipped must keep working."""
    assert unwrap_session_id(RAW) == (None, RAW)


def test_the_separator_cannot_occur_in_either_half() -> None:
    """Otherwise `partition` would split an id in the wrong place."""
    assert SEPARATOR not in RAW
    assert SEPARATOR not in OURS


# ------------------------------------------------------------------
# Routing
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_off_fly_the_middleware_is_a_pass_through() -> None:
    """A laptop has no machine id and must behave exactly as before."""
    app = Recorder(issue_session=RAW)
    status, headers, _ = await _call(FlyReplayMiddleware(app, None), [])

    assert status == 200
    assert app.called
    assert headers[b"mcp-session-id"] == RAW.encode()


@pytest.mark.anyio
async def test_a_new_session_id_is_stamped_with_this_machine() -> None:
    app = Recorder(issue_session=RAW)
    _, headers, _ = await _call(FlyReplayMiddleware(app, OURS), [])

    assert headers[b"mcp-session-id"].decode() == f"{OURS}{SEPARATOR}{RAW}"


@pytest.mark.anyio
async def test_our_own_session_reaches_the_app_unwrapped() -> None:
    """The MCP app must never see the wrapper it did not issue."""
    app = Recorder()
    status, _, _ = await _call(
        FlyReplayMiddleware(app, OURS),
        [(b"mcp-session-id", wrap_session_id(RAW, OURS).encode())],
    )

    assert status == 200
    assert _header(app.seen_headers, b"mcp-session-id") == RAW


@pytest.mark.anyio
async def test_another_machines_session_is_replayed_not_served() -> None:
    """Serving it locally is the bug: it would look like an empty session."""
    app = Recorder()
    status, headers, _ = await _call(
        FlyReplayMiddleware(app, OURS),
        [(b"mcp-session-id", wrap_session_id(RAW, THEIRS).encode())],
    )

    assert not app.called, "the request was served by the wrong machine"
    assert headers[b"fly-replay"] == f"instance={THEIRS}".encode()
    assert status == 409


@pytest.mark.anyio
async def test_an_unwrapped_session_is_served_locally() -> None:
    """No owner encoded means nothing to route to; bouncing would loop."""
    app = Recorder()
    status, _, _ = await _call(
        FlyReplayMiddleware(app, OURS), [(b"mcp-session-id", RAW.encode())]
    )

    assert status == 200
    assert app.called
    assert _header(app.seen_headers, b"mcp-session-id") == RAW


@pytest.mark.anyio
async def test_a_dead_owner_gives_a_clean_error_not_a_loop() -> None:
    """FLY-3: a vanished machine must not produce a 500 or a replay loop.

    Fly marks a request it has already replayed. Seeing that mark and
    still not owning the session means the owner is gone.
    """
    app = Recorder()
    status, _, body = await _call(
        FlyReplayMiddleware(app, OURS),
        [
            (b"mcp-session-id", wrap_session_id(RAW, THEIRS).encode()),
            (b"fly-replay-src", b"instance=somewhere"),
        ],
    )

    assert not app.called
    assert status == 404
    assert status < 500, "a gone session is not a server fault"
    payload = json.loads(body)
    assert payload["error"]["code"] == -32600
    assert "re-initialize" in payload["error"]["message"].lower()


@pytest.mark.anyio
async def test_a_request_with_no_session_id_is_untouched() -> None:
    """Initialization carries no session id and must reach the app."""
    app = Recorder(issue_session=RAW)
    status, _, _ = await _call(
        FlyReplayMiddleware(app, OURS), [(b"content-type", b"application/json")]
    )

    assert status == 200
    assert app.called


@pytest.mark.anyio
async def test_a_lifespan_scope_passes_straight_through() -> None:
    """Only HTTP scopes carry session ids; anything else must not break."""
    seen: list[str] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def noop(*_: Any) -> Any:
        return {}

    await FlyReplayMiddleware(app, OURS)(
        {"type": "lifespan"}, noop, noop
    )
    assert seen == ["lifespan"]
