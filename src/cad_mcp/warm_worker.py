"""A pre-warmed, single-use sandbox worker.

Measured on this machine: a bare interpreter starts in 0.14s, and one
that has imported CadQuery takes 3.3s. A whole `execute_cad` of a simple
box took 2.96s — so essentially *all* of it was the import, paid again on
every call, and again for every block of an append-mode replay. That
alone put `execute_cad` at or over SPEC N2's 5s budget for trivial
geometry and limited how many iterations the LLM could afford inside
SPEC 9.1's four-iteration target.

The fix keeps the isolation property that matters. A spare process is
started ahead of time and does nothing but import CadQuery; when a job
arrives it installs the confinement guards for that session's directory,
runs the user's code **once**, and exits. A replacement spare is started
immediately, so the next call is warm too.

So each run still gets a fresh interpreter and a fresh namespace — the
worker is never reused for a second job — while the 3.3s import happens
off the critical path. Concurrent callers simply fall back to a cold
start, which is correct and safe rather than shared.

Disable with ``CAD_MCP_WARM_WORKER=0``.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("CAD_MCP_WARM_WORKER", "1") != "0"


class WarmSpare:
    """Holds at most one pre-warmed worker, replenished after each use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spare: subprocess.Popen[str] | None = None
        self._spawn: Any = None

    def configure(self, spawn: Any) -> None:
        """Register the callable that starts a worker in serve mode."""
        with self._lock:
            self._spawn = spawn

    def take(self) -> subprocess.Popen[str] | None:
        """Hand out the warm spare, if there is one, and replenish."""
        if not enabled():
            return None
        with self._lock:
            spare = self._spare
            self._spare = None
        if spare is not None and spare.poll() is not None:
            # It died while idle; do not hand out a corpse.
            logger.warning(
                "warm sandbox worker exited before use (code %s)",
                spare.returncode,
            )
            spare = None
        self.prewarm()
        return spare

    def prewarm(self) -> None:
        """Start a replacement spare in the background."""
        if not enabled():
            return
        with self._lock:
            if self._spare is not None or self._spawn is None:
                return
            spawn = self._spawn

        def start() -> None:
            try:
                proc = spawn()
            except Exception as exc:
                logger.warning("could not pre-warm a sandbox worker: %s", exc)
                return
            with self._lock:
                if self._spare is None:
                    self._spare = proc
                else:
                    _terminate(proc)

        threading.Thread(target=start, daemon=True).start()

    def shutdown(self) -> None:
        with self._lock:
            spare, self._spare = self._spare, None
        if spare is not None:
            _terminate(spare)


def _terminate(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(OSError):
        proc.kill()


POOL = WarmSpare()
