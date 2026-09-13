"""Prove SPEC N4: the same model exports byte-identically, every time.

Run standalone::

    uv run python scripts/determinism_check.py
    uv run python scripts/determinism_check.py --json hashes-linux.json

Every export happens in a **separate process**, which is the point: the
old test called ``export_model`` twice inside one interpreter and so only
showed that tessellation is stable in-process. It could not have caught
the STEP header timestamp or the random 3MF UUIDs, because both differ
per *write*, not per *process* -- but a test that never crosses a process
boundary is also blind to anything seeded once at import.

``--json`` writes the hash of each format so CI can compare the files
produced on different operating systems, which is the other half of N4
and cannot be checked from a single runner.

Children exit via ``os._exit`` after writing a sentinel file. OCP/VTK
crash in native static destructors at interpreter shutdown on this
stack (issue #10), so a child's exit code says nothing about whether its
work succeeded; the sentinel does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _REPO_SRC not in sys.path:
    sys.path.insert(0, _REPO_SRC)

FORMATS = ("step", "stl", "3mf", "glb")

# A shape with a curved surface and a through hole: a plain box would
# tessellate to twelve triangles and hide any ordering instability.
MODEL_CODE = """\
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 10)
    .edges("|Z")
    .fillet(4)
    .faces(">Z")
    .workplane()
    .hole(6)
)
"""


def _sentinel(out_dir: Path) -> Path:
    return out_dir / "_done"


def _build_brep(brep: Path) -> None:
    """Child mode: evaluate MODEL_CODE and persist the B-rep."""
    import os

    namespace: dict[str, object] = {}
    exec(MODEL_CODE, namespace)
    result = namespace["result"]
    result.val().exportBrep(str(brep))  # type: ignore[attr-defined]
    _sentinel(brep.parent).write_text("ok")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _export_all(brep: Path, out_dir: Path) -> None:
    """Child mode: write every format from *brep* into *out_dir*."""
    import os

    from cad_mcp import export

    out_dir.mkdir(parents=True, exist_ok=True)
    for fmt in FORMATS:
        target = out_dir / f"model{export.FORMAT_EXTENSIONS[fmt]}"
        export.EXPORTERS[fmt](brep, target)
    _sentinel(out_dir).write_text("ok")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _run_child(args: list[str], label: str) -> None:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        capture_output=True,
        text=True,
    )
    # Exit code is deliberately not checked; see the module docstring.
    if proc.returncode not in (0, None):
        print(f"  note: {label} exited {proc.returncode} (teardown)")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(runs: int = 2, workdir: Path | None = None) -> dict[str, str]:
    """Export *runs* times in separate processes; return the hashes.

    Raises ``AssertionError`` naming every format that differed.
    """
    root = workdir or Path(tempfile.mkdtemp(prefix="cad-det-"))
    root.mkdir(parents=True, exist_ok=True)

    brep_dir = root / "brep"
    brep_dir.mkdir(parents=True, exist_ok=True)
    brep = brep_dir / "model.brep"
    # Reuse a B-rep a caller already built in this workdir: the shape is
    # fixed, so rebuilding it is a wasted CadQuery start-up.
    if not brep.exists():
        _run_child(["--build-brep", str(brep)], "brep build")
    if not _sentinel(brep_dir).exists() or not brep.exists():
        msg = "the B-rep build child did not complete"
        raise RuntimeError(msg)

    per_run: list[dict[str, str]] = []
    for index in range(runs):
        out_dir = root / f"run{index}"
        _run_child(["--export-all", str(brep), str(out_dir)], f"run {index}")
        if not _sentinel(out_dir).exists():
            msg = f"export child {index} did not complete"
            raise RuntimeError(msg)
        per_run.append(
            {
                fmt: _hash(out_dir / f"model{_ext(fmt)}")
                for fmt in FORMATS
            }
        )

    first = per_run[0]
    unstable = [
        fmt
        for fmt in FORMATS
        if any(run[fmt] != first[fmt] for run in per_run[1:])
    ]
    if unstable:
        detail = "\n".join(
            f"  {fmt}: " + " ".join(run[fmt][:16] for run in per_run)
            for fmt in unstable
        )
        msg = (
            "SPEC N4 violated -- these formats are not byte-stable across "
            f"processes:\n{detail}"
        )
        raise AssertionError(msg)
    return first


def _ext(fmt: str) -> str:
    from cad_mcp.export import FORMAT_EXTENSIONS

    return FORMAT_EXTENSIONS[fmt]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--json",
        type=Path,
        help="write {format: sha256} here, for cross-OS comparison in CI",
    )
    # Internal child modes.
    parser.add_argument("--build-brep", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--export-all", type=Path, nargs=2, help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    if args.build_brep is not None:
        _build_brep(args.build_brep)
        return 0
    if args.export_all is not None:
        _export_all(args.export_all[0], args.export_all[1])
        return 0

    try:
        hashes = check(runs=args.runs)
    except AssertionError as exc:
        print(exc)
        return 1

    print(f"SPEC N4: byte-identical across {args.runs} separate processes")
    for fmt in FORMATS:
        print(f"  {fmt:5} {hashes[fmt]}")
    if args.json:
        args.json.write_text(json.dumps(hashes, indent=2, sort_keys=True))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
