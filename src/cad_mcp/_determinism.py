"""Pin the two things that stop exports being byte-reproducible.

SPEC N4 promises that the same code yields byte-identical exports in all
four formats, across separate processes and machines. Tessellation is
already deterministic, and STL and GLB come out byte-stable as trimesh
writes them. Two writers spoil it by embedding *when* and *where* the
export happened rather than *what* was exported:

* **STEP** records a wall-clock timestamp in the ``FILE_NAME`` header
  entity. Measured on a 50x30x10 box, that timestamp is the *only*
  difference between two runs -- 20 226 identical bytes either side of it.
* **3MF** requires a ``p:UUID`` on objects, items and the build element.
  trimesh calls :func:`uuid.uuid4` for each, so every write differs. The
  zip container is otherwise well-behaved: trimesh already zeroes the
  member timestamps to the 1980 epoch.

Both are pinned here rather than excluded from N4, because a build that
cannot be reproduced cannot be verified -- and a slicer profile pinned to
a 3MF hash is a real workflow.
"""
from __future__ import annotations

import re
import uuid
import zipfile
from pathlib import Path

# Fixed point in time written into every STEP header. The epoch is
# chosen because it is obviously a placeholder: a reader that sees
# 1970 knows it is looking at a reproducible build, not at a file
# somebody wrote in 1970.
STEP_TIMESTAMP = "1970-01-01T00:00:00"

# Namespace for the UUIDs substituted into 3MF documents. A fixed
# random UUID, as RFC 4122 intends a namespace to be.
_3MF_NAMESPACE = uuid.UUID("6f3d2a1e-9c47-5b8e-a0d3-7e1f4b2c8a95")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Every member of a canonical 3MF carries this timestamp. It is what
# trimesh already writes, and it is the lowest value the zip format can
# represent, so "no information" is encoded rather than a wrong date.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def pin_step_header(writer: object) -> None:
    """Fix the ``FILE_NAME`` timestamp on a transferred STEP model.

    Call after ``Transfer`` (the header does not exist before) and
    before ``Write``. *writer* is a ``STEPControl_Writer``; for the XCAF
    assembly path pass ``cafwriter.ChangeWriter()``.
    """
    from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
    from OCP.TCollection import TCollection_HAsciiString

    header = APIHeaderSection_MakeHeader(writer.Model())  # type: ignore[attr-defined]
    header.SetTimeStamp(TCollection_HAsciiString(STEP_TIMESTAMP))


def canonicalise_3mf(path: Path) -> None:
    """Rewrite a 3MF in place so its bytes depend only on its geometry.

    Random ``p:UUID`` values are replaced with UUID5s derived from the
    order in which they are first encountered, which keeps them unique
    within the document (all 3MF requires) while making them a function
    of the content. A UUID referenced from more than one place -- a build
    item pointing at an object -- maps to the same replacement, so the
    cross-references survive.

    The container is repacked with fixed member timestamps and a fixed
    ``create_system``; without the latter the same document packs
    differently on Windows and Linux, which would defeat the
    cross-platform half of N4.
    """
    with zipfile.ZipFile(path) as zf:
        # Sorting makes the numbering below independent of the order the
        # writer happened to emit members in.
        names = sorted(zf.namelist())
        payloads = {name: zf.read(name) for name in names}

    mapping: dict[str, str] = {}

    def _substitute(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = mapping.get(original)
        if replacement is None:
            replacement = str(
                uuid.uuid5(_3MF_NAMESPACE, f"cad-mcp/{len(mapping)}")
            )
            mapping[original] = replacement
        return replacement

    for name in names:
        raw = payloads[name]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # a thumbnail or other binary member
        payloads[name] = _UUID_RE.sub(_substitute, text).encode("utf-8")

    tmp = path.with_name(path.name + ".canonical")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 0 = MS-DOS/FAT. zipfile otherwise stamps 3 (Unix) on POSIX
            # and 0 on Windows, and that single byte per member is enough
            # to change the hash between CI runners.
            info.create_system = 0
            info.external_attr = 0
            out.writestr(info, payloads[name])

    tmp.replace(path)
