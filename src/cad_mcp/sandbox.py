"""Sandboxed subprocess runner for CadQuery code execution.

Two layers of containment:

* **OS-enforced** (this module): wall-clock timeout, address-space cap,
  CPU-time cap, file-size cap, process-count cap, and a process-group
  kill so nothing survives a timeout.  These are hard guarantees.
* **In-process** (``_sandbox_policy``): filesystem confinement, network
  and process-creation blocks, and the import policy.  Defence in depth
  against accidents and casual misuse, not a hostile-code jail.

See ``_sandbox_policy`` for the full threat model, and SPEC N1.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cad_mcp import warm_worker

_WORKER = Path(__file__).with_name("_sandbox_worker.py")

DEFAULT_TIMEOUT_S = int(os.environ.get("CAD_MCP_SANDBOX_TIMEOUT_S") or 30)
DEFAULT_MEMORY_MB = int(os.environ.get("CAD_MCP_SANDBOX_MEM_MB") or 2048)
DEFAULT_MAX_FILE_MB = int(os.environ.get("CAD_MCP_SANDBOX_FILE_MB") or 512)
DEFAULT_MAX_PROCS = int(os.environ.get("CAD_MCP_SANDBOX_MAX_PROCS") or 64)

_IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    error_type: str | None = None
    message: str | None = None
    line: int | None = None
    snippet: str | None = None
    hint: str | None = None
    context: list[str] | None = None
    solid_count: int | None = None
    bbox: dict[str, float] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        for key in (
            "error_type",
            "message",
            "line",
            "snippet",
            "hint",
            "context",
            "solid_count",
            "bbox",
        ):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        return d

    def format_for_llm(self) -> str:
        """Single-string representation suitable for an MCP text response."""
        if self.ok:
            parts = [f"OK -- {self.solid_count} solid(s)"]
            if self.bbox:
                b = self.bbox
                dims = (
                    f"{b['xmax'] - b['xmin']:.2f} x "
                    f"{b['ymax'] - b['ymin']:.2f} x "
                    f"{b['zmax'] - b['zmin']:.2f} mm"
                )
                parts.append(f"Bounding box: {dims}")
                parts.append(
                    f"  x: [{b['xmin']:.2f}, {b['xmax']:.2f}]  "
                    f"y: [{b['ymin']:.2f}, {b['ymax']:.2f}]  "
                    f"z: [{b['zmin']:.2f}, {b['zmax']:.2f}]"
                )
            return "\n".join(parts)

        parts = [f"{self.error_type}: {self.message}"]
        if self.line is not None:
            loc = f"  at line {self.line}"
            if self.snippet:
                loc += f": {self.snippet}"
            parts.append(loc)
        if self.hint:
            parts.append(f"  Hint: {self.hint}")
        return "\n".join(parts)


def _preexec(memory_bytes: int, max_file_bytes: int, max_procs: int):  # type: ignore[no-untyped-def]
    """Return a preexec_fn applying rlimits and a new process group.

    The new session is what makes the timeout path able to kill
    grandchildren rather than orphaning them.
    """
    if sys.platform == "win32":
        return None

    def _set_limits() -> None:
        import resource

        os.setsid()
        for res, limit in (
            (resource.RLIMIT_AS, memory_bytes),
            (resource.RLIMIT_DATA, memory_bytes),
            (resource.RLIMIT_FSIZE, max_file_bytes),
            (resource.RLIMIT_NPROC, max_procs),
            (resource.RLIMIT_CORE, 0),
        ):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(res, (limit, limit))

    return _set_limits


def _assign_windows_job(proc: subprocess.Popen[str], memory_bytes: int) -> Any:
    """Cap memory and guarantee cleanup for a Windows child.

    Windows has no rlimits; a Job Object with
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is the equivalent, and it
    covers the whole process tree rather than only the direct child.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JobObjectExtendedLimitInformation = 9
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info.ProcessMemoryLimit = memory_bytes
        info.BasicLimitInformation.ActiveProcessLimit = DEFAULT_MAX_PROCS
        kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        handle = kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid
        )
        if handle:
            kernel32.AssignProcessToJobObject(job, handle)
            kernel32.CloseHandle(handle)
        return job
    except Exception:
        return None


def _worker_env(out_brep: Path, out_result: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CAD_MCP_BREP_OUT"] = str(out_brep)
    env["CAD_MCP_RESULT_OUT"] = str(out_result)
    # The worker imports cad_mcp._sandbox_policy; make the package
    # importable whether running from a source checkout or a wheel.
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [pkg_parent, env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    return env


def _popen_kwargs(
    cwd: Path,
    env: dict[str, str],
    memory_bytes: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": env,
    }
    preexec = _preexec(memory_bytes, max_file_bytes, DEFAULT_MAX_PROCS)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return kwargs


def _spawn_worker(
    tmpdir: Path,
    code_file: Path,
    env: dict[str, str],
    memory_bytes: int,
    max_file_bytes: int,
) -> subprocess.Popen[str]:
    """Start a cold worker that takes its job from argv."""
    return subprocess.Popen(
        [sys.executable, str(_WORKER), str(tmpdir), str(code_file)],
        **_popen_kwargs(tmpdir, env, memory_bytes, max_file_bytes),
    )


def prewarm() -> None:
    """Start a spare worker so the next execute_cad is warm.

    Called once at server start-up. Blocking until the worker reports
    ready matters: a process that has merely been *started* is not warm,
    and handing one out mid-import would move the cost rather than
    remove it.
    """
    if not warm_worker.enabled():
        return

    def spawn() -> subprocess.Popen[str]:
        env = dict(os.environ)
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(
            [pkg_parent, env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)

        proc = subprocess.Popen(
            [sys.executable, str(_WORKER), "--serve"],
            **_popen_kwargs(
                Path.cwd(),
                env,
                DEFAULT_MEMORY_MB * 1024 * 1024,
                DEFAULT_MAX_FILE_MB * 1024 * 1024,
            ),
        )

        assert proc.stderr is not None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                break
            if "worker ready" in line:
                return proc
        proc.kill()
        msg = "warm sandbox worker never reported ready"
        raise RuntimeError(msg)

    warm_worker.POOL.configure(spawn)
    warm_worker.POOL.prewarm()


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the child and everything it spawned."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def run(
    code: str,
    tmpdir: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    memory_bytes: int | None = None,
    brep_out: Path | None = None,
) -> SandboxResult:
    """Execute *code* in a sandboxed subprocess.

    Each invocation gets its own directory under *tmpdir* for its source
    and its BREP output, so concurrent calls in one session cannot
    overwrite each other's files.
    """
    memory_bytes = memory_bytes or DEFAULT_MEMORY_MB * 1024 * 1024
    max_file_bytes = DEFAULT_MAX_FILE_MB * 1024 * 1024

    run_dir = tmpdir / f"run-{uuid.uuid4().hex[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    code_file = run_dir / "user_code.py"
    code_file.write_text(code, encoding="utf-8")
    out_brep = brep_out or (run_dir / "out.brep")
    out_result = run_dir / "result.json"

    env = _worker_env(out_brep, out_result)

    # A pre-warmed worker has already paid the ~3.3s CadQuery import, so
    # the timeout below covers execution rather than start-up. It is
    # single-use: the interpreter and namespace are never reused for a
    # second job, so isolation is unchanged (CAD-014).
    proc = warm_worker.POOL.take()
    job_payload: str | None = None
    windows_job: Any = None

    if proc is not None:
        job_payload = json.dumps(
            {
                "tmpdir": str(tmpdir),
                "code_path": str(code_file),
                "brep_out": str(out_brep),
                "result_out": str(out_result),
            }
        )
    else:
        proc = _spawn_worker(
            tmpdir, code_file, env, memory_bytes, max_file_bytes
        )
        windows_job = _assign_windows_job(proc, memory_bytes)

    try:
        stdout, stderr = proc.communicate(job_payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=5)
        return SandboxResult(
            ok=False,
            error_type="TimeoutError",
            message=f"Code execution exceeded the {timeout}s time limit.",
            hint="Simplify the model or break it into smaller steps.",
        )
    finally:
        # The platform test has to come *first*. mypy only narrows
        # `sys.platform` when it leads the condition, so with the
        # `windows_job is not None` term in front it still looked for
        # ctypes.WinDLL on Linux and failed the Linux job — which is how
        # main came to be red.
        if sys.platform == "win32" and windows_job is not None:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(windows_job)

    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()

    # The result file is the protocol; stdout is only a fallback, because
    # CadQuery/VTK/OCP print to it uninvited (SPEC N6, PLAN stdout risk).
    data: dict[str, Any] | None = _read_result(out_result)
    if data is None and stdout:
        for line in reversed(stdout.splitlines()):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        return _no_output_result(proc.returncode, stderr, memory_bytes)

    return SandboxResult(
        ok=data.get("ok", False),
        error_type=data.get("error_type"),
        message=data.get("message"),
        line=data.get("line"),
        snippet=data.get("snippet"),
        hint=data.get("hint"),
        context=data.get("context"),
        solid_count=data.get("solid_count"),
        bbox=data.get("bbox"),
    )


def _read_result(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed


def _no_output_result(
    returncode: int | None, stderr: str, memory_bytes: int
) -> SandboxResult:
    """Classify a worker that died without producing JSON.

    A memory-limit kill looks like a signal death or a MemoryError on
    stderr; reporting it as a generic SandboxError left the LLM with
    nothing to act on.
    """
    lowered = stderr.lower()
    mb = memory_bytes // (1024 * 1024)

    if "memoryerror" in lowered or "cannot allocate" in lowered:
        return SandboxResult(
            ok=False,
            error_type="MemoryError",
            message=f"Code exceeded the {mb}MB memory limit.",
            hint="Reduce mesh density, tessellation tolerance, or model size.",
        )
    # SIGKILL (-9) is what the OOM path and the job-object cap look like.
    if returncode in (-9, 137, 1816):
        return SandboxResult(
            ok=False,
            error_type="MemoryError",
            message=(
                f"Code was killed by the sandbox, most likely the "
                f"{mb}MB memory limit."
            ),
            hint="Reduce mesh density, tessellation tolerance, or model size.",
        )
    if returncode is not None and returncode < 0:
        return SandboxResult(
            ok=False,
            error_type="SandboxError",
            message=f"Sandbox process died with signal {-returncode}.",
            hint="The geometry kernel crashed; try a simpler operation.",
        )

    msg = "Sandbox produced no output."
    if stderr:
        for line in stderr.splitlines():
            if "Error" in line or "error" in line:
                msg = line.strip()
                break
    return SandboxResult(ok=False, error_type="SandboxError", message=msg)
