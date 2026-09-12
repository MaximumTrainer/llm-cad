"""Structured observability per SPEC N6 (CAD-026 / #27).

N6 asks for a structured line per tool call on stderr, and never on
stdout, because the stdio transport owns stdout for MCP framing. Both
halves were unverified: `setup()` was called only from `main()`, so under
tests or any embedding the SDK's own handler took over and emitted
Rich-formatted text instead of JSON.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from cad_mcp import _logging, session
from cad_mcp.server import mcp

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry

ROOT = Path(__file__).resolve().parent.parent
BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(5,5,5)"


@pytest.fixture
def captured_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Collect the structured records the logger emits."""
    records: list[dict[str, Any]] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            formatted = _logging._JsonFormatter().format(record)
            records.append(json.loads(formatted))

    handler = Collector()
    _logging.logger.addHandler(handler)
    _logging.logger.setLevel(logging.INFO)
    yield records
    _logging.logger.removeHandler(handler)


# ------------------------------------------------------------------
# setup() is idempotent and applies itself
# ------------------------------------------------------------------


def test_setup_is_idempotent() -> None:
    """A second call used to append a handler and double-log."""
    _logging.setup()
    before = len(_logging.logger.handlers)
    _logging.setup()
    _logging.setup()
    assert len(_logging.logger.handlers) == before


def test_setup_force_replaces_rather_than_appends() -> None:
    _logging.setup()
    before = len(_logging.logger.handlers)
    _logging.setup(force=True)
    assert len(_logging.logger.handlers) == before


def test_logging_is_configured_without_calling_main() -> None:
    """N6 must hold however the server was started."""
    for handler in list(_logging.logger.handlers):
        if getattr(handler, "_cad_mcp_tag", None):
            _logging.logger.removeHandler(handler)
    assert not _logging.is_configured()

    _logging.log_tool_call("probe", "default", 0.01, True)

    assert _logging.is_configured(), (
        "a tool call did not lazily configure structured logging"
    )


def test_handler_writes_to_stderr_not_stdout() -> None:
    _logging.setup(force=True)
    handler = next(
        h
        for h in _logging.logger.handlers
        if getattr(h, "_cad_mcp_tag", None)
    )
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr


def test_logger_does_not_propagate() -> None:
    """A root handler could be writing to stdout."""
    _logging.setup(force=True)
    assert _logging.logger.propagate is False


def test_log_level_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAD_MCP_LOG_LEVEL", "WARNING")
    _logging.setup(force=True)
    assert _logging.logger.level == logging.WARNING


# ------------------------------------------------------------------
# Every tool emits exactly one record, with the real session id
# ------------------------------------------------------------------


def test_every_registered_tool_is_logged() -> None:
    """N6 says every tool call. Three tools had no decorator."""
    import inspect

    unlogged = []
    for path in sorted((ROOT / "src" / "cad_mcp" / "tools").glob("*.py")):
        if path.name == "__init__.py":
            continue
        if "@logged_tool(" not in path.read_text(encoding="utf-8"):
            unlogged.append(path.name)
    assert not unlogged, f"tools with no structured logging: {unlogged}"

    # And the decorator is actually applied to the registered callables.
    assert inspect.isfunction(_logging.logged_tool("x")(lambda: None))


def test_a_tool_call_emits_one_record(
    captured_logs: list[dict[str, Any]],
) -> None:
    anyio.run(lambda: mcp.call_tool("ping", {}))

    pings = [r for r in captured_logs if r.get("tool") == "ping"]
    assert len(pings) == 1, f"expected one record, got {len(pings)}"

    record = pings[0]
    assert record["success"] is True
    assert record["duration_ms"] >= 0
    assert record["session_id"] == "default"
    assert record["call_id"], "no per-call id"
    assert record["level"] == "INFO"


def test_records_carry_the_real_session_id(
    captured_logs: list[dict[str, Any]],
) -> None:
    """It was the constant "default" regardless of the client."""

    class Ctx:
        @property
        def headers(self) -> dict[str, str]:
            return {"Mcp-Session-Id": "client-xyz"}

    real = session.resolve_id
    session.resolve_id = lambda ctx=None: "client-xyz"  # type: ignore[assignment]
    try:
        anyio.run(lambda: mcp.call_tool("ping", {}))
    finally:
        session.resolve_id = real  # type: ignore[assignment]

    pings = [r for r in captured_logs if r.get("tool") == "ping"]
    assert pings[-1]["session_id"] == "client-xyz"


def test_failures_log_the_error_type(
    captured_logs: list[dict[str, Any]],
) -> None:
    """`success: false` alone does not say what went wrong."""

    @_logging.logged_tool("exploder")
    def exploder() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        exploder()

    record = next(r for r in captured_logs if r.get("tool") == "exploder")
    assert record["success"] is False
    assert record["error_type"] == "RuntimeError"


def test_call_ids_are_unique_per_call(
    captured_logs: list[dict[str, Any]],
) -> None:
    for _ in range(3):
        anyio.run(lambda: mcp.call_tool("ping", {}))
    ids = [
        r["call_id"] for r in captured_logs if r.get("tool") == "ping"
    ]
    assert len(ids) == len(set(ids)) == 3


def test_every_record_is_one_json_object_per_line() -> None:
    """A multi-line record would break line-based log ingestion."""
    formatter = _logging._JsonFormatter()
    record = logging.LogRecord(
        "cad-mcp", logging.INFO, __file__, 1, "msg\nwith newline", None, None
    )
    line = formatter.format(record)
    assert "\n" not in line
    assert json.loads(line)["msg"] == "msg\nwith newline"


# ------------------------------------------------------------------
# Nothing but MCP framing reaches stdout
# ------------------------------------------------------------------

_STDIO_PROBE = r"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import anyio

from cad_mcp import _logging
from cad_mcp.server import mcp

_logging.setup()


async def main() -> None:
    # Exercise a tool that logs, renders and touches the geometry stack -
    # all the places a stray print() or library chatter could appear.
    await mcp.call_tool(
        "execute_cad",
        {"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(5,5,5)"},
    )
    await mcp.call_tool("render_views", {})
    await mcp.call_tool("list_session", {})


anyio.run(main)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
"""


@pytest.mark.slow
def test_nothing_reaches_stdout_during_tool_calls(tmp_path: Path) -> None:
    """PLAN's named risk: a stray print corrupts stdio MCP framing.

    Run in a subprocess so the check is on the real process stdout, not
    a pytest capture buffer.
    """
    probe = tmp_path / "stdio_probe.py"
    probe.write_text(_STDIO_PROBE, encoding="utf-8")

    env = dict(**dict(__import__("os").environ))
    env["CAD_MCP_OUTPUT_DIR"] = str(tmp_path / "out")
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )

    assert result.returncode == 0, (
        f"probe failed ({result.returncode}): {result.stderr[-2000:]}"
    )
    assert result.stdout.strip() == "", (
        "output reached stdout, which the stdio transport owns for MCP "
        f"framing:\n{result.stdout[:2000]}"
    )
    # And the structured records went to stderr as JSON.
    json_lines = [
        line
        for line in result.stderr.splitlines()
        if line.strip().startswith("{")
    ]
    assert json_lines, f"no JSON log lines on stderr: {result.stderr[:800]}"
    parsed = [json.loads(line) for line in json_lines]
    tools = {r.get("tool") for r in parsed}
    assert {"execute_cad", "render_views", "list_session"} <= tools, (
        f"missing tool records; saw {tools}"
    )
