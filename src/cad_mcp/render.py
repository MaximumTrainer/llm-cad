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


def _check_pyrender() -> bool:
    global _HAS_PYRENDER
    if _HAS_PYRENDER is not None:
        return _HAS_PYRENDER
    try:
        import os

        os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        import pyrender

        _r = pyrender.OffscreenRenderer(64, 64)
        _r.delete()
        _HAS_PYRENDER = True
    except Exception:
        _HAS_PYRENDER = False
    return _HAS_PYRENDER


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
    """
    import cadquery as cq
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    ocp_shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not BRepTools.Read_s(ocp_shape, str(brep_path), builder):
        msg = f"Failed to read BREP: {brep_path}"
        raise ValueError(msg)

    shape = cq.Shape(ocp_shape)
    raw_verts, raw_faces = shape.tessellate(tolerance, angular_tolerance)

    verts = np.array(
        [(v.x, v.y, v.z) for v in raw_verts], dtype=np.float64
    )
    faces = np.array(raw_faces, dtype=np.int32)
    return verts, faces


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


def _render_matplotlib(
    verts: NDArray[np.float64],
    faces: NDArray[np.int32],
    views: Sequence[str],
    width: int,
    height: int,
) -> bytes:
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import (  # type: ignore[import-untyped]
        Poly3DCollection,
    )

    fc = _face_colors(verts, faces)
    polygons = verts[faces]

    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    center = (mins + maxs) / 2.0
    half_span = float(max(maxs - mins)) * 0.65

    n = len(views)
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    dpi = 100
    fig = plt.figure(
        figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="white"
    )

    for idx, name in enumerate(views):
        ax: Any = fig.add_subplot(rows, cols, idx + 1, projection="3d")

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
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    all_verts = np.vstack([pm.verts for pm in part_meshes])
    mins = all_verts.min(axis=0)
    maxs = all_verts.max(axis=0)
    center = (mins + maxs) / 2.0
    half_span = float(max(maxs - mins)) * 0.65

    n = len(views)
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
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


def _render_pyrender(
    verts: NDArray[np.float64],
    faces: NDArray[np.int32],
    views: Sequence[str],
    width: int,
    height: int,
) -> bytes:
    import pyrender
    import trimesh
    from PIL import Image

    cell_w = width // 2
    cell_h = height // 2

    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.fix_normals()

    pr_mesh = pyrender.Mesh.from_trimesh(
        mesh,
        smooth=True,
        material=pyrender.MetallicRoughnessMaterial(
            baseColorFactor=[0.55, 0.65, 0.82, 1.0],
            metallicFactor=0.15,
            roughnessFactor=0.6,
        ),
    )

    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    center = (mins + maxs) / 2.0
    diag = float(np.linalg.norm(maxs - mins))
    cam_dist = diag * 1.8

    composite = Image.new("RGB", (width, height), (255, 255, 255))
    renderer = pyrender.OffscreenRenderer(cell_w, cell_h)

    try:
        for idx, name in enumerate(views):
            scene = pyrender.Scene(
                bg_color=[1.0, 1.0, 1.0, 1.0],
                ambient_light=[0.3, 0.3, 0.3],
            )
            scene.add(pr_mesh)
            scene.add(
                pyrender.DirectionalLight(color=[1, 1, 1], intensity=3.0),
                pose=np.eye(4),
            )

            pose = _pyrender_camera_pose(name, center, cam_dist)

            if name == "iso":
                cam: Any = pyrender.PerspectiveCamera(
                    yfov=math.radians(40)
                )
            else:
                cam = pyrender.OrthographicCamera(
                    xmag=diag * 0.6, ymag=diag * 0.6
                )
            scene.add(cam, pose=pose)

            color_img, _ = renderer.render(scene)
            cell = Image.fromarray(color_img)

            col = idx % 2
            row = idx // 2
            composite.paste(cell, (col * cell_w, row * cell_h))
    finally:
        renderer.delete()

    buf = io.BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()


def _render_assembly_pyrender(
    part_meshes: Sequence[PartMesh],
    views: Sequence[str],
    width: int,
    height: int,
) -> bytes:
    """Render multiple parts with distinct colors (pyrender)."""
    import pyrender
    import trimesh
    from PIL import Image

    cell_w = width // 2
    cell_h = height // 2

    pr_meshes = []
    for pm in part_meshes:
        mesh = trimesh.Trimesh(vertices=pm.verts, faces=pm.faces)
        mesh.fix_normals()
        rgba = [*pm.color, 1.0]
        pr_mesh = pyrender.Mesh.from_trimesh(
            mesh,
            smooth=True,
            material=pyrender.MetallicRoughnessMaterial(
                baseColorFactor=rgba,
                metallicFactor=0.15,
                roughnessFactor=0.6,
            ),
        )
        pr_meshes.append(pr_mesh)

    all_verts = np.vstack([pm.verts for pm in part_meshes])
    mins = all_verts.min(axis=0)
    maxs = all_verts.max(axis=0)
    center = (mins + maxs) / 2.0
    diag = float(np.linalg.norm(maxs - mins))
    cam_dist = diag * 1.8

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
                cam: Any = pyrender.PerspectiveCamera(
                    yfov=math.radians(40)
                )
            else:
                cam = pyrender.OrthographicCamera(
                    xmag=diag * 0.6, ymag=diag * 0.6
                )
            scene.add(cam, pose=pose)

            color_img, _ = renderer.render(scene)
            cell = Image.fromarray(color_img)

            col = idx % 2
            row = idx // 2
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
    """Render a multi-view PNG of the shape at *brep_path*.

    Returns raw PNG bytes.  Tries pyrender/EGL first, then matplotlib.
    """
    if views is None:
        views = DEFAULT_VIEWS

    bad = [v for v in views if v not in VIEW_ANGLES]
    if bad:
        msg = f"Unknown views: {bad}. Choose from {list(VIEW_ANGLES)}"
        raise ValueError(msg)

    verts, faces = load_and_tessellate(brep_path)

    if _check_pyrender():
        logger.info("Using pyrender backend")
        return _render_pyrender(verts, faces, views, width, height)

    logger.info("Using matplotlib backend (pyrender/EGL unavailable)")
    return _render_matplotlib(verts, faces, views, width, height)


def render_assembly(
    part_meshes: Sequence[PartMesh],
    views: Sequence[str] | None = None,
    width: int = 800,
    height: int = 600,
) -> bytes:
    """Render multiple parts with distinct colors as a multi-view PNG.

    Returns raw PNG bytes.
    """
    if views is None:
        views = DEFAULT_VIEWS

    bad = [v for v in views if v not in VIEW_ANGLES]
    if bad:
        msg = f"Unknown views: {bad}. Choose from {list(VIEW_ANGLES)}"
        raise ValueError(msg)

    if _check_pyrender():
        logger.info("Using pyrender backend (assembly)")
        return _render_assembly_pyrender(part_meshes, views, width, height)

    logger.info("Using matplotlib backend (assembly)")
    return _render_assembly_matplotlib(part_meshes, views, width, height)
