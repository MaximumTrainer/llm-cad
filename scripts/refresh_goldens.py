#!/usr/bin/env python3
"""Regenerate the golden perceptual hash for the bracket render.

Deliberately a separate, explicit step. A perceptual-hash mismatch means
the render changed; that is a question ("did I mean to change it?"), not
a failure to silence. So this script:

* writes the new render to a PNG you can actually look at;
* prints the old and new hashes and the distance between them;
* refuses to touch the test file unless you pass ``--write``.

Usage::

    uv run python scripts/refresh_goldens.py            # inspect only
    uv run python scripts/refresh_goldens.py --write    # update the test

Per-backend note: the golden is backend-specific. Regenerate on the
backend CI treats as authoritative, and say in the commit message what
visibly changed and why. See the `render-check` skill.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TEST_FILE = ROOT / "tests" / "test_render.py"


async def _render_bracket() -> bytes:
    from cad_mcp import session
    from cad_mcp.server import mcp

    sys.path.insert(0, str(ROOT))
    from tests.test_render import BRACKET_CODE

    session.cleanup_all()
    await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})
    result = await mcp.call_tool("render_views", {})
    image = next(c for c in result.content if c.type == "image")
    return base64.b64decode(image.data)  # type: ignore[union-attr]


def main() -> int:
    if not os.environ.get("CAD_MCP_OUTPUT_DIR"):
        os.environ["CAD_MCP_OUTPUT_DIR"] = tempfile.mkdtemp(
            prefix="cad-mcp-goldens-"
        )

    import imagehash
    from PIL import Image

    from cad_mcp import render

    png = asyncio.run(_render_bracket())
    image = Image.open(io.BytesIO(png))
    new_hash = str(imagehash.phash(image, hash_size=16))

    source = TEST_FILE.read_text(encoding="utf-8")
    match = re.search(
        r'BRACKET_GOLDEN_PHASH = \(\s*"([0-9a-f]+)"\s*"([0-9a-f]+)"\s*\)',
        source,
    )
    old_hash = (match.group(1) + match.group(2)) if match else ""

    out_png = ROOT / "golden-render.png"
    out_png.write_bytes(png)

    print(f"backend : {render.active_backend()}")
    print(f"platform: {sys.platform}")
    print(f"old hash: {old_hash or '(none found)'}")
    print(f"new hash: {new_hash}")
    if old_hash:
        distance = imagehash.hex_to_hash(new_hash) - imagehash.hex_to_hash(
            old_hash
        )
        print(f"distance: {distance}")
    print(f"\nrendered image written to {out_png} — LOOK AT IT before writing")

    if "--write" not in sys.argv:
        print("\nDry run. Re-run with --write to update tests/test_render.py.")
        return 0

    if not match:
        print("\nCould not find BRACKET_GOLDEN_PHASH to replace.")
        return 1

    half = len(new_hash) // 2
    replacement = (
        f'BRACKET_GOLDEN_PHASH = (\n'
        f'    "{new_hash[:half]}"\n'
        f'    "{new_hash[half:]}"\n'
        f')'
    )
    TEST_FILE.write_text(
        source[: match.start()] + replacement + source[match.end() :],
        encoding="utf-8",
    )
    print(f"\nUpdated {TEST_FILE.relative_to(ROOT)}")
    print("Say in the commit message what changed in the image and why.")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # OCP/VTK segfault on teardown (issue #10); exit with the real status.
    os._exit(code)
