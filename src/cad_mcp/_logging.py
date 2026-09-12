"""Structured stderr logging for cad-mcp (SPEC N6).

Every tool call emits a JSON line to stderr with duration, success,
session id, and tool name.  Never writes to stdout (stdio transport
owns stdout).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger("cad-mcp")


_HANDLER_TAG = "cad-mcp-json"


def setup(force: bool = False) -> None:
    """Attach the structured stderr handler. Safe to call repeatedly.

    Previously this was called only from ``main()``, so under tests, the
    smoke script, or any embedding the SDK's own handler took over and
    output was Rich-formatted text rather than JSON — SPEC N6 held only
    when the server happened to be started from the CLI. It also appended
    a handler on every call, so a second call double-logged every line
    (CAD-026).
    """
    for existing in logger.handlers:
        if getattr(existing, "_cad_mcp_tag", None) == _HANDLER_TAG:
            if not force:
                return
            logger.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    handler._cad_mcp_tag = _HANDLER_TAG  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(
        os.environ.get("CAD_MCP_LOG_LEVEL", "INFO").upper()
    )
    # stdout belongs to the MCP stdio framing; never propagate to a root
    # handler that might be writing there.
    logger.propagate = False


def is_configured() -> bool:
    """Whether the structured handler is attached."""
    return any(
        getattr(h, "_cad_mcp_tag", None) == _HANDLER_TAG
        for h in logger.handlers
    )


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "tool"):
            entry["tool"] = record.tool
        if hasattr(record, "session_id"):
            entry["session_id"] = record.session_id
        if hasattr(record, "duration_ms"):
            entry["duration_ms"] = record.duration_ms
        if hasattr(record, "success"):
            entry["success"] = record.success
        if hasattr(record, "call_id"):
            entry["call_id"] = record.call_id
        if hasattr(record, "error_type"):
            entry["error_type"] = record.error_type
        return json.dumps(entry)


def log_tool_call(
    tool_name: str,
    session_id: str,
    duration_s: float,
    success: bool,
    call_id: str = "",
    error_type: str = "",
) -> None:
    """Emit a structured log line for a completed tool call."""
    # Applied lazily so SPEC N6 holds however the server was started.
    if not is_configured():
        setup()

    extra: dict[str, Any] = {
        "tool": tool_name,
        "session_id": session_id,
        "duration_ms": round(duration_s * 1000, 1),
        "success": success,
    }
    if call_id:
        extra["call_id"] = call_id
    if error_type:
        extra["error_type"] = error_type
    logger.info(
        "%s completed in %.1fms",
        tool_name,
        duration_s * 1000,
        extra=extra,
    )


def _session_id(sess_mod: Any, kwargs: dict[str, Any]) -> str:
    """Real session id for the call, not a constant.

    The previous implementation called `get_or_create()` with no argument,
    so every log line said "default" regardless of which client made the
    request (CAD-007/CAD-026).
    """
    sid: str = "default"
    with contextlib.suppress(Exception):
        sid = str(sess_mod.resolve_id(kwargs.get("ctx")))
    return sid


def logged_tool(tool_name: str) -> Callable[..., Any]:
    """Decorator that wraps a tool function with structured logging.

    Supports both sync and async tool functions.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from cad_mcp import session as sess_mod

                call_id = uuid.uuid4().hex[:8]
                t0 = time.perf_counter()
                success = True
                error_type = ""
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    success = False
                    error_type = type(exc).__name__
                    raise
                finally:
                    log_tool_call(
                        tool_name,
                        _session_id(sess_mod, kwargs),
                        time.perf_counter() - t0,
                        success,
                        call_id,
                        error_type,
                    )

            return async_wrapper

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from cad_mcp import session as sess_mod

            call_id = uuid.uuid4().hex[:8]
            t0 = time.perf_counter()
            success = True
            error_type = ""
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                success = False
                error_type = type(exc).__name__
                raise
            finally:
                log_tool_call(
                    tool_name,
                    _session_id(sess_mod, kwargs),
                    time.perf_counter() - t0,
                    success,
                    call_id,
                    error_type,
                )

        return wrapper

    return decorator
