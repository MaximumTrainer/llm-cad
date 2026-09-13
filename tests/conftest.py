"""Shared test fixtures.

Two things every test needs and none should have to remember:

* an isolated export directory. Exports are now durable (CAD-023), so
  without this they accumulate in the developer's working tree across a
  run — and collision suffixing then silently changes the paths later
  tests assert on.
* a clean session registry, so state cannot leak between tests.
"""
from __future__ import annotations

import contextlib
import os
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


def _xdist_workers() -> int:
    """How many workers are running, 1 when xdist is off."""
    raw = os.environ.get("PYTEST_XDIST_WORKER_COUNT")
    try:
        return int(raw) if raw else 1
    except ValueError:
        return 1


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Keep contention-sensitive tests out of a parallel run.

    A wall-clock budget measured while eleven other workers saturate the
    CPU is not a measurement of anything, and SPEC N2's budgets were the
    first thing to go red when `-n auto` was switched on. Skipping them
    here rather than loosening the budgets keeps the assertions worth
    making; CI runs them in a dedicated serial job, so nothing is lost.
    """
    if _xdist_workers() <= 1:
        return
    skip = pytest.mark.skip(
        reason=(
            "contention-sensitive; run serially with "
            "`uv run pytest -m serial -n0`"
        )
    )
    for item in items:
        if "serial" in item.keywords:
            item.add_marker(skip)


# Both of these must be set before `cad_mcp.sandbox` is imported: it
# reads them into module constants, and `run()` binds the timeout as a
# default argument at definition time, so patching later is too late.
if _xdist_workers() > 1:
    # The warm worker exists to keep the ~3.3s CadQuery import off the
    # *latency* critical path of a single call. In a parallel suite it
    # buys no wall clock and costs a second resident OCP process per
    # worker -- with `-n auto` that is up to 24 of them, and the memory
    # pressure alone makes everything slower. The behaviour it provides
    # is asserted by the `serial` perf tests, which run with the warm
    # worker on and the machine to themselves.
    os.environ.setdefault("CAD_MCP_WARM_WORKER", "0")

from cad_mcp import session  # noqa: E402


@pytest.fixture
def free_port() -> int:
    """An unused TCP port.

    The HTTP integration tests used to hardcode 18923/18924, which two
    xdist workers will happily try to bind at the same moment.
    """
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture(autouse=True)
def isolated_output_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """Point exports at a per-test directory, never the repo."""
    target = tmp_path_factory.mktemp("cad-output")
    monkeypatch.setenv("CAD_MCP_OUTPUT_DIR", str(target))
    yield target


@pytest.fixture(autouse=True)
def clean_sessions() -> Iterator[None]:
    """No session state survives a test."""
    session.cleanup_all()
    yield
    session.cleanup_all()


@pytest.fixture(scope="session")
def scratch_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="cad-mcp-scratch-"))
    yield path


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Exit without running native static destructors.

    OCP/VTK segfault while tearing down at interpreter exit. The crash is
    *after* all work completes, so it never affects a result — but it does
    make the process exit 139/127 instead of 0, which means `pytest`
    prints "195 passed" and CI still marks the job failed.

    Reproduced on commit 80d660e (pre-dating any of this work, 3/3 runs)
    and in a script that only imports cadquery and tessellates, so it is
    the geometry stack rather than anything in the test suite.

    Flushing and then `os._exit` skips the destructors. It is the standard
    mitigation for native-extension teardown crashes and it makes the exit
    code mean what it says. See issue #10 for the mid-operation case,
    which is a different and larger problem.

    Set `CAD_MCP_NO_HARD_EXIT=1` to opt out (useful when debugging, since
    the hard exit also skips coverage and profiler teardown).
    """
    import os
    import sys

    # `pytest_unconfigure` with trylast runs after the terminal reporter
    # has written its summary, so the "N passed" line is not lost.
    sys.stdout.flush()
    sys.stderr.flush()

    if os.environ.get("CAD_MCP_NO_HARD_EXIT") == "1":
        return

    status = getattr(config, "_cad_mcp_exitstatus", 0)
    os._exit(int(status))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Stash the real exit status for the hard exit in `pytest_unconfigure`."""
    session.config._cad_mcp_exitstatus = exitstatus  # type: ignore[attr-defined]
