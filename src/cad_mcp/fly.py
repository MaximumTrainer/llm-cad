"""Session affinity for a multi-machine Fly.io deployment (SPEC 10.4).

MCP's streamable HTTP transport is stateful. The server issues an
``Mcp-Session-Id`` and every later request for that session must reach
the *same* process, because the session's parts, code history and cached
``.brep`` files live in a temp directory on one machine's local disk
(``session.py``). An anycast proxy in front of several machines will
happily send the second request somewhere else, and the model vanishes
mid-conversation.

``CAD_MCP_STATELESS=1`` is not a way out: it breaks the core loop
outright, because ``render_views`` and ``export_model`` read state that a
previous ``execute_cad`` wrote.

MCP clients know nothing about Fly and will not send routing headers, so
affinity has to be established server-side. This middleware does it by
**wrapping the session id**: the id handed to the client is
``<machine-id>~<sdk-id>``, and the wrapper is stripped again before the
request reaches the MCP app, which therefore never learns it happened.
Any machine can then read the owner straight off the header with no
shared store and no lookup:

* the request is for a session this machine owns -> strip and serve;
* it is for another machine -> answer with ``fly-replay: instance=<id>``
  and let Fly's proxy re-run the request there, invisibly to the client;
* the owning machine is gone -> a JSON-RPC error telling the client to
  re-initialize, rather than a 500 or a silently empty session.

Outside Fly (``FLY_MACHINE_ID`` unset) the middleware is a pass-through,
so local stdio and HTTP behave exactly as before.
"""
from __future__ import annotations

import json
import os
from typing import Any

# `~` is in neither a UUID hex nor a Fly machine id, and is unreserved in
# a URI, so it can never appear inside either half.
SEPARATOR = "~"

_SESSION_HEADER = b"mcp-session-id"
_REPLAY_HEADER = b"fly-replay"
# Fly sets this on a request it has already replayed once. Seeing it
# means the bounce did not help, so bouncing again would loop.
_REPLAY_SRC_HEADER = b"fly-replay-src"

# JSON-RPC: "Invalid Request". The MCP spec has the client re-initialize
# when its session is no longer recognised.
_SESSION_GONE_CODE = -32600


def machine_id() -> str | None:
    """This machine's Fly id, or None when not running on Fly."""
    return os.environ.get("FLY_MACHINE_ID") or None


def wrap_session_id(raw: str, machine: str) -> str:
    return f"{machine}{SEPARATOR}{raw}"


def unwrap_session_id(value: str) -> tuple[str | None, str]:
    """Split a wrapped id into ``(owning machine, underlying id)``.

    An unwrapped id yields ``(None, value)``: sessions created before a
    deploy that added the wrapper, or by a client talking to a
    single-machine deployment, still work.
    """
    owner, sep, rest = value.partition(SEPARATOR)
    if not sep:
        return None, value
    return owner, rest


class FlyReplayMiddleware:
    """Pure-ASGI middleware; see the module docstring.

    Pure ASGI rather than Starlette's ``BaseHTTPMiddleware`` on purpose:
    the MCP endpoint streams SSE, and ``BaseHTTPMiddleware`` buffers
    responses through a queue, which would add latency to exactly the
    long-lived responses the render loop depends on.
    """

    def __init__(self, app: Any, machine: str | None = None) -> None:
        self.app = app
        self._machine = machine if machine is not None else machine_id()

    async def __call__(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None:
        if scope["type"] != "http" or not self._machine:
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
        incoming = _header(headers, _SESSION_HEADER)

        if incoming is not None:
            owner, underlying = unwrap_session_id(incoming)
            if owner is not None and owner != self._machine:
                if _header(headers, _REPLAY_SRC_HEADER) is not None:
                    # Already bounced once and still not ours: the owner
                    # is gone. Say so in a way the client can act on.
                    await _send_session_gone(send, owner)
                    return
                await _send_replay(send, owner)
                return
            # Ours (or unwrapped): hand the MCP app the id it issued.
            scope = dict(scope)
            scope["headers"] = _replace_header(
                headers, _SESSION_HEADER, underlying.encode("latin-1")
            )

        machine = self._machine

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                out = list(message.get("headers") or [])
                issued = _header(out, _SESSION_HEADER)
                if issued is not None:
                    existing_owner, _ = unwrap_session_id(issued)
                    if existing_owner is None:
                        message = dict(message)
                        message["headers"] = _replace_header(
                            out,
                            _SESSION_HEADER,
                            wrap_session_id(issued, machine).encode("latin-1"),
                        )
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ------------------------------------------------------------------
# Header helpers. ASGI headers are a list of (lowercase bytes, bytes).
# ------------------------------------------------------------------


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _replace_header(
    headers: list[tuple[bytes, bytes]], name: bytes, value: bytes
) -> list[tuple[bytes, bytes]]:
    out = [(k, v) for k, v in headers if k.lower() != name]
    out.append((name, value))
    return out


async def _send_replay(send: Any, owner: str) -> None:
    """Ask Fly's proxy to re-run this request on the owning machine.

    The client never sees this: the proxy replays the request and returns
    the real response. The body is only a fallback for a proxy that does
    not honour the header.
    """
    body = json.dumps(
        {
            "error": "session belongs to another instance",
            "instance": owner,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 409,
            "headers": [
                (_REPLAY_HEADER, f"instance={owner}".encode("latin-1")),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_session_gone(send: Any, owner: str) -> None:
    """A clean MCP-level error, not a 500 (FLY-3)."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": _SESSION_GONE_CODE,
                "message": (
                    f"The machine holding this session ({owner}) is no "
                    f"longer running. Re-initialize to start a new "
                    f"session; model code can be replayed into it."
                ),
            },
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
