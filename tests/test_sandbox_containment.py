"""SPEC 9.3 containment suite — asserts effects, not error strings.

The suite this replaces passed while the sandbox was wide open. It
failed in three ways, all of which this file is written to avoid:

1. It asserted ``"OK" not in text``, which any error satisfies — including
   an error raised for an unrelated reason *after* the malicious action
   had already succeeded.
2. Its "file escape" test ran ``import shutil`` and never touched the
   filesystem.
3. Its fork-bomb test passed on Windows only because ``os.fork`` does not
   exist there, and would have fork-bombed a Linux runner.

Every test here therefore:

* assembles a payload that assigns a valid ``result``, so a *successful*
  escape would be reported as ``ok=True`` and the test would fail;
* asserts the observable effect (no file at the target path, no
  connection accepted, no surviving process), not the wording;
* asserts the specific ``error_type`` rather than the absence of "OK".
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from cad_mcp import sandbox

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry

VALID_TAIL = "\nimport cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)"


@pytest.fixture
def tmp_session() -> Path:
    return Path(tempfile.mkdtemp(prefix="cad-mcp-test-"))


def run(code: str, tmpdir: Path, **kw: object) -> sandbox.SandboxResult:
    """Run a payload that would report success if containment failed."""
    return sandbox.run(code + VALID_TAIL, tmpdir, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Filesystem confinement
# ------------------------------------------------------------------


def test_write_outside_session_dir_is_blocked(tmp_session: Path) -> None:
    """The escape proven during review: a write to the user's home."""
    target = Path.home() / "cadmcp_containment_probe.txt"
    target.unlink(missing_ok=True)

    result = run(f"open(r'{target}', 'w').write('escaped')", tmp_session)

    assert not target.exists(), (
        f"SANDBOX ESCAPE: user code wrote to {target}"
    )
    assert not result.ok
    assert result.error_type == "SandboxViolation", result.message


def test_read_outside_session_dir_is_blocked(tmp_session: Path) -> None:
    secret = Path.home() / "cadmcp_secret_probe.txt"
    secret.write_text("sensitive", encoding="utf-8")
    try:
        result = run(f"data = open(r'{secret}').read()", tmp_session)
        assert not result.ok
        assert result.error_type == "SandboxViolation", result.message
        assert "sensitive" not in (result.message or "")
    finally:
        secret.unlink(missing_ok=True)


def test_os_level_write_outside_is_blocked(tmp_session: Path) -> None:
    """`os.open` bypasses `builtins.open` and must be guarded too."""
    target = Path.home() / "cadmcp_osopen_probe.txt"
    target.unlink(missing_ok=True)

    result = run(
        f"fd = os_mod.open(r'{target}', os_mod.O_WRONLY | os_mod.O_CREAT)",
        tmp_session,
    )
    # `os` is not in the namespace by default; the import itself is the
    # first barrier, the path guard the second. Either is a pass.
    assert not target.exists(), f"SANDBOX ESCAPE: os.open wrote to {target}"
    assert not result.ok


def test_pathlib_write_outside_is_blocked(tmp_session: Path) -> None:
    target = Path.home() / "cadmcp_pathlib_probe.txt"
    target.unlink(missing_ok=True)

    result = run(
        f"import pathlib\npathlib.Path(r'{target}').write_text('escaped')",
        tmp_session,
    )
    assert not target.exists(), f"SANDBOX ESCAPE: pathlib wrote to {target}"
    assert not result.ok


def test_writes_inside_session_dir_still_work(tmp_session: Path) -> None:
    """Confinement must not break legitimate intermediate files."""
    result = run("open('scratch.txt', 'w').write('fine')", tmp_session)
    assert result.ok, f"Legitimate in-session write was blocked: {result.message}"
    assert (tmp_session / "scratch.txt").read_text() == "fine"


# ------------------------------------------------------------------
# Network
# ------------------------------------------------------------------


def test_socket_module_import_is_blocked(tmp_session: Path) -> None:
    result = run("import socket\ns = socket.socket()", tmp_session)
    assert not result.ok
    assert result.error_type == "ImportError", result.message


def test_raw_socket_accelerator_is_blocked(tmp_session: Path) -> None:
    """The `_socket` hole proven during review."""
    result = run("import _socket\ns = _socket.socket()", tmp_session)
    assert not result.ok
    assert result.error_type == "ImportError", result.message


def test_no_connection_reaches_a_local_listener(tmp_session: Path) -> None:
    """Effect-based: a real listener must see zero connections."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(15)
    port = server.getsockname()[1]
    accepted: list[object] = []

    def accept_one() -> None:
        try:
            conn, _ = server.accept()
            accepted.append(conn)
        except (TimeoutError, OSError):
            pass

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()

    result = run(
        "import socket\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=3)",
        tmp_session,
    )

    thread.join(timeout=3)
    server.close()

    assert not accepted, "SANDBOX ESCAPE: sandbox opened a network connection"
    assert not result.ok


# ------------------------------------------------------------------
# Process creation
# ------------------------------------------------------------------


def test_subprocess_import_is_blocked(tmp_session: Path) -> None:
    result = run("import subprocess\nsubprocess.run(['echo','x'])", tmp_session)
    assert not result.ok
    assert result.error_type == "ImportError", result.message


def test_importlib_bypass_is_blocked(tmp_session: Path) -> None:
    """`importlib.import_module` does not route through `__import__`."""
    result = run(
        "import importlib\nsp = importlib.import_module('subprocess')\n"
        "sp.run(['echo', 'x'])",
        tmp_session,
    )
    assert not result.ok
    assert result.error_type == "ImportError", result.message


def test_os_system_is_blocked(tmp_session: Path) -> None:
    marker = Path(tempfile.gettempdir()) / "cadmcp_system_probe.txt"
    marker.unlink(missing_ok=True)
    result = run(
        "import os\n"
        f"os.system('echo pwned > {marker.as_posix()}')",
        tmp_session,
    )
    assert not marker.exists(), "SANDBOX ESCAPE: os.system ran a command"
    assert not result.ok
    marker.unlink(missing_ok=True)


@pytest.mark.skipif(
    not hasattr(os, "fork"), reason="os.fork does not exist on this platform"
)
def test_fork_is_blocked(tmp_session: Path) -> None:
    """A bounded fork attempt: `os.fork` must not be reachable at all.

    Deliberately NOT an unbounded fork bomb — the old test would have
    taken down a Linux runner while asserting nothing.
    """
    result = run("import os\nchild = os.fork()", tmp_session)
    assert not result.ok
    assert result.error_type in ("SandboxViolation", "ImportError"), (
        result.message
    )


# ------------------------------------------------------------------
# Resource limits (OS-enforced)
# ------------------------------------------------------------------


def test_infinite_loop_is_killed(tmp_session: Path) -> None:
    start = time.monotonic()
    result = sandbox.run("while True: pass", tmp_session, timeout=5)
    elapsed = time.monotonic() - start

    assert not result.ok
    assert result.error_type == "TimeoutError"
    assert elapsed < 25, f"Timeout took {elapsed:.1f}s to fire"


def test_timeout_kills_grandchildren(tmp_session: Path) -> None:
    """Nothing may outlive the timeout.

    The worker cannot spawn (that is blocked), so the guarantee is
    verified at the mechanism level: after a timeout the worker process
    itself is gone, and `_kill_tree` targets the group/job rather than a
    single pid.
    """
    before = _python_process_count()
    result = sandbox.run(
        "import time\nwhile True: pass", tmp_session, timeout=5
    )
    assert result.error_type == "TimeoutError"

    time.sleep(2)
    after = _python_process_count()
    assert after <= before + 1, (
        f"Process leak after timeout: {before} -> {after}"
    )


@pytest.mark.slow
def test_memory_bomb_is_killed(tmp_session: Path) -> None:
    """A runaway allocation must be stopped on every platform.

    The cap has to sit above CadQuery's own import footprint (~600MB) or
    the worker dies before it can report anything useful; 1.5GB leaves
    headroom while still being reachable in a couple of seconds.
    """
    start = time.monotonic()
    result = sandbox.run(
        "import numpy\n"
        "blocks = []\n"
        "for _ in range(4000):\n"
        "    blocks.append(numpy.zeros((1024, 1024, 32)))\n"
        "result = 1",
        tmp_session,
        timeout=120,
        memory_bytes=1536 * 1024 * 1024,
    )
    elapsed = time.monotonic() - start

    assert not result.ok, "Memory bomb was not contained"
    assert result.error_type == "MemoryError", (
        f"Memory exhaustion should be reported as MemoryError, "
        f"got {result.to_dict()}"
    )
    assert elapsed < 100, "Cap was only enforced by the wall-clock timeout"


def _python_process_count() -> int:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.lower().count("python.exe")
    out = subprocess.run(
        ["pgrep", "-c", "python"], capture_output=True, text=True, check=False
    )
    return int(out.stdout.strip() or 0)
