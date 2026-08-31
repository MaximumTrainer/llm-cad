"""Per-session state: parts, code history, tmpdir lifecycle, metadata.

Sessions are keyed by a session ID (currently "default" for stdio
transport; maps to MCP session IDs for HTTP transport).
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PART_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_PARTS = 16
DEFAULT_COLORS = ["steel", "blue", "red", "green", "orange", "purple"]


@dataclass
class Part:
    """A named shape within an assembly."""

    name: str
    code_history: list[str] = field(default_factory=list)
    color: str = "steel"
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

    def __post_init__(self) -> None:
        if not self.parts:
            self.parts["main"] = Part(name="main")

    def get_active_part(self) -> Part:
        return self.parts[self.active_part]

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

_SESSION_PREFIX = "cad-mcp-"


def get_or_create(session_id: str = "default") -> Session:
    if session_id not in _sessions:
        tmpdir = Path(tempfile.mkdtemp(prefix=_SESSION_PREFIX))
        _sessions[session_id] = Session(session_id=session_id, tmpdir=tmpdir)
    return _sessions[session_id]


def reset(session_id: str = "default") -> str:
    if session_id in _sessions:
        _sessions[session_id].cleanup()
        del _sessions[session_id]
    return f"Session '{session_id}' has been reset."


def cleanup_all() -> None:
    for s in _sessions.values():
        s.cleanup()
    _sessions.clear()
