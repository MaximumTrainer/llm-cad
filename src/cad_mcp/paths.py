"""Filename and output-path validation shared by every writer.

The LLM chooses export filenames, so an unvalidated name is a path
traversal with a language model holding the pen: `filename="../../x"`
escaped the session directory and wrote wherever the server process
could reach.  Every writer goes through :func:`safe_output_path`.

Names are rejected rather than silently rewritten, so the model gets a
correctable error instead of a file in a place it did not ask for.
"""
from __future__ import annotations

import re
from pathlib import Path

SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Reserved device names on Windows; writing to them is never a file.
_NT_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class UnsafeFilename(ValueError):
    """Raised when a caller-supplied filename is not safe to write."""


def validate_filename(name: str) -> str:
    """Return *name* when it is a safe single path component.

    Raises :class:`UnsafeFilename` with an LLM-actionable message
    otherwise.
    """
    if not name or not name.strip():
        raise UnsafeFilename(
            "filename must not be empty. Use letters, digits, dot, "
            "dash or underscore, e.g. 'bracket'."
        )
    if not SAFE_FILENAME_RE.match(name):
        raise UnsafeFilename(
            f"Invalid filename {name!r}. A filename must match "
            f"[A-Za-z0-9][A-Za-z0-9._-]{{0,63}} — no path separators, no "
            f"'..', no absolute paths, no drive letters. Try 'bracket'."
        )
    if name.split(".")[0].lower() in _NT_RESERVED:
        raise UnsafeFilename(
            f"Invalid filename {name!r}: that is a reserved device name."
        )
    return name


def safe_output_path(output_dir: Path, filename: str) -> Path:
    """Validate *filename* and resolve it inside *output_dir*.

    Belt and braces: the name is validated, then the resolved path is
    re-checked against the directory, so a future change to the pattern
    cannot silently reintroduce an escape.
    """
    validate_filename(filename)

    base = output_dir.resolve()
    candidate = (base / filename).resolve()

    if candidate != base and base not in candidate.parents:
        raise UnsafeFilename(
            f"Refusing to write outside the output directory: {candidate}"
        )
    return candidate
