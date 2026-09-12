"""Shared test fixtures.

Two things every test needs and none should have to remember:

* an isolated export directory. Exports are now durable (CAD-023), so
  without this they accumulate in the developer's working tree across a
  run — and collision suffixing then silently changes the paths later
  tests assert on.
* a clean session registry, so state cannot leak between tests.
"""
from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cad_mcp import session


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
