"""Mesh validation: watertight, manifold, wall thickness, overhang analysis.

Also provides assembly-level interference checking (SPEC 10.3 A6).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cad_mcp.render import load_and_tessellate

PLA_DENSITY_G_PER_MM3 = 1.24e-3  # 1.24 g/cm³


def _overhang_analysis(
    faces_arr: NDArray[np.int32],
    verts: NDArray[np.float64],
    max_overhang_deg: float,
) -> dict[str, Any]:
    """Analyse downward-facing triangles for overhang severity.

    Excludes faces on the build plate (minimum Z) since they rest
    on the bed and do not need support.
    """
    v0 = verts[faces_arr[:, 0]]
    v1 = verts[faces_arr[:, 1]]
    v2 = verts[faces_arr[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    norms: NDArray[np.float64] = np.linalg.norm(
        normals, axis=1, keepdims=True
    )
    norms[norms < 1e-12] = 1.0
    normals = normals / norms
    centroids = (v0 + v1 + v2) / 3.0

    nz = normals[:, 2]
    downward = nz < -1e-6

    min_z = float(verts[:, 2].min())
    on_bed = centroids[:, 2] < (min_z + 0.1)
    downward = downward & ~on_bed

    if not downward.any():
        return {
            "overhang_faces": 0,
            "total_downward_faces": 0,
            "max_overhang_angle_deg": 0.0,
            "threshold_deg": max_overhang_deg,
        }

    abs_nz = np.abs(nz[downward])
    threshold = math.cos(math.radians(max_overhang_deg))
    overhang_mask = abs_nz > threshold
    overhang_count = int(overhang_mask.sum())

    surface_angles = np.degrees(np.arccos(np.clip(abs_nz, 0.0, 1.0)))
    max_angle = 90.0 - float(surface_angles.min()) if len(surface_angles) else 0.0

    return {
        "overhang_faces": overhang_count,
        "total_downward_faces": int(downward.sum()),
        "max_overhang_angle_deg": round(max_angle, 1),
        "threshold_deg": max_overhang_deg,
    }


def _wall_thickness(
    verts: NDArray[np.float64],
    faces_arr: NDArray[np.int32],
    min_wall_mm: float,
    max_samples: int = 500,
) -> dict[str, Any]:
    """Estimate minimum wall thickness via opposite-normal proximity.

    For each sampled face centroid, finds the nearest centroid whose
    normal is roughly opposite (dot product < -0.3), then reports that
    distance as the wall thickness at that point.
    """
    from scipy.spatial import KDTree

    n_faces = len(faces_arr)
    if n_faces == 0:
        return {
            "min_mm": 0.0,
            "samples": 0,
            "violations": 0,
            "threshold_mm": min_wall_mm,
        }

    v0 = verts[faces_arr[:, 0]]
    v1 = verts[faces_arr[:, 1]]
    v2 = verts[faces_arr[:, 2]]
    centroids = (v0 + v1 + v2) / 3.0

    normals = np.cross(v1 - v0, v2 - v0)
    norms: NDArray[np.float64] = np.linalg.norm(
        normals, axis=1, keepdims=True
    )
    norms[norms < 1e-12] = 1.0
    normals = normals / norms

    rng = np.random.default_rng(42)
    sample_count = min(max_samples, n_faces)
    indices = rng.choice(n_faces, size=sample_count, replace=False)

    tree = KDTree(centroids)
    min_thickness = float("inf")
    violations = 0

    for idx in indices:
        pt = centroids[idx]
        n = normals[idx]

        dists, neighbors = tree.query(pt, k=min(50, n_faces))
        if not isinstance(dists, np.ndarray):
            dists = np.array([dists])
            neighbors = np.array([neighbors])

        for d, nb in zip(dists, neighbors, strict=False):
            if nb == idx or d < 0.01:
                continue
            dot = float(np.dot(n, normals[nb]))
            if dot < -0.3:
                if d < min_thickness:
                    min_thickness = d
                if d < min_wall_mm:
                    violations += 1
                break

    if min_thickness == float("inf"):
        min_thickness = 0.0

    return {
        "min_mm": round(min_thickness, 3),
        "samples": sample_count,
        "violations": violations,
        "threshold_mm": min_wall_mm,
    }


def validate(
    brep_path: Path,
    min_wall_mm: float = 1.2,
    max_overhang_deg: float = 45.0,
) -> dict[str, Any]:
    """Run full validation on a tessellated BREP mesh.

    Returns a dict with watertight, manifold, volume, mass,
    wall-thickness, and overhang results.
    """
    import manifold3d
    import trimesh

    verts, faces_arr = load_and_tessellate(brep_path)

    tri_mesh = trimesh.Trimesh(vertices=verts, faces=faces_arr)
    tri_mesh.merge_vertices()
    tri_mesh.fix_normals()
    watertight = bool(tri_mesh.is_watertight)

    merged_v = tri_mesh.vertices.astype(np.float32)
    merged_f = tri_mesh.faces.astype(np.uint32)
    m3d_mesh = manifold3d.Mesh(
        vert_properties=merged_v,
        tri_verts=merged_f,
    )
    manifold = manifold3d.Manifold(m3d_mesh)
    status = manifold.status()
    is_manifold = str(status) == "Error.NoError"

    volume_mm3 = float(tri_mesh.volume) if watertight else 0.0
    mass_pla_g = round(volume_mm3 * PLA_DENSITY_G_PER_MM3, 2)

    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    dims = maxs - mins

    wall = _wall_thickness(verts, faces_arr, min_wall_mm)
    overhangs = _overhang_analysis(faces_arr, verts, max_overhang_deg)

    issues: list[str] = []
    if not watertight:
        issues.append("Mesh is not watertight (open edges detected)")
    if not is_manifold:
        issues.append(f"Mesh is not manifold ({status})")
    if wall["violations"] > 0:
        issues.append(
            f"Wall thickness below {min_wall_mm}mm at "
            f"{wall['violations']} of {wall['samples']} sample points "
            f"(min: {wall['min_mm']}mm)"
        )
    if overhangs["overhang_faces"] > 0:
        issues.append(
            f"{overhangs['overhang_faces']} faces exceed "
            f"{max_overhang_deg}° overhang threshold"
        )

    return {
        "watertight": watertight,
        "manifold": is_manifold,
        "manifold_status": str(status),
        "triangle_count": len(faces_arr),
        "volume_mm3": round(volume_mm3, 2),
        "mass_pla_g": mass_pla_g,
        "bbox": {
            "min": [round(float(x), 3) for x in mins],
            "max": [round(float(x), 3) for x in maxs],
            "dimensions": [round(float(x), 3) for x in dims],
        },
        "wall_thickness": wall,
        "overhangs": overhangs,
        "issues": issues,
        "printable": len(issues) == 0,
    }


def check_interference(
    brep_a: Path,
    brep_b: Path,
    translate_a: tuple[float, float, float],
    rotate_a: tuple[float, float, float],
    translate_b: tuple[float, float, float],
    rotate_b: tuple[float, float, float],
) -> float:
    """Compute interference volume between two transformed parts.

    Returns the overlap volume in mm³.  A value > 0.01 indicates
    actual interference.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from cad_mcp.export import _load_ocp_shape, transform_ocp_shape

    shape_a = _load_ocp_shape(brep_a)
    shape_b = _load_ocp_shape(brep_b)
    shape_a = transform_ocp_shape(shape_a, translate_a, rotate_a)
    shape_b = transform_ocp_shape(shape_b, translate_b, rotate_b)

    common = BRepAlgoAPI_Common(shape_a, shape_b)
    if not common.IsDone():
        return 0.0

    intersection = common.Shape()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(intersection, props)
    return float(abs(props.Mass()))
