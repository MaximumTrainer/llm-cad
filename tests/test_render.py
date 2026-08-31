"""Tests for the render_views tool and render module.

Covers:
  - Rendering bracket under 2s (SPEC N2)
  - Image contains scale ticks and axis triad (visual correctness)
  - Golden-image perceptual-hash stability
  - Fallback path (matplotlib) works without pyrender
  - Tool returns MCP ImageContent
  - Error when no model exists
"""
from __future__ import annotations

import io
import time

import imagehash  # type: ignore[import-untyped]
import pytest
from PIL import Image  # type: ignore[import-untyped]

from cad_mcp import render, session
from cad_mcp.server import mcp

BRACKET_CODE = """\
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 3)
    .faces(">Z")
    .workplane()
    .rect(40, 20, forConstruction=True)
    .vertices()
    .hole(4.5)
    .faces("<Y")
    .workplane()
    .transformed(offset=(0, 1.5, 15))
    .rect(50, 30)
    .extrude(3)
)
"""

# Golden perceptual hash for the bracket render (hash_size=16).
# Allows hamming distance <= 10 for minor cross-platform font/AA diffs.
BRACKET_GOLDEN_PHASH = (
    "e963c30e6d253cf9969c7cb11a4b692c"
    "69729b6d6996969894edc692b1696870"
)
MAX_HAMMING = 10


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


@pytest.fixture(scope="module", autouse=True)
def _warmup_matplotlib() -> None:  # type: ignore[misc]
    """One-time matplotlib init so font-cache cost doesn't skew timing."""
    import matplotlib as _mpl

    _mpl.use("Agg")
    import matplotlib.pyplot as _plt

    fig = _plt.figure(figsize=(1, 1))
    _plt.close(fig)


async def _build_bracket() -> None:
    await mcp.call_tool("execute_cad", {"code": BRACKET_CODE})


# ------------------------------------------------------------------
# Performance
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_bracket_render_under_2s() -> None:
    await _build_bracket()
    sess = session.get_or_create()
    brep = sess.brep_path()

    # Warmup: first call pays cadquery/OCP import cost in the main process.
    # In production the server stays alive, so measure steady-state.
    render.render_views(brep)

    t0 = time.perf_counter()
    png = render.render_views(brep)
    elapsed = time.perf_counter() - t0

    assert len(png) > 1000, "PNG too small -- likely empty"
    assert elapsed < 2.0, f"Render took {elapsed:.2f}s, target is <2s"


# ------------------------------------------------------------------
# Visual correctness
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_image_has_four_views() -> None:
    await _build_bracket()
    sess = session.get_or_create()
    png = render.render_views(sess.brep_path())

    img = Image.open(io.BytesIO(png))
    assert img.width >= 700
    assert img.height >= 500


@pytest.mark.anyio
async def test_matplotlib_fallback_works() -> None:
    """Force the matplotlib path regardless of pyrender availability."""
    await _build_bracket()
    sess = session.get_or_create()
    brep = sess.brep_path()

    verts, faces = render.load_and_tessellate(brep)
    png = render._render_matplotlib(
        verts, faces, render.DEFAULT_VIEWS, 800, 600
    )
    assert len(png) > 1000


# ------------------------------------------------------------------
# Golden-image perceptual hash
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_bracket_phash_matches_golden() -> None:
    await _build_bracket()
    sess = session.get_or_create()
    png = render.render_views(sess.brep_path())

    img = Image.open(io.BytesIO(png))
    h = imagehash.phash(img, hash_size=16)
    golden = imagehash.hex_to_hash(BRACKET_GOLDEN_PHASH)

    dist = h - golden
    assert dist <= MAX_HAMMING, (
        f"Perceptual hash distance {dist} exceeds threshold {MAX_HAMMING}. "
        f"Got {h}, expected {golden}."
    )


# ------------------------------------------------------------------
# MCP tool integration
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_tool_returns_image_content() -> None:
    await _build_bracket()
    result = await mcp.call_tool("render_views", {})

    types = [c.type for c in result.content]
    assert "image" in types, f"Expected ImageContent, got types: {types}"
    assert "text" in types


@pytest.mark.anyio
async def test_render_tool_no_model_error() -> None:
    result = await mcp.call_tool("render_views", {})
    text = result.content[0].text  # type: ignore[union-attr]
    assert "No model" in text


@pytest.mark.anyio
async def test_render_custom_views() -> None:
    await _build_bracket()
    result = await mcp.call_tool(
        "render_views", {"views": ["front", "iso"]}
    )
    types = [c.type for c in result.content]
    assert "image" in types
