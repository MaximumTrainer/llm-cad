"""Convert a triangle mesh (GLB/OBJ/STL) to an OCP TopoDS_Shape via sewing.

The result is a tessellated B-rep (planar triangular faces sewn together),
NOT a NURBS solid.  Parametric operations like fillet and shell will fail
on these shapes -- this is expected for AI-generated organic meshes.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

logger = logging.getLogger(__name__)

# Sewing builds one OCP face per triangle in a Python loop, so cost is
# linear in triangle count with a large constant. target_polycount is
# caller-supplied and refine returns far denser meshes, so an unbounded
# import could occupy the server for minutes and gigabytes (CAD-028).
MAX_SEW_TRIANGLES = int(os.environ.get("CAD_MCP_MAX_SEW_TRIS", "20000"))


def glb_to_brep(glb_path: Path, brep_path: Path) -> dict[str, Any]:
    """Load a GLB, sew triangles into an OCP shape, write BREP.

    Returns mesh stats: vertex_count, face_count.
    """
    scene = trimesh.load(str(glb_path), force="scene")
    if isinstance(scene, trimesh.Scene):
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            msg = f"No triangle meshes found in {glb_path}"
            raise ValueError(msg)
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(scene, trimesh.Trimesh):
        mesh = scene
    else:
        msg = f"Unsupported geometry type: {type(scene)}"
        raise ValueError(msg)

    mesh.merge_vertices()
    mesh.fix_normals()

    original_faces = len(mesh.faces)
    simplified = False
    if original_faces > MAX_SEW_TRIANGLES:
        mesh = _decimate(mesh, MAX_SEW_TRIANGLES)
        simplified = True
        logger.warning(
            "mesh simplified from %d to %d triangles before sewing",
            original_faces,
            len(mesh.faces),
        )

    shape = _sew_mesh(mesh.vertices, mesh.faces)

    from OCP.BRepTools import BRepTools

    BRepTools.Write_s(shape, str(brep_path))

    lo = mesh.bounds[0]
    hi = mesh.bounds[1]
    return {
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
        "original_face_count": original_faces,
        "simplified": simplified,
        "max_sew_triangles": MAX_SEW_TRIANGLES,
        # gen_ai_mesh used to leave part.bbox as None, so list_parts
        # reported no bounding box for a part that had geometry (CAD-022).
        "bbox": {
            "xmin": round(float(lo[0]), 4),
            "ymin": round(float(lo[1]), 4),
            "zmin": round(float(lo[2]), 4),
            "xmax": round(float(hi[0]), 4),
            "ymax": round(float(hi[1]), 4),
            "zmax": round(float(hi[2]), 4),
        },
    }


def _decimate(mesh: Any, target_faces: int) -> Any:
    """Reduce triangle count before sewing, preferring quadric decimation."""
    for method in ("simplify_quadric_decimation", "simplify_quadratic_decimation"):
        simplifier = getattr(mesh, method, None)
        if simplifier is None:
            continue
        try:
            reduced = simplifier(face_count=target_faces)
        except Exception:
            continue
        if reduced is not None and len(reduced.faces) <= target_faces:
            return reduced

    # Last resort: keep a deterministic subset of faces. Lossy, but a
    # bounded import beats a server that stalls for minutes.
    rng = np.random.default_rng(0)
    keep = rng.choice(len(mesh.faces), size=target_faces, replace=False)
    keep.sort()
    return trimesh.Trimesh(
        vertices=mesh.vertices, faces=mesh.faces[keep], process=True
    )


def _sew_mesh(
    vertices: np.ndarray[Any, np.dtype[np.floating[Any]]],
    faces: np.ndarray[Any, np.dtype[np.integer[Any]]],
) -> Any:
    """Build an OCP shape by sewing triangular faces."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_Sewing,
    )
    from OCP.gp import gp_Pnt

    sew = BRepBuilderAPI_Sewing(1e-3)

    for tri in faces:
        pts = [
            gp_Pnt(float(vertices[i][0]), float(vertices[i][1]), float(vertices[i][2]))
            for i in tri
        ]
        wire = BRepBuilderAPI_MakePolygon(pts[0], pts[1], pts[2], True).Wire()
        face = BRepBuilderAPI_MakeFace(wire, True).Face()
        sew.Add(face)

    sew.Perform()
    return sew.SewedShape()
