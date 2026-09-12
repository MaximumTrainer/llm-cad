"""Export module: STEP from B-rep, STL/3MF/GLB from tessellation.

Supports single-part and multi-part (assembly) export.  STEP assembly
export uses XCAF for named shapes.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from cad_mcp.paths import safe_output_path
from cad_mcp.render import TESS_ANGULAR, TESS_LINEAR, load_and_tessellate


def _load_ocp_shape(brep_path: Path) -> Any:
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    ocp_shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not BRepTools.Read_s(ocp_shape, str(brep_path), builder):
        msg = f"Failed to read BREP: {brep_path}"
        raise ValueError(msg)
    return ocp_shape


def build_ocp_transform(
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
) -> Any:
    """Build a gp_Trsf from translate + Euler XYZ rotation (degrees)."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    rx, ry, rz = (math.radians(a) for a in rotate)
    origin = gp_Pnt(0, 0, 0)

    rx_t = gp_Trsf()
    rx_t.SetRotation(gp_Ax1(origin, gp_Dir(1, 0, 0)), rx)
    ry_t = gp_Trsf()
    ry_t.SetRotation(gp_Ax1(origin, gp_Dir(0, 1, 0)), ry)
    rz_t = gp_Trsf()
    rz_t.SetRotation(gp_Ax1(origin, gp_Dir(0, 0, 1)), rz)

    # rot = Rz * Ry * Rx
    rot = gp_Trsf()
    rot.Multiply(rz_t)
    rot.Multiply(ry_t)
    rot.Multiply(rx_t)

    # final = T * rot
    final = gp_Trsf()
    final.SetTranslation(gp_Vec(*translate))
    final.Multiply(rot)
    return final


def transform_ocp_shape(
    shape: Any,
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
) -> Any:
    """Apply Euler XYZ rotation + translation to an OCP shape."""
    if all(t == 0.0 for t in translate) and all(r == 0.0 for r in rotate):
        return shape

    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    trsf = build_ocp_transform(translate, rotate)
    transformer = BRepBuilderAPI_Transform(shape, trsf, True)
    return transformer.Shape()


def export_step(brep_path: Path, output_path: Path) -> Path:
    """Export B-rep directly to STEP via OCP (no tessellation loss)."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    ocp_shape = _load_ocp_shape(brep_path)

    writer = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    writer.Transfer(ocp_shape, STEPControl_AsIs)
    status = writer.Write(str(output_path))

    if status != IFSelect_RetDone:
        msg = f"STEP export failed with status {status}"
        raise RuntimeError(msg)

    return output_path


_PartStepData = tuple[
    str, Path, tuple[float, float, float], tuple[float, float, float]
]


def export_assembly_step(
    parts: list[_PartStepData],
    output_path: Path,
) -> Path:
    """Export multiple named parts to a single STEP file using XCAF."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("XBF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    for name, brep_path, translate, rotate in parts:
        ocp_shape = _load_ocp_shape(brep_path)
        transformed = transform_ocp_shape(ocp_shape, translate, rotate)
        label = shape_tool.AddShape(transformed)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc)
    status = writer.Write(str(output_path))

    if status != IFSelect_RetDone:
        msg = f"STEP assembly export failed with status {status}"
        raise RuntimeError(msg)

    return output_path


def export_stl(
    brep_path: Path,
    output_path: Path,
    tolerance: float = TESS_LINEAR,
    angular_tolerance: float = TESS_ANGULAR,
) -> Path:
    """Export to binary STL via deterministic tessellation."""
    import trimesh

    verts, faces = load_and_tessellate(brep_path, tolerance, angular_tolerance)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.export(str(output_path), file_type="stl")
    return output_path


def export_glb(
    brep_path: Path,
    output_path: Path,
    tolerance: float = TESS_LINEAR,
    angular_tolerance: float = TESS_ANGULAR,
) -> Path:
    """Export to GLB (binary glTF) via tessellation."""
    import trimesh

    verts, faces = load_and_tessellate(brep_path, tolerance, angular_tolerance)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.fix_normals()
    mesh.export(str(output_path), file_type="glb")
    return output_path


def export_3mf(
    brep_path: Path,
    output_path: Path,
    tolerance: float = TESS_LINEAR,
    angular_tolerance: float = TESS_ANGULAR,
) -> Path:
    """Export to 3MF via tessellation."""
    import trimesh

    verts, faces = load_and_tessellate(brep_path, tolerance, angular_tolerance)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.fix_normals()
    mesh.export(str(output_path), file_type="3mf")
    return output_path


def export_transformed_stl(
    brep_path: Path,
    output_path: Path,
    translate: tuple[float, float, float],
    rotate: tuple[float, float, float],
    tolerance: float = TESS_LINEAR,
) -> Path:
    """Export a single part to STL with transform applied."""
    import trimesh

    from cad_mcp.render import apply_transform

    verts, faces = load_and_tessellate(brep_path, tolerance)
    verts = apply_transform(verts, translate, rotate)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.export(str(output_path), file_type="stl")
    return output_path


def export_assembly_mesh(
    parts: list[tuple[Path, tuple[float, float, float], tuple[float, float, float]]],
    output_path: Path,
    fmt: str,
    tolerance: float = TESS_LINEAR,
) -> Path:
    """Export combined assembly to a mesh format (STL/3MF/GLB)."""
    import trimesh

    from cad_mcp.render import apply_transform

    meshes = []
    for brep_path, translate, rotate in parts:
        verts, faces = load_and_tessellate(brep_path, tolerance)
        verts = apply_transform(verts, translate, rotate)
        mesh = trimesh.Trimesh(vertices=verts, faces=faces)
        meshes.append(mesh)

    combined = trimesh.util.concatenate(meshes)
    combined.fix_normals()
    combined.export(str(output_path), file_type=fmt)
    return output_path


EXPORTERS = {
    "step": export_step,
    "stl": export_stl,
    "3mf": export_3mf,
    "glb": export_glb,
}

FORMAT_EXTENSIONS = {
    "step": ".step",
    "stl": ".stl",
    "3mf": ".3mf",
    "glb": ".glb",
}


def export_model(
    brep_path: Path,
    output_dir: Path,
    fmt: str,
    filename: str | None = None,
    tolerance: float = TESS_LINEAR,
) -> dict[str, Any]:
    """Export the model to the specified format.

    Returns ``{"path": ..., "size_bytes": ..., "format": ...}``.
    """
    fmt = fmt.lower()
    if fmt not in EXPORTERS:
        msg = f"Unsupported format '{fmt}'. Choose from: {list(EXPORTERS)}"
        raise ValueError(msg)

    ext = FORMAT_EXTENSIONS[fmt]
    if filename is None:
        filename = f"model{ext}"
    elif not filename.endswith(ext):
        filename = f"{filename}{ext}"

    output_dir.mkdir(parents=True, exist_ok=True)
    # Re-validated here so no caller can bypass the guard (CAD-003).
    output_path = safe_output_path(output_dir, filename)

    if fmt == "step":
        export_step(brep_path, output_path)
    elif fmt == "stl":
        export_stl(brep_path, output_path, tolerance)
    elif fmt == "3mf":
        export_3mf(brep_path, output_path, tolerance)
    elif fmt == "glb":
        export_glb(brep_path, output_path, tolerance)

    size = output_path.stat().st_size
    return {
        "path": str(output_path),
        "size_bytes": size,
        "format": fmt,
        "filename": filename,
    }
