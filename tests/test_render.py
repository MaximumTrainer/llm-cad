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

import base64
import io
import time

import imagehash  # type: ignore[import-untyped]
import numpy as np
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
async def test_matplotlib_fallback_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback must be exercisable on demand, not by luck.

    PLAN's Phase 2 gate asks for the EGL-absent path to be tested. It
    previously reached into a private single-part function that the tool
    never called; now it forces the backend through the same public entry
    point the tool uses.
    """
    monkeypatch.setenv("CAD_MCP_FORCE_BACKEND", "matplotlib")
    await _build_bracket()
    sess = session.get_or_create()

    assert render.active_backend() == "matplotlib"
    png = render.render_views(sess.brep_path())
    assert len(png) > 1000
    assert Image.open(io.BytesIO(png)).size == (800, 600)


@pytest.mark.anyio
async def test_grid_layout_matches_view_count() -> None:
    """Both backends must agree on layout for 1-4 views (CAD-010)."""
    assert render.grid_shape(1) == (1, 1)
    assert render.grid_shape(2) == (1, 2)
    assert render.grid_shape(3) == (2, 2)
    assert render.grid_shape(4) == (2, 2)

    await _build_bracket()
    for count in (1, 2, 3, 4):
        views = render.DEFAULT_VIEWS[:count]
        result = await mcp.call_tool("render_views", {"views": views})
        types = [c.type for c in result.content]
        assert "image" in types, f"{count} view(s) produced no image"


@pytest.mark.anyio
async def test_duplicate_views_are_collapsed() -> None:
    assert render.normalise_views(["front", "front", "top"]) == [
        "front",
        "top",
    ]


@pytest.mark.anyio
async def test_unknown_view_is_rejected_with_choices() -> None:
    with pytest.raises(ValueError, match="Choose from"):
        render.normalise_views(["sideways"])


@pytest.mark.anyio
async def test_oversized_request_is_clamped() -> None:
    """An unbounded width/height could exhaust memory (CAD-010)."""
    await _build_bracket()
    result = await mcp.call_tool(
        "render_views", {"width": 100_000, "height": 100_000}
    )
    detail = next(
        c.text for c in result.content if c.type == "text"  # type: ignore[union-attr]
    )
    assert "clamped" in detail
    image = next(c for c in result.content if c.type == "image")
    png = base64.b64decode(image.data)  # type: ignore[union-attr]
    width, height = Image.open(io.BytesIO(png)).size
    assert width <= render.MAX_WIDTH
    assert height <= render.MAX_HEIGHT


@pytest.mark.anyio
async def test_response_names_the_backend() -> None:
    """The model (and the developer) must know which renderer ran."""
    await _build_bracket()
    result = await mcp.call_tool("render_views", {})
    detail = next(
        c.text for c in result.content if c.type == "text"  # type: ignore[union-attr]
    )
    assert "Backend:" in detail
    assert render.active_backend() in detail


@pytest.mark.anyio
async def test_forcing_an_unavailable_backend_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forcing pyrender must not silently fall back to matplotlib.

    Otherwise a CI job that believes it is testing the pyrender path
    quietly tests matplotlib instead.
    """
    monkeypatch.setenv("CAD_MCP_FORCE_BACKEND", "pyrender")
    monkeypatch.setattr(render, "_HAS_PYRENDER", None)

    try:
        available = render._check_pyrender()
    except RuntimeError as exc:
        assert "unavailable" in str(exc)
        return
    assert available is True, (
        "forcing pyrender neither succeeded nor raised"
    )


# ------------------------------------------------------------------
# Golden-image perceptual hash
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_bracket_phash_matches_golden() -> None:
    await _build_bracket()
    # Drive the tool, not a helper: the tool is what ships (CAD-013).
    result = await mcp.call_tool("render_views", {})
    image = next(c for c in result.content if c.type == "image")
    png = base64.b64decode(image.data)  # type: ignore[union-attr]

    img = Image.open(io.BytesIO(png))
    h = imagehash.phash(img, hash_size=16)
    golden = imagehash.hex_to_hash(BRACKET_GOLDEN_PHASH)

    dist = h - golden
    assert dist <= MAX_HAMMING, (
        f"Perceptual hash distance {dist} exceeds threshold "
        f"{MAX_HAMMING} on the {render.active_backend()} backend. "
        f"Got {h}, expected {golden}. If the render changed "
        f"deliberately, inspect the image first, then regenerate with: "
        f"uv run python scripts/refresh_goldens.py"
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


# ------------------------------------------------------------------
# pyrender annotations (CAD-010)
#
# The pyrender backend cannot run here (no EGL on this platform), so the
# annotation drawing is tested directly against a synthetic cell. That
# covers the logic without pretending the GPU path was exercised; CI's
# osmesa job runs the real thing.
# ------------------------------------------------------------------


def _blank_cell(size: tuple[int, int] = (400, 300)) -> Image.Image:
    return Image.new("RGB", size, (255, 255, 255))


def _ink_pixels(image: Image.Image) -> int:
    return sum(1 for px in _pixels(image.convert("L")) if px < 200)


def _pixels(image: Image.Image) -> list[int]:
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return list(getter())


def test_annotation_draws_on_an_orthographic_cell() -> None:
    cell = _blank_cell()
    before = _ink_pixels(cell)
    render._annotate_cell(
        cell,
        "front",
        mins=np.array([-10.0, -5.0, 0.0]),
        maxs=np.array([10.0, 5.0, 20.0]),
        ortho_extent=15.0,
    )
    assert _ink_pixels(cell) > before + 200, (
        "no axes/ticks were drawn on the orthographic cell"
    )


def test_annotation_labels_the_view_and_units() -> None:
    """The title and 'mm' must be present — that is the scale cue."""
    cell = _blank_cell()
    render._annotate_cell(
        cell,
        "top",
        mins=np.array([0.0, 0.0, 0.0]),
        maxs=np.array([60.0, 40.0, 10.0]),
        ortho_extent=40.0,
    )
    # Compare against a cell annotated at a very different scale: the tick
    # labels must differ, which is what makes the render readable.
    other = _blank_cell()
    render._annotate_cell(
        other,
        "top",
        mins=np.array([0.0, 0.0, 0.0]),
        maxs=np.array([6.0, 4.0, 1.0]),
        ortho_extent=4.0,
    )
    assert _pixels(cell) != _pixels(other), (
        "a 60mm part and a 6mm part annotated identically — the ticks "
        "carry no scale information"
    )


def test_perspective_cell_gets_a_bbox_caption_and_triad() -> None:
    """Ticks would be wrong under perspective, so caption instead."""
    cell = _blank_cell()
    before = _ink_pixels(cell)
    render._annotate_cell(
        cell,
        "iso",
        mins=np.array([0.0, 0.0, 0.0]),
        maxs=np.array([30.0, 20.0, 10.0]),
        ortho_extent=None,
    )
    assert _ink_pixels(cell) > before + 100, "nothing drawn on the iso cell"


def test_view_axes_mapping_is_right() -> None:
    assert render._view_axes("front") == (0, 2)  # X right, Z up
    assert render._view_axes("right") == (1, 2)  # Y right, Z up
    assert render._view_axes("top") == (0, 1)    # X right, Y up
