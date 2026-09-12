"""Mesh validation: watertight, manifold, wall thickness, overhang analysis.

Also provides assembly-level interference checking (SPEC 10.3 A6).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cad_mcp.render import TESS_LINEAR, load_and_tessellate

logger = logging.getLogger(__name__)

PLA_DENSITY_G_PER_MM3 = 1.24e-3  # 1.24 g/cm³


def _overhang_analysis(
    faces_arr: NDArray[np.int32],
    verts: NDArray[np.float64],
    max_overhang_deg: float,
    max_regions: int = 5,
) -> dict[str, Any]:
    """Find and locate unsupported overhangs.

    SPEC 5.1 asks for overhang *regions*. The previous version returned
    counts only — "412 faces exceed 45 degrees" tells the LLM a problem
    exists but not where, so its only available fix is a blind global
    change (CAD-016). Offending faces are now clustered into connected
    regions, each reported with a location, area and worst angle.

    Angle convention: measured from vertical, so 0 deg is a vertical wall
    and 90 deg is a horizontal ceiling. A face is an overhang when its
    angle exceeds *max_overhang_deg*.
    """
    v0 = verts[faces_arr[:, 0]]
    v1 = verts[faces_arr[:, 1]]
    v2 = verts[faces_arr[:, 2]]
    cross = np.cross(v1 - v0, v2 - v0)
    areas = np.linalg.norm(cross, axis=1) / 2.0
    norms: NDArray[np.float64] = np.linalg.norm(
        cross, axis=1, keepdims=True
    )
    norms[norms < 1e-12] = 1.0
    normals = cross / norms
    centroids = (v0 + v1 + v2) / 3.0

    nz = normals[:, 2]
    downward = nz < -1e-6

    # Faces resting on the build plate need no support. The tolerance is
    # derived from the tessellation tolerance rather than a magic 0.1:
    # below that, a "flat" face is indistinguishable from faceting.
    bed_tol = TESS_LINEAR * 2.0
    min_z = float(verts[:, 2].min())
    on_bed = centroids[:, 2] < (min_z + bed_tol)
    candidates = downward & ~on_bed

    base = {
        "overhang_faces": 0,
        "total_downward_faces": int(candidates.sum()),
        "max_overhang_angle_deg": 0.0,
        "threshold_deg": max_overhang_deg,
        "angle_convention": (
            "degrees from vertical: 0 = vertical wall, 90 = horizontal "
            "ceiling; flagged when greater than threshold_deg"
        ),
        "bed_tolerance_mm": round(bed_tol, 3),
        "regions": [],
    }
    if not candidates.any():
        return base

    # Angle from vertical for each downward face.
    angles = np.degrees(np.arccos(np.clip(np.abs(nz), 0.0, 1.0)))
    overhang_angles = 90.0 - angles  # 90 for a flat ceiling, 0 for a wall
    flagged = candidates & (overhang_angles > max_overhang_deg)

    base["max_overhang_angle_deg"] = round(
        float(overhang_angles[candidates].max()), 1
    )
    if not flagged.any():
        return base

    flagged_idx = np.flatnonzero(flagged)
    base["overhang_faces"] = int(flagged_idx.size)
    base["regions"] = _cluster_regions(
        faces_arr, centroids, areas, overhang_angles, flagged_idx,
        max_regions,
    )
    return base


def _cluster_regions(
    faces_arr: NDArray[np.int32],
    centroids: NDArray[np.float64],
    areas: NDArray[np.float64],
    overhang_angles: NDArray[np.float64],
    flagged_idx: NDArray[np.intp],
    max_regions: int,
) -> list[dict[str, Any]]:
    """Group flagged faces into connected regions, worst area first.

    Connectivity is shared-vertex adjacency among the flagged faces only,
    walked with a union-find, so a single ceiling comes back as one region
    rather than hundreds of triangles.
    """
    parent = {int(i): int(i) for i in flagged_idx}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Map each vertex to the flagged faces touching it.
    by_vertex: dict[int, list[int]] = {}
    for face_i in flagged_idx:
        for vertex in faces_arr[face_i]:
            by_vertex.setdefault(int(vertex), []).append(int(face_i))
    for touching in by_vertex.values():
        first = touching[0]
        for other in touching[1:]:
            union(first, other)

    groups: dict[int, list[int]] = {}
    for face_i in flagged_idx:
        groups.setdefault(find(int(face_i)), []).append(int(face_i))

    regions: list[dict[str, Any]] = []
    for members in groups.values():
        idx = np.array(members, dtype=np.intp)
        pts = centroids[idx]
        area = float(areas[idx].sum())
        worst = float(overhang_angles[idx].max())
        centre = pts.mean(axis=0)
        regions.append(
            {
                "face_count": int(idx.size),
                "area_mm2": round(area, 2),
                "worst_angle_deg": round(worst, 1),
                "centroid": {
                    "x": round(float(centre[0]), 2),
                    "y": round(float(centre[1]), 2),
                    "z": round(float(centre[2]), 2),
                },
                "bbox": {
                    "min": [round(float(v), 2) for v in pts.min(axis=0)],
                    "max": [round(float(v), 2) for v in pts.max(axis=0)],
                },
                "description": (
                    f"unsupported overhang, {worst:.0f} deg, "
                    f"{area:.1f} mm2 near "
                    f"({centre[0]:.1f}, {centre[1]:.1f}, {centre[2]:.1f})"
                ),
            }
        )

    regions.sort(key=lambda r: (-r["area_mm2"], -r["worst_angle_deg"]))
    return regions[:max_regions]


def _wall_thickness(
    verts: NDArray[np.float64],
    faces_arr: NDArray[np.int32],
    min_wall_mm: float,
    max_samples: int = 400,
) -> dict[str, Any]:
    """Measure wall thickness by casting a ray into the material.

    The previous implementation found, for each sampled face centroid, the
    nearest *other* centroid whose normal was roughly opposite, and called
    that distance the wall thickness. That geometry describes two surfaces
    facing each other — which is a wall when the material is between them
    and a **gap** when the air is. A 0.5mm slot, the clearance between a
    lid and a box, or the inside of a narrow pocket all read as
    sub-minimum "walls" and produced a false printability failure, which
    then sent the LLM to thicken geometry that was already correct
    (CAD-015).

    Casting a ray from just inside the surface, along the inward normal,
    and taking the first hit measures material. Sampling is seeded so the
    estimate is reproducible.
    """
    import trimesh

    n_faces = len(faces_arr)
    empty = {
        "min_mm": 0.0,
        "samples": 0,
        "violations": 0,
        "threshold_mm": min_wall_mm,
        "method": "inward ray cast to first back-face",
        "thinnest_point": None,
    }
    if n_faces == 0:
        return empty

    mesh = trimesh.Trimesh(vertices=verts, faces=faces_arr, process=False)
    mesh.fix_normals()

    centroids = mesh.triangles_center
    normals = np.asarray(mesh.face_normals, dtype=np.float64)

    rng = np.random.default_rng(42)
    sample_count = min(max_samples, n_faces)
    idx = rng.choice(n_faces, size=sample_count, replace=False)

    # Start just inside the surface so the ray does not re-hit its origin
    # face, and travel inward.
    epsilon = max(TESS_LINEAR * 0.5, 1e-4)
    origins = centroids[idx] - normals[idx] * epsilon
    directions = -normals[idx]

    try:
        locations, ray_ids, _tri = mesh.ray.intersects_location(
            ray_origins=origins,
            ray_directions=directions,
            multiple_hits=False,
        )
    except Exception:
        logger.warning(
            "ray intersection unavailable; wall thickness not measured"
        )
        return {**empty, "method": "unavailable"}

    if len(ray_ids) == 0:
        return {**empty, "samples": sample_count}

    distances = np.linalg.norm(locations - origins[ray_ids], axis=1)
    # A hit essentially at the origin is numerical noise, not a wall.
    keep = distances > epsilon * 2
    distances = distances[keep]
    hit_rays = ray_ids[keep]

    if len(distances) == 0:
        return {**empty, "samples": sample_count}

    thinnest = int(np.argmin(distances))
    min_thickness = float(distances[thinnest])
    point = origins[hit_rays[thinnest]]

    return {
        "min_mm": round(min_thickness, 3),
        "samples": sample_count,
        "measured": len(distances),
        "violations": int((distances < min_wall_mm).sum()),
        "threshold_mm": min_wall_mm,
        "method": "inward ray cast to first back-face",
        # Where to look, so the LLM can act rather than guess (CAD-015).
        "thinnest_point": {
            "x": round(float(point[0]), 2),
            "y": round(float(point[1]), 2),
            "z": round(float(point[2]), 2),
        },
        "note": (
            f"Estimate from {len(distances)} sampled points; accurate to "
            f"about the tessellation tolerance ({TESS_LINEAR}mm)."
        ),
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
        where = "; ".join(
            r["description"] for r in overhangs.get("regions", [])[:3]
        )
        issues.append(
            f"{len(overhangs.get('regions', []))} overhang region(s) "
            f"exceed {max_overhang_deg}deg "
            f"({overhangs['overhang_faces']} faces)"
            + (f": {where}" if where else "")
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
