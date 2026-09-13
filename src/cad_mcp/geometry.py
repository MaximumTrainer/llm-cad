"""Server-side client for the isolated geometry worker (issue #10).

SPEC §7 isolates *user* code in a subprocess, but everything downstream
of it -- tessellation, measurement, STEP/XCAF export, boolean
interference -- used to load the B-rep and call OCP inside the server
process. OCP is a binding over a C++ kernel: a malformed shape, a
degenerate boolean or a teardown bug is a **segfault**, not a Python
exception, and no ``try/except`` will catch it. When it happened the MCP
server died mid-conversation and the LLM got a transport error instead of
something it could act on, which is exactly what N3 exists to prevent.

Evidence this is not theoretical: on this machine a script that imports
cadquery, tessellates and exports completes its work and *then* exits
with 0xC0000374 (heap corruption) on Windows, and 139 (SIGSEGV) on
Linux -- in OCP/VTK static destructors. The test suite already works
around the shutdown case with a hard exit in `conftest.py`; this module
handles the mid-operation case, which that cannot.

So: one long-lived worker process owns OCP. It is reused between calls --
it runs our code, not the user's, so a fresh interpreter would buy no
isolation and cost the 3.3s CadQuery import every time -- and it is
replaced when it dies. A crash becomes ``GeometryKernelError``, which the
tools render as a normal structured failure, and the next call gets a
fresh worker.

Tessellation is cached on (path, mtime, size, tolerances) so the extra
process boundary is paid once per shape rather than once per render,
validate and export of the same shape.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cad_mcp._geometry_worker import DONE, READY

logger = logging.getLogger(__name__)

_WORKER = Path(__file__).with_name("_geometry_worker.py")

#: Wall-clock ceiling for one geometry operation. Generous: a boolean on
#: a dense shape is legitimately slow, and killing a working operation is
#: worse than waiting. The sandbox's 30s limit covers *user* code, which
#: is the untrusted part.
DEFAULT_TIMEOUT_S = float(os.environ.get("CAD_MCP_GEOMETRY_TIMEOUT_S") or 120)

#: Set to "0" to run OCP in-process again. Escape hatch for debugging a
#: kernel problem under a debugger, not a supported mode.
def isolated() -> bool:
    return os.environ.get("CAD_MCP_GEOMETRY_ISOLATION", "1") != "0"


class GeometryKernelError(RuntimeError):
    """An OCP operation failed, or the kernel process died doing it."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "GeometryKernelError",
        hint: str | None = None,
        crashed: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.hint = hint
        self.crashed = crashed


@dataclass
class _Worker:
    proc: subprocess.Popen[str]
    workdir: Path


class GeometryWorker:
    """Owns at most one live worker process, restarting it as needed."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker: _Worker | None = None
        self._counter = 0

    # -- lifecycle -------------------------------------------------

    def _spawn(self) -> _Worker:
        import tempfile

        env = dict(os.environ)
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(
            [pkg_parent, env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        # The worker's stdout carries our protocol marker; keep OCP's
        # uninvited chatter from being mistaken for it by parsing only
        # marker lines (see _await_done).
        env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert proc.stderr is not None
        deadline_lines = 0
        while deadline_lines < 200:
            line = proc.stderr.readline()
            if not line:
                break
            if READY in line:
                workdir = Path(tempfile.mkdtemp(prefix="cad-geom-"))
                logger.debug("geometry worker %s ready", proc.pid)
                return _Worker(proc=proc, workdir=workdir)
            deadline_lines += 1
        proc.kill()
        msg = "the geometry worker never became ready"
        raise GeometryKernelError(msg, crashed=True)

    def _ensure(self) -> _Worker:
        worker = self._worker
        if worker is not None and worker.proc.poll() is None:
            return worker
        if worker is not None:
            logger.warning(
                "geometry worker exited (code %s); starting a new one",
                worker.proc.returncode,
            )
        self._worker = self._spawn()
        return self._worker

    def shutdown(self) -> None:
        with self._lock:
            worker, self._worker = self._worker, None
        if worker is not None:
            with _suppress_os_error():
                worker.proc.kill()

    def is_ready(self) -> bool:
        """Whether a live worker is standing by, without starting one.

        Readiness is not liveness: the process answers HTTP about 3.3s
        before it can run geometry, because that is what importing
        CadQuery costs. `/health` needs to tell those apart (SPEC H7), so
        this must never block and must never spawn — a health check that
        starts the thing it is checking always reports success.
        """
        worker = self._worker
        return worker is not None and worker.proc.poll() is None

    def prewarm(self) -> None:
        """Start the worker off the critical path, like the sandbox does."""
        if not isolated():
            return

        def start() -> None:
            try:
                with self._lock:
                    self._ensure()
            except Exception as exc:
                logger.warning("could not pre-warm the geometry worker: %s", exc)

        threading.Thread(target=start, daemon=True).start()

    # -- the call --------------------------------------------------

    def call(
        self,
        op: str,
        args: dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Run *op* in the worker. Raises GeometryKernelError on failure."""
        with self._lock:
            worker = self._ensure()
            self._counter += 1
            stem = worker.workdir / f"{self._counter:06d}-{uuid.uuid4().hex[:8]}"
            request = stem.with_suffix(".json")
            request.write_text(
                json.dumps({"op": op, "args": args}), encoding="utf-8"
            )

            try:
                assert worker.proc.stdin is not None
                worker.proc.stdin.write(f"{request}\n")
                worker.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._discard(worker)
                raise self._crashed(op, f"pipe closed: {exc}") from exc

            response_path = self._await_done(worker, op, timeout)
            payload = json.loads(
                Path(response_path).read_text(encoding="utf-8")
            )

        if payload.get("ok"):
            result: dict[str, Any] = payload["result"]
            return result

        # An ordinary Python-level failure in the kernel: the worker is
        # still healthy, so keep it and report what went wrong.
        raise GeometryKernelError(
            payload.get("message") or "geometry operation failed",
            error_type=payload.get("error_type", "GeometryKernelError"),
            hint=_hint_for(payload.get("error_type", ""), op),
        )

    def _await_done(
        self, worker: _Worker, op: str, timeout: float
    ) -> str:
        """Read stdout until the marker, a crash, or the deadline."""
        import time

        assert worker.proc.stdout is not None
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                self._discard(worker)
                raise GeometryKernelError(
                    f"geometry operation {op!r} exceeded {timeout:.0f}s",
                    error_type="TimeoutError",
                    hint="Simplify the model, or raise CAD_MCP_GEOMETRY_TIMEOUT_S.",
                    crashed=True,
                )
            line = worker.proc.stdout.readline()
            if not line:
                # EOF: the process is gone. This is the segfault path.
                self._discard(worker)
                raise self._crashed(op, _exit_description(worker.proc))
            if line.startswith(DONE):
                return str(line[len(DONE) :].strip())
            # Anything else is CadQuery/VTK/OCP printing to stdout
            # uninvited, which they do. Not an error, just not ours.
            logger.debug("geometry worker noise: %s", line.rstrip())

    def _discard(self, worker: _Worker) -> None:
        with _suppress_os_error():
            worker.proc.kill()
        if self._worker is worker:
            self._worker = None

    @staticmethod
    def _crashed(op: str, detail: str) -> GeometryKernelError:
        return GeometryKernelError(
            f"The geometry kernel crashed during {op!r} ({detail}). "
            f"The server is unaffected and the next operation will use a "
            f"fresh kernel process.",
            error_type="GeometryKernelError",
            hint=(
                "The shape is likely degenerate. Try a coarser tolerance, "
                "a simpler boolean, or rebuilding the part from code."
            ),
            crashed=True,
        )


def _exit_description(proc: subprocess.Popen[str]) -> str:
    code = proc.poll()
    if code is None:
        return "process ended without a status"
    if code < 0:
        return f"killed by signal {-code}"
    # 0xC0000374 STATUS_HEAP_CORRUPTION, 0xC0000005 access violation.
    known = {
        3221226356: "heap corruption",
        3221225477: "access violation",
        139: "SIGSEGV",
        134: "SIGABRT",
    }
    label = known.get(code)
    return f"exit code {code}" + (f", {label}" if label else "")


def _hint_for(error_type: str, op: str) -> str | None:
    if "ValueError" in error_type and op == "tessellate":
        return "The B-rep could not be read; rebuild the part with execute_cad."
    if op.startswith("export"):
        return "Try exporting STL instead, or simplify the geometry."
    return None


class _suppress_os_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return bool(exc_type is not None and issubclass(exc_type, OSError))


POOL = GeometryWorker()

# The worker is a child of this process; without this it outlives a clean
# shutdown and holds its ~600MB of OCCT until the OS notices.
atexit.register(POOL.shutdown)


# ------------------------------------------------------------------
# Public API. Everything the server needs from OCP goes through here.
# ------------------------------------------------------------------


def call(op: str, **args: Any) -> dict[str, Any]:
    """Run one geometry operation, isolated unless explicitly disabled."""
    if not isolated():
        return _call_in_process(op, args)
    return POOL.call(op, args)


def _call_in_process(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Debug-only path: run the op here, crash and all."""
    from cad_mcp import _geometry_worker

    result: dict[str, Any] = _geometry_worker.OPS[op](args)
    return result


_TessKey = tuple[str, int, int, float, float]
_tess_cache: dict[_TessKey, tuple[NDArray[np.float64], NDArray[np.int32]]] = {}
_tess_lock = threading.RLock()

#: Tessellations are large (a dense bracket is a few MB of float64) and
#: a session only ever has a handful of parts, so the cache is bounded by
#: count rather than bytes and evicted oldest-first.
_TESS_CACHE_MAX = 32


def _tess_key(
    brep_path: Path, tolerance: float, angular: float
) -> _TessKey | None:
    try:
        stat = brep_path.stat()
    except OSError:
        return None
    return (
        str(brep_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        tolerance,
        angular,
    )


def tessellate(
    brep_path: Path,
    tolerance: float,
    angular_tolerance: float,
) -> tuple[NDArray[np.float64], NDArray[np.int32]]:
    """Tessellate a B-rep, out of process, with a cache.

    The cache is keyed on the file's mtime and size as well as its path,
    so a rebuilt part is never served a stale mesh -- `execute_cad`
    rewrites the same path in place.
    """
    key = _tess_key(brep_path, tolerance, angular_tolerance)
    if key is not None:
        with _tess_lock:
            hit = _tess_cache.get(key)
        if hit is not None:
            return hit

    out = Path(POOL_WORKDIR()) / f"tess-{uuid.uuid4().hex[:12]}.npz"
    call(
        "tessellate",
        brep_path=str(brep_path),
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        out_path=str(out),
    )
    with np.load(out) as data:
        verts: NDArray[np.float64] = data["verts"]
        faces: NDArray[np.int32] = data["faces"]
    with _suppress_os_error():
        out.unlink()

    if key is not None:
        with _tess_lock:
            if len(_tess_cache) >= _TESS_CACHE_MAX:
                _tess_cache.pop(next(iter(_tess_cache)))
            _tess_cache[key] = (verts, faces)
    return verts, faces


def POOL_WORKDIR() -> Path:
    """A directory both processes can see."""
    import tempfile

    with POOL._lock:
        worker = POOL._worker
        if worker is not None:
            return worker.workdir
    global _fallback_workdir
    if _fallback_workdir is None:
        _fallback_workdir = Path(tempfile.mkdtemp(prefix="cad-geom-"))
    return _fallback_workdir


_fallback_workdir: Path | None = None


def clear_tessellation_cache() -> None:
    with _tess_lock:
        _tess_cache.clear()
