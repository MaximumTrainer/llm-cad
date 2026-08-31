"""Convert a triangle mesh (GLB/OBJ/STL) to an OCP TopoDS_Shape via sewing.

The result is a tessellated B-rep (planar triangular faces sewn together),
NOT a NURBS solid.  Parametric operations like fillet and shell will fail
on these shapes -- this is expected for AI-generated organic meshes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import trimesh


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

    shape = _sew_mesh(mesh.vertices, mesh.faces)

    from OCP.BRepTools import BRepTools

    BRepTools.Write_s(shape, str(brep_path))

    return {
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
    }


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
