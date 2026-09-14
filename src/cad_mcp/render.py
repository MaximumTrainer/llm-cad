"""Multi-view renderer for CadQuery shapes.

Loads a BREP file, tessellates it with fixed tolerances, and renders a
grid of orthographic/perspective views as a single PNG.  Tries pyrender
with EGL offscreen first; falls back to matplotlib 3D automatically.

Supports single-part rendering (``render_views``) and multi-part
assembly rendering (``render_assembly``) with per-part colors.
"""
from __future__ import annotations

import io
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# Fixed tolerances for deterministic tessellation (SPEC N4)
TESS_LINEAR = 0.1  # mm
TESS_ANGULAR = 0.1  # radians

# View definitions: (elevation_deg, azimuth_deg)
VIEW_ANGLES: dict[str, tuple[float, float]] = {
    "front": (0.0, 0.0),
    "right": (0.0, 90.0),
    "top": (90.0, 0.0),
    "iso": (30.0, -45.0),
}

DEFAULT_VIEWS: list[str] = ["front", "right", "top", "iso"]

COLOR_PALETTE: dict[str, tuple[float, float, float]] = {
    "steel": (0.55, 0.65, 0.82),
    "blue": (0.2, 0.4, 0.8),
    "red": (0.8, 0.2, 0.2),
    "green": (0.2, 0.7, 0.3),
    "orange": (0.9, 0.5, 0.1),
    "purple": (0.6, 0.3, 0.8),
}

_HAS_PYRENDER: bool | None = None


@dataclass
class PartMesh:
    """Tessellated part data for assembly rendering."""

    verts: NDArray[np.float64]
    faces: NDArray[np.int32]
    color: tuple[float, float, float]


# Maximum grid size. CLAUDE.md caps the image at 800x600; larger images
# cost render time for no extra information to the model.
MAX_WIDTH = 1600
MAX_HEIGHT = 1200
MIN_WIDTH = 200
MIN_HEIGHT = 150


def forced_backend() -> str | None:
    """`CAD_MCP_FORCE_BACKEND` = "pyrender" | "matplotlib", or unset.

    Lets CI and the `render-check` skill exercise a specific backend
    rather than silently testing whichever one happens to be installed.
    """
    import os

    value = os.environ.get("CAD_MCP_FORCE_BACKEND", "").strip().lower()
    return value or None


def _check_pyrender() -> bool:
    """Whether the pyrender/EGL backend can actually render.

    Failures are logged at WARNING with the underlying exception. They
    used to be swallowed, which made "pyrender is not installed"
    indistinguishable from "EGL is misconfigured" — and since pyrender
    was never a declared dependency, this returned False forever and the
    whole pyrender path went unexecuted (CAD-011).
    """
    global _HAS_PYRENDER

    forced = forced_backend()
    if forced == "matplotlib":
        return False

    if _HAS_PYRENDER is not None:
        return _HAS_PYRENDER
    try:
        import os

        os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        import pyrender

        renderer = pyrender.OffscreenRenderer(64, 64)
        renderer.delete()
        _HAS_PYRENDER = True
    except Exception as exc:
        if forced == "pyrender":
            # Explicitly requested: fail loudly rather than fall back and
            # pretend the pyrender path was tested.
            msg = (
                f"CAD_MCP_FORCE_BACKEND=pyrender but pyrender is "
                f"unavailable: {type(exc).__name__}: {exc}"
            )
            raise RuntimeError(msg) from exc
        logger.warning(
            "pyrender/EGL unavailable (%s: %s); using the matplotlib "
            "backend. Install the 'gpu' extra and a working EGL/OSMesa "
            "for GPU rendering.",
            type(exc).__name__,
            exc,
        )
        _HAS_PYRENDER = False
    return _HAS_PYRENDER


def active_backend() -> str:
    """Name of the backend that will be used, for reporting to the LLM."""
    return "pyrender" if _check_pyrender() else "matplotlib"


def known_backend() -> str | None:
    """The backend, if it has already been determined; otherwise None.

    `active_backend` settles the question by building a throwaway EGL
    context, which is the wrong thing to do inside `/health`: it is slow
    on first call and it runs on the event loop. `main()` settles it
    during pre-warm, so by the time the server reports ready this
    answers without probing.
    """
    if forced_backend() == "matplotlib":
        return "matplotlib"
    if _HAS_PYRENDER is None:
        return None
    return "pyrender" if _HAS_PYRENDER else "matplotlib"


def grid_shape(n_views: int) -> tuple[int, int]:
    """(rows, cols) for *n_views* cells, shared by both backends.

    The pyrender path used to hardcode two columns, so a 1- or 3-view
    request left blank quadrants and the two backends disagreed on
    layout (CAD-010).
    """
    if n_views <= 1:
        return 1, 1
    if n_views == 2:
        return 1, 2
    cols = 2
    rows = math.ceil(n_views / cols)
    return rows, cols


def normalise_views(views: Sequence[str] | None) -> list[str]:
    """Validate, de-duplicate and order the requested views."""
    if not views:
        return list(DEFAULT_VIEWS)
    seen: list[str] = []
    for name in views:
        key = str(name).strip().lower()
        if key not in VIEW_ANGLES:
            msg = (
                f"Unknown view '{name}'. Choose from {list(VIEW_ANGLES)}."
            )
            raise ValueError(msg)
        if key not in seen:
            seen.append(key)
    return seen


def clamp_size(width: int, height: int) -> tuple[int, int, str | None]:
    """Bound the image size. Returns (width, height, note-if-changed)."""
    w = max(MIN_WIDTH, min(int(width), MAX_WIDTH))
    h = max(MIN_HEIGHT, min(int(height), MAX_HEIGHT))
    if (w, h) != (int(width), int(height)):
        return w, h, (
            f"size clamped from {int(width)}x{int(height)} to {w}x{h} "
            f"(limits {MIN_WIDTH}x{MIN_HEIGHT}-{MAX_WIDTH}x{MAX_HEIGHT})"
        )
    return w, h, None


# ------------------------------------------------------------------
# Transform helpers
# ------------------------------------------------------------------


def apply_transform(
    verts: NDArray[np.float64],
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
) -> NDArray[np.float64]:
    """Apply Euler XYZ rotation (degrees) then translation to vertices."""
    if all(t == 0.0 for t in translate) and all(r == 0.0 for r in rotate):
        return verts

    rx, ry, rz = (math.radians(a) for a in rotate)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # Combined Rz * Ry * Rx
    r_mat = np.array([
        [cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
        [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
        [-sy, sx * cy, cx * cy],
    ])

    result: NDArray[np.float64] = (verts @ r_mat.T) + np.array(translate)
    return result


def color_rgb(name: str) -> tuple[float, float, float]:
    """Look up a named color, defaulting to steel gray."""
    return COLOR_PALETTE.get(name, COLOR_PALETTE["steel"])


# ------------------------------------------------------------------
# Tessellation
# ------------------------------------------------------------------


def load_and_tessellate(
    brep_path: Path,
    tolerance: float = TESS_LINEAR,
    angular_tolerance: float = TESS_ANGULAR,
) -> tuple[NDArray[np.float64], NDArray[np.int32]]:
    """Load a BREP file and tessellate it.

    Returns ``(vertices, faces)`` where *vertices* is *(N, 3)* float64
    and *faces* is *(M, 3)* int32 triangle indices.

    The tessellation itself happens in the isolated geometry worker, not
    here: OCCT meshing a degenerate face is a segfault, and taking the
    server down mid-conversation is not a failure mode the LLM can do
    anything with (issue #10, SPEC §7). Results are cached per B-rep and
    tolerance, so render, validate and export of one shape pay for one
    tessellation between them rather than three.
    """
    from cad_mcp import geometry

    return geometry.tessellate(brep_path, tolerance, angular_tolerance)


# ------------------------------------------------------------------
# Face shading helpers
# ------------------------------------------------------------------

_LIGHT_DIR = np.array([1.0, 0.8, 1.5])
_LIGHT_DIR = _LIGHT_DIR / float(np.linalg.norm(_LIGHT_DIR))
_AMBIENT = 0.35


def _face_colors(
    verts: NDArray[np.float64],
    faces: NDArray[np.int32],
    base_color: NDArray[np.float64] | None = None,
) -> NDArray[np.float64]:
    """Per-face RGB using Lambertian shading."""
    if base_color is None:
        base_color = np.array(COLOR_PALETTE["steel"])

    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1.0
    normals /= norms
    diffuse: NDArray[np.float64] = np.abs(normals @ _LIGHT_DIR)
    intensity = np.clip(_AMBIENT + (1.0 - _AMBIENT) * diffuse, 0.0, 1.0)
    return np.outer(intensity, base_color)


# ------------------------------------------------------------------
# Nice tick values
# ------------------------------------------------------------------


def _nice_ticks(
    lo: float, hi: float, max_ticks: int = 5
) -> NDArray[np.float64]:
    span = hi - lo
    if span < 1e-9:
        return np.array([lo])
    raw = span / max_ticks
    mag = 10.0 ** math.floor(math.log10(raw))
    s = mag
    for step in (1.0, 2.0, 5.0, 10.0):
        s = step * mag
        if span / s <= max_ticks:
            break
    start = math.ceil(lo / s) * s
    ticks = np.arange(start, hi + s * 0.01, s)
    return ticks[ticks <= hi + 1e-9]


# ------------------------------------------------------------------
# Matplotlib renderer (always-available fallback)
# ------------------------------------------------------------------


def _setup_axes(
    ax: Any,
    name: str,
    center: NDArray[np.float64],
    half_span: float,
) -> None:
    """Configure axis limits, ticks, labels, and triad for one view."""
    elev, azim = VIEW_ANGLES[name]
    ax.view_init(elev=elev, azim=azim)
    for setter, i in (
        (ax.set_xlim, 0),
        (ax.set_ylim, 1),
        (ax.set_zlim, 2),
    ):
        setter(center[i] - half_span, center[i] + half_span)

    for getter, tick_setter in (
        (ax.get_xlim, ax.set_xticks),
        (ax.get_ylim, ax.set_yticks),
        (ax.get_zlim, ax.set_zticks),
    ):
        lo, hi = getter()
        ticks = _nice_ticks(lo, hi, max_ticks=4)
        tick_setter(ticks)

    ax.set_xlabel("X mm", fontsize=6, labelpad=-1)
    ax.set_ylabel("Y mm", fontsize=6, labelpad=-1)
    ax.set_zlabel("Z mm", fontsize=6, labelpad=-1)
    ax.tick_params(labelsize=5, pad=-2)
    ax.set_title(name.capitalize(), fontsize=9, fontweight="bold", pad=1)

    _draw_triad(ax, center, half_span)


def _render_assembly_matplotlib(
    part_meshes: Sequence[PartMesh],
    views: Sequence[str],
    width: int,
    height: int,
) -> bytes:
    """Render multiple parts with distinct colors (matplotlib)."""
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import (  # type: ignore[import-untyped]
        Poly3DCollection,
    )

    all_verts = np.vstack([pm.verts for pm in part_meshes])
    mins = all_verts.min(axis=0)
    maxs = all_verts.max(axis=0)
    center = (mins + maxs) / 2.0
    half_span = float(max(maxs - mins)) * 0.65

    rows, cols = grid_shape(len(views))
    dpi = 100
    fig = plt.figure(
        figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="white"
    )

    for idx, name in enumerate(views):
        ax: Any = fig.add_subplot(rows, cols, idx + 1, projection="3d")

        for pm in part_meshes:
            base = np.array(pm.color)
            fc = _face_colors(pm.verts, pm.faces, base)
            polygons = pm.verts[pm.faces]
            pc = Poly3DCollection(polygons, linewidths=0.15)
            pc.set_facecolor(fc)
            pc.set_edgecolor((0.3, 0.3, 0.3, 0.12))
            ax.add_collection3d(pc)

        _setup_axes(ax, name, center, half_span)

    fig.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _draw_triad(
    ax: Any,
    center: NDArray[np.float64],
    half_span: float,
) -> None:
    """Draw a small X/Y/Z axis triad in the scene."""
    origin = center - half_span * 0.82
    length = half_span * 0.18
    dirs = np.eye(3) * length
    colors = ["#DD3333", "#33AA33", "#3333DD"]
    labels = ["X", "Y", "Z"]
    for d, c, lab in zip(dirs, colors, labels, strict=True):
        end = origin + d
        ax.plot(
            [origin[0], end[0]],
            [origin[1], end[1]],
            [origin[2], end[2]],
            color=c,
            linewidth=2.0,
            solid_capstyle="round",
        )
        ax.text(
            end[0], end[1], end[2], lab,
            color=c, fontsize=6, fontweight="bold",
        )


# ------------------------------------------------------------------
# Pyrender renderer (preferred when EGL is available)
# ------------------------------------------------------------------


def _annotate_cell(
    image: Any,
    name: str,
    mins: NDArray[np.float64],
    maxs: NDArray[np.float64],
    ortho_extent: float | None,
) -> None:
    """Draw the title, axis labels and mm ticks onto one pyrender cell.

    SPEC 5.1 requires axes and mm scale ticks. The pyrender path drew
    neither, so the preferred backend gave the model a picture with no
    sense of scale — the exact failure `measure` exists to catch and a
    render is supposed to prevent (CAD-010).

    For an orthographic view the mapping from millimetres to pixels is
    exact (`xmag`/`ymag` define the visible half-extent), so real ticks
    can be drawn. The perspective iso view gets a bounding-box caption
    instead of ticks that would be wrong.
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    w, h = image.size
    ink = (40, 40, 40)
    faint = (120, 120, 120)

    draw.text((6, 4), name.capitalize(), fill=ink)

    dims = maxs - mins
    if ortho_extent is None or ortho_extent <= 0:
        draw.text(
            (6, h - 14),
            f"bbox {dims[0]:.1f} x {dims[1]:.1f} x {dims[2]:.1f} mm",
            fill=ink,
        )
        _draw_triad_2d(draw, w, h)
        return

    # Horizontal/vertical world axes visible in this view.
    h_axis, v_axis = _view_axes(name)
    labels = "XYZ"

    # Orthographic camera: full visible width is 2 * xmag millimetres.
    mm_per_px_x = (2.0 * ortho_extent) / max(w, 1)
    mm_per_px_y = (2.0 * ortho_extent) / max(h, 1)
    centre = (mins + maxs) / 2.0

    lo_x = centre[h_axis] - (w / 2.0) * mm_per_px_x
    hi_x = centre[h_axis] + (w / 2.0) * mm_per_px_x
    lo_y = centre[v_axis] - (h / 2.0) * mm_per_px_y
    hi_y = centre[v_axis] + (h / 2.0) * mm_per_px_y

    baseline = h - 16
    draw.line([(0, baseline), (w, baseline)], fill=faint)
    for value in _nice_ticks(lo_x, hi_x, max_ticks=5):
        px = int((value - lo_x) / max(hi_x - lo_x, 1e-9) * w)
        draw.line([(px, baseline - 4), (px, baseline + 4)], fill=ink)
        draw.text((px + 2, baseline + 3), f"{value:g}", fill=ink)

    draw.line([(18, 0), (18, baseline)], fill=faint)
    for value in _nice_ticks(lo_y, hi_y, max_ticks=4):
        # Screen y grows downward.
        py = int(baseline - (value - lo_y) / max(hi_y - lo_y, 1e-9) * baseline)
        draw.line([(14, py), (22, py)], fill=ink)
        draw.text((24, py - 6), f"{value:g}", fill=ink)

    draw.text((w - 58, baseline + 3), f"{labels[h_axis]} mm", fill=ink)
    draw.text((2, 16), f"{labels[v_axis]} mm", fill=ink)


def _view_axes(name: str) -> tuple[int, int]:
    """(horizontal, vertical) world axis indices shown by a named view."""
    return {
        "front": (0, 2),  # looking along -Y: X right, Z up
        "right": (1, 2),  # looking along -X: Y right, Z up
        "top": (0, 1),    # looking down -Z: X right, Y up
    }.get(name, (0, 2))


def _draw_triad_2d(draw: Any, w: int, h: int) -> None:
    """A small X/Y/Z legend for the perspective view."""
    ox, oy, length = 16, h - 26, 18
    draw.line([(ox, oy), (ox + length, oy)], fill=(200, 40, 40), width=2)
    draw.text((ox + length + 2, oy - 6), "X", fill=(200, 40, 40))
    draw.line([(ox, oy), (ox + 12, oy - 12)], fill=(40, 150, 40), width=2)
    draw.text((ox + 14, oy - 22), "Y", fill=(40, 150, 40))
    draw.line([(ox, oy), (ox, oy - length)], fill=(40, 40, 200), width=2)
    draw.text((ox + 2, oy - length - 12), "Z", fill=(40, 40, 200))


def _pyrender_camera_pose(
    name: str,
    center: NDArray[np.float64],
    cam_dist: float,
) -> NDArray[np.float64]:
    """Compute a 4x4 camera pose for a named view."""
    elev_rad = math.radians(VIEW_ANGLES[name][0])
    azim_rad = math.radians(VIEW_ANGLES[name][1])

    cx = cam_dist * math.cos(elev_rad) * math.sin(azim_rad)
    cy = -cam_dist * math.cos(elev_rad) * math.cos(azim_rad)
    cz = cam_dist * math.sin(elev_rad)
    eye = center + np.array([cx, cy, cz])

    up = np.array([0.0, 0.0, 1.0])
    if abs(elev_rad) > 1.4:
        up = np.array([0.0, 1.0, 0.0])

    fwd = center - eye
    fwd_n = fwd / (float(np.linalg.norm(fwd)) + 1e-12)
    right = np.cross(up, -fwd_n)
    right /= float(np.linalg.norm(right)) + 1e-12
    new_up = np.cross(-fwd_n, right)

    pose = np.eye(4)
    pose[:3, 0] = right
    pose[:3, 1] = new_up
    pose[:3, 2] = -fwd_n
    pose[:3, 3] = eye
    return pose


def _render_assembly_pyrender(
    part_meshes: Sequence[PartMesh],
    views: Sequence[str],
    width: int,
    height: int,
) -> bytes:
    """Render parts with distinct colors via pyrender, then annotate."""
    import pyrender
    import trimesh
    from PIL import Image

    rows, cols = grid_shape(len(views))
    cell_w = width // cols
    cell_h = height // rows

    pr_meshes = []
    for pm in part_meshes:
        mesh = trimesh.Trimesh(vertices=pm.verts, faces=pm.faces)
        mesh.fix_normals()
        pr_meshes.append(
            pyrender.Mesh.from_trimesh(
                mesh,
                smooth=True,
                material=pyrender.MetallicRoughnessMaterial(
                    baseColorFactor=[*pm.color, 1.0],
                    metallicFactor=0.15,
                    roughnessFactor=0.6,
                ),
            )
        )

    all_verts = np.vstack([pm.verts for pm in part_meshes])
    mins = all_verts.min(axis=0)
    maxs = all_verts.max(axis=0)
    center = (mins + maxs) / 2.0
    diag = float(np.linalg.norm(maxs - mins))
    cam_dist = diag * 1.8
    ortho_mag = diag * 0.6

    composite = Image.new("RGB", (width, height), (255, 255, 255))
    renderer = pyrender.OffscreenRenderer(cell_w, cell_h)

    try:
        for idx, name in enumerate(views):
            scene = pyrender.Scene(
                bg_color=[1.0, 1.0, 1.0, 1.0],
                ambient_light=[0.3, 0.3, 0.3],
            )
            for pr_mesh in pr_meshes:
                scene.add(pr_mesh)
            scene.add(
                pyrender.DirectionalLight(color=[1, 1, 1], intensity=3.0),
                pose=np.eye(4),
            )

            pose = _pyrender_camera_pose(name, center, cam_dist)
            if name == "iso":
                cam: Any = pyrender.PerspectiveCamera(yfov=math.radians(40))
                extent: float | None = None
            else:
                cam = pyrender.OrthographicCamera(
                    xmag=ortho_mag, ymag=ortho_mag
                )
                extent = ortho_mag
            scene.add(cam, pose=pose)

            color_img, _ = renderer.render(scene)
            cell = Image.fromarray(color_img)
            # SPEC 5.1: axes and mm ticks, which this backend omitted.
            _annotate_cell(cell, name, mins, maxs, extent)

            col = idx % cols
            row = idx // cols
            composite.paste(cell, (col * cell_w, row * cell_h))
    finally:
        renderer.delete()

    buf = io.BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def render_views(
    brep_path: Path,
    views: Sequence[str] | None = None,
    width: int = 800,
    height: int = 600,
) -> bytes:
    """Render a single shape. Thin wrapper over :func:`render_assembly`.

    Kept because it is a convenient entry point, but it no longer has its
    own rendering code: the duplicate single-part path was what the
    golden-image test exercised while the tool called the assembly path,
    so the one test guarding the product's core output guarded a function
    no user could reach (CAD-013).
    """
    verts, faces = load_and_tessellate(brep_path)
    mesh = PartMesh(
        verts=verts, faces=faces, color=COLOR_PALETTE["steel"]
    )
    return render_assembly([mesh], views, width, height)


def render_assembly(
    part_meshes: Sequence[PartMesh],
    views: Sequence[str] | None = None,
    width: int = 800,
    height: int = 600,
) -> bytes:
    """Render parts with distinct colors as a multi-view PNG."""
    chosen = normalise_views(views)
    width, height, _ = clamp_size(width, height)

    if _check_pyrender():
        logger.info("Using pyrender backend (assembly)")
        return _render_assembly_pyrender(part_meshes, chosen, width, height)

    logger.info("Using matplotlib backend (assembly)")
    return _render_assembly_matplotlib(part_meshes, chosen, width, height)
