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
from unittest import mock

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


def test_the_path_guard_does_not_re_enter_itself(tmp_path: Path) -> None:
    """The guard must not be broken by the functions it guards.

    `os.path.realpath` is pure Python on POSIX and resolves a path by
    calling `os.lstat` and `os.readlink` -- both of which the guard
    wraps. Without a re-entrancy flag each check resolves a path that
    triggers another check, and the interpreter dies with RecursionError
    before any policy decision is ever reached.

    Every sandboxed execution on Linux and macOS failed this way, and the
    containment tests still "passed" because a crash is also a refusal --
    precisely the trap the sandbox-audit skill warns about: "denied" and
    "the guard never ran" look identical from outside. Measured in a
    container, pre-fix: a write *inside* the session directory, which
    must succeed, failed with RecursionError. Windows was unaffected, and
    was the only platform CI had ever managed to run.

    `realpath` is stubbed with a POSIX-shaped one -- resolve by calling
    `os.lstat` -- so this exercises the invariant on every platform
    rather than passing vacuously on the one where the real `realpath` is
    a single C call.
    """
    import os.path as osp

    from cad_mcp._sandbox_policy import _PathPolicy

    policy = _PathPolicy(str(tmp_path))
    target = tmp_path / "a" / "b" / "c.txt"
    target.parent.mkdir(parents=True)
    target.write_text("x")

    real_lstat = os.lstat
    lstat_calls: list[str] = []

    def guarded_lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        # What install_path_guard wraps os.lstat with.
        lstat_calls.append(str(path))
        policy.check(path, write=False)
        return real_lstat(path, *args, **kwargs)

    def posix_shaped_realpath(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        os.lstat(path)  # the guarded one, as posixpath would reach it
        return str(path)

    with mock.patch.object(os, "lstat", guarded_lstat), mock.patch.object(
        osp, "realpath", posix_shaped_realpath
    ):
        policy.check(target, write=True)  # must not raise RecursionError

    assert lstat_calls, "the re-entrant path was never exercised"


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


# Wall-clock: the assertion is about how fast the timeout fires, which
# means nothing while eleven other workers own the CPU.
@pytest.mark.slow
@pytest.mark.serial
def test_infinite_loop_is_killed(tmp_session: Path) -> None:
    start = time.monotonic()
    result = sandbox.run("while True: pass", tmp_session, timeout=5)
    elapsed = time.monotonic() - start

    assert not result.ok
    assert result.error_type == "TimeoutError"
    assert elapsed < 25, f"Timeout took {elapsed:.1f}s to fire"


# Counts python processes on the *host*, so a parallel run attributes
# other workers' subprocesses to this test (measured: 62 -> 68).
@pytest.mark.slow
@pytest.mark.serial
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


# Allocating 1.5GB while eleven other workers each hold a resident
# CadQuery is how you measure the machine, not the memory cap.
@pytest.mark.slow
@pytest.mark.serial
def test_memory_bomb_is_killed(tmp_session: Path) -> None:
    """A runaway allocation must be stopped on every platform.

    The cap has to sit above CadQuery's own import footprint (~600MB) or
    the worker dies before it can report anything useful; 1.5GB leaves
    headroom while still being reachable in a couple of seconds.
    """
    start = time.monotonic()
    result = sandbox.run(
        # cadquery first, so a payload that is *not* contained can still
        # assign a valid result and report ok=true. The previous version
        # ended `result = 1`, so an uncontained run failed the
        # result-type check instead -- the exact shape of false pass the
        # sandbox-audit skill exists to forbid.
        "import cadquery as cq\n"
        "import numpy\n"
        "blocks = []\n"
        # 12 x 256MB = 3GB against a 1.5GB cap. Each block is *touched*:
        # numpy.zeros hands back lazily-mapped pages, and an allocation
        # nothing ever reads costs no physical memory, so an untouched
        # loop tests the allocator rather than the cap.
        "for _ in range(12):\n"
        "    block = numpy.zeros((1024, 1024, 32))\n"
        "    block.fill(1.0)\n"
        "    blocks.append(block)\n"
        "result = cq.Workplane('XY').box(1, 1, 1)\n",
        tmp_session,
        timeout=120,
        memory_bytes=1536 * 1024 * 1024,
    )
    elapsed = time.monotonic() - start

    assert not result.ok, (
        f"3GB was allocated and touched under a 1.5GB cap: the memory "
        f"limit is not enforced here. {result.to_dict()}"
    )
    assert result.error_type == "MemoryError", (
        f"Memory exhaustion should be reported as MemoryError, "
        f"got {result.to_dict()}"
    )
    assert elapsed < 100, "Cap was only enforced by the wall-clock timeout"


@pytest.mark.slow
@pytest.mark.serial
def test_the_memory_cap_survives_worker_reuse(tmp_session: Path) -> None:
    """The pre-warmed worker must be capped too.

    `main()` calls `sandbox.prewarm()` at start-up, so a reused worker is
    not an edge case -- it is the path every production execution takes.
    It was also the path with no memory cap on it: the Job Object was
    only ever assigned on the cold path, and Windows accepts a job
    assignment onto an already-running process and then declines to
    enforce it. This payload allocated and touched 4GB under a 2GB cap
    and reported ok=true.

    Deliberately uses the *default* cap rather than passing one, because
    the default is what the server runs with.
    """
    sandbox.prewarm()
    # prewarm starts the worker on a thread; give it the import.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if sandbox.warm_worker.POOL._spare is not None:
            break
        time.sleep(0.5)
    else:
        pytest.skip("the warm worker never came up; nothing to assert")

    result = sandbox.run(
        # 16 x 256MB = 4GB against the 2GB default, every page touched.
        "import cadquery as cq\n"
        "import numpy\n"
        "blocks = []\n"
        "for _ in range(16):\n"
        "    block = numpy.zeros((1024, 1024, 32))\n"
        "    block.fill(1.0)\n"
        "    blocks.append(block)\n"
        "result = cq.Workplane('XY').box(1, 1, 1)\n",
        tmp_session,
        timeout=180,
    )

    assert not result.ok, (
        f"a re-used warm worker allocated and touched 4GB under the "
        f"{sandbox.DEFAULT_MEMORY_MB}MB default cap: the limit is not "
        f"applied to pre-warmed workers. {result.to_dict()}"
    )
    assert result.error_type == "MemoryError", (
        f"expected the memory cap to bite, got {result.to_dict()}"
    )


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
