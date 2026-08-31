"""Sandboxed subprocess runner for CadQuery code execution.

Writes user code to the session tmpdir and runs _sandbox_worker.py in a
subprocess with timeout and (on Unix) memory limits.  Returns a typed
result that the tool layer converts to an MCP response.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_WORKER = Path(__file__).with_name("_sandbox_worker.py")

_DEFAULT_TIMEOUT = 30
_DEFAULT_MEMORY_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    error_type: str | None = None
    message: str | None = None
    line: int | None = None
    snippet: str | None = None
    hint: str | None = None
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


def _preexec(memory_bytes: int):  # type: ignore[no-untyped-def]
    """Return a preexec_fn that sets memory limits (Unix only)."""
    if sys.platform == "win32":
        return None

    def _set_limits() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

    return _set_limits


def run(
    code: str,
    tmpdir: Path,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    memory_bytes: int = _DEFAULT_MEMORY_BYTES,
) -> SandboxResult:
    """Execute *code* in a sandboxed subprocess.

    The code is written to ``tmpdir/user_code.py`` and the sandbox worker
    is launched with *tmpdir* as its cwd.  The worker outputs a single
    JSON line on stdout which is parsed into a `SandboxResult`.
    """
    code_file = tmpdir / "user_code.py"
    code_file.write_text(code, encoding="utf-8")

    cmd = [sys.executable, str(_WORKER), str(tmpdir)]

    kwargs: dict[str, Any] = {
        "cwd": str(tmpdir),
        "timeout": timeout,
        "capture_output": True,
        "text": True,
    }

    preexec = _preexec(memory_bytes)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec

    try:
        proc = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        return SandboxResult(
            ok=False,
            error_type="TimeoutError",
            message=f"Code execution exceeded the {timeout}s time limit.",
            hint="Simplify the model or break it into smaller steps.",
        )

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if not stdout:
        msg = "Sandbox produced no output."
        if stderr:
            for line in stderr.splitlines():
                if "Error" in line or "error" in line:
                    msg = line.strip()
                    break
        return SandboxResult(ok=False, error_type="SandboxError", message=msg)

    try:
        data: dict[str, Any] = json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return SandboxResult(
            ok=False,
            error_type="SandboxError",
            message=f"Sandbox returned unparseable output: {stdout[:200]}",
        )

    return SandboxResult(
        ok=data.get("ok", False),
        error_type=data.get("error_type"),
        message=data.get("message"),
        line=data.get("line"),
        snippet=data.get("snippet"),
        hint=data.get("hint"),
        solid_count=data.get("solid_count"),
        bbox=data.get("bbox"),
    )
