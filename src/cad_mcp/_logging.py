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
import sys
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger("cad-mcp")


def setup() -> None:
    """Configure the cad-mcp logger to emit structured JSON to stderr."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


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
        return json.dumps(entry)


def log_tool_call(
    tool_name: str,
    session_id: str,
    duration_s: float,
    success: bool,
) -> None:
    """Emit a structured log line for a completed tool call."""
    extra = {
        "tool": tool_name,
        "session_id": session_id,
        "duration_ms": round(duration_s * 1000, 1),
        "success": success,
    }
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

                t0 = time.perf_counter()
                success = True
                try:
                    return await fn(*args, **kwargs)
                except Exception:
                    success = False
                    raise
                finally:
                    elapsed = time.perf_counter() - t0
                    log_tool_call(
                        tool_name, _session_id(sess_mod, kwargs), elapsed,
                        success,
                    )

            return async_wrapper

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from cad_mcp import session as sess_mod

            t0 = time.perf_counter()
            success = True
            try:
                return fn(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                elapsed = time.perf_counter() - t0
                log_tool_call(
                    tool_name, _session_id(sess_mod, kwargs), elapsed, success,
                )

        return wrapper

    return decorator
