"""Per-session state: parts, code history, tmpdir lifecycle, metadata.

Sessions are keyed by a session ID (currently "default" for stdio
transport; maps to MCP session IDs for HTTP transport).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SESSION_ID_HEADER = "mcp-session-id"
DEFAULT_SESSION_ID = "default"


def idle_timeout_s() -> float:
    """Seconds an unused session is kept before eviction (SPEC 10.1 H4)."""
    raw = os.environ.get("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0

PART_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def output_root() -> Path:
    """Where exports are written.

    Deliberately NOT the session tmpdir: that is deleted by
    ``reset_session`` and reclaimed by the OS, so the artefact the whole
    workflow exists to produce was stored in the one place guaranteed to
    be thrown away (SPEC G4).
    """
    configured = os.environ.get("CAD_MCP_OUTPUT_DIR", "").strip()
    root = Path(configured) if configured else Path.cwd() / "cad-mcp-output"
    return root.expanduser().resolve()
MAX_PARTS = 16
DEFAULT_COLORS = ["steel", "blue", "red", "green", "orange", "purple"]


@dataclass
class Part:
    """A named shape within an assembly."""

    name: str
    code_history: list[str] = field(default_factory=list)
    color: str = "steel"
    # "cadquery" geometry is reproducible by replaying code_history.
    # "ai_mesh" geometry is not: it came from gen_ai_mesh and exists only
    # as a BREP/GLB, so replaying history would silently destroy it.
    source: str = "cadquery"
    ai_prompt: str | None = None
    ai_glb_path: str | None = None
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotate: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bbox: dict[str, float] | None = None

    def accumulated_code(self) -> str:
        return "\n\n".join(self.code_history)


@dataclass
class Session:
    session_id: str
    tmpdir: Path
    parts: dict[str, Part] = field(default_factory=dict)
    active_part: str = "main"
    exports: list[dict[str, str]] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)
    # Tools are dispatched on a thread pool, so two calls against one
    # session can interleave. Mutating tools take this lock (CAD-008).
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        if not self.parts:
            self.parts["main"] = Part(name="main")

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def get_active_part(self) -> Part:
        return self.parts[self.active_part]

    def output_dir(self) -> Path:
        """Durable per-session export directory; survives reset."""
        path = output_root() / self.session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def brep_path(self, part_name: str | None = None) -> Path:
        name = part_name or self.active_part
        return self.tmpdir / f"{name}.brep"

    def has_model(self, part_name: str | None = None) -> bool:
        return self.brep_path(part_name).exists()

    def accumulated_code(self) -> str:
        return self.get_active_part().accumulated_code()

    def next_color(self) -> str:
        used = {p.color for p in self.parts.values()}
        for c in DEFAULT_COLORS:
            if c not in used:
                return c
        return DEFAULT_COLORS[len(self.parts) % len(DEFAULT_COLORS)]

    def summary(self) -> dict[str, Any]:
        parts_info = []
        for name, part in self.parts.items():
            parts_info.append({
                "name": name,
                "code_blocks": len(part.code_history),
                "has_model": self.brep_path(name).exists(),
                "color": part.color,
                "translate": list(part.translate),
                "rotate": list(part.rotate),
                "bbox": part.bbox,
                "is_active": name == self.active_part,
            })

        active = self.get_active_part()
        return {
            "session_id": self.session_id,
            "active_part": self.active_part,
            "parts": parts_info,
            "code_blocks": len(active.code_history),
            "code": active.accumulated_code() if active.code_history else None,
            "current_bbox": active.bbox,
            "exports": self.exports,
            "has_model": self.has_model(),
        }

    def cleanup(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)


_sessions: dict[str, Session] = {}
_sessions_lock = threading.RLock()

_SESSION_PREFIX = "cad-mcp-"


def resolve_id(ctx: Any = None) -> str:
    """Identify the MCP session behind a tool call.

    Over stdio there is one client and one session. Over HTTP each client
    gets its own `Mcp-Session-Id`, and sharing state between them would
    leak one user's model — and code — into another's (SPEC 10.1 H4).
    """
    if ctx is None:
        return DEFAULT_SESSION_ID
    try:
        headers = ctx.headers or {}
    except Exception:
        return DEFAULT_SESSION_ID
    for key, value in headers.items():
        if key.lower() == SESSION_ID_HEADER and value:
            return str(value)
    return DEFAULT_SESSION_ID


def get_or_create(session_id: str = DEFAULT_SESSION_ID) -> Session:
    with _sessions_lock:
        # Never sweep the session we are about to hand out.
        evict_idle(keep=session_id)
        sess = _sessions.get(session_id)
        if sess is None:
            tmpdir = Path(tempfile.mkdtemp(prefix=_SESSION_PREFIX))
            sess = Session(session_id=session_id, tmpdir=tmpdir)
            _sessions[session_id] = sess
        sess.touch()
        return sess


def for_context(ctx: Any = None) -> Session:
    """The session belonging to this request. Use this from tools."""
    return get_or_create(resolve_id(ctx))


def evict_idle(
    now: float | None = None, keep: str | None = None
) -> list[str]:
    """Drop sessions unused for longer than the idle timeout.

    Three things are deliberately never evicted, because getting this
    wrong destroys a live model mid-conversation:

    * the session named by *keep* — the one the current request is for;
    * the stdio session, which has exactly one client for the lifetime of
      the process and so is never "disconnected" (SPEC 10.1 H4 is about
      HTTP session cleanup);
    * any session whose lock is held, i.e. a request is in flight.
    """
    timeout = idle_timeout_s()
    if timeout <= 0:
        return []
    current = time.monotonic() if now is None else now
    evicted: list[str] = []

    with _sessions_lock:
        for sid, sess in list(_sessions.items()):
            if sid in (keep, DEFAULT_SESSION_ID):
                continue
            if current - sess.last_used <= timeout:
                continue
            # A held lock means a tool is still running against it.
            if not sess.lock.acquire(blocking=False):
                continue
            try:
                sess.cleanup()
                del _sessions[sid]
                evicted.append(sid)
            finally:
                sess.lock.release()
    return evicted


def active_ids() -> list[str]:
    with _sessions_lock:
        return list(_sessions)


def reset(session_id: str = "default") -> str:
    """Clear modelling state. Exported files are deliberately kept."""
    kept: list[str] = []
    if session_id in _sessions:
        sess = _sessions[session_id]
        kept = [e["path"] for e in sess.exports]
        sess.cleanup()
        del _sessions[session_id]

    msg = f"Session '{session_id}' has been reset."
    if kept:
        listing = "\n  ".join(kept)
        msg += (
            f" {len(kept)} exported file(s) were kept in "
            f"{output_root() / session_id}:\n  {listing}"
        )
    return msg


def cleanup_all() -> None:
    for s in _sessions.values():
        s.cleanup()
    _sessions.clear()
