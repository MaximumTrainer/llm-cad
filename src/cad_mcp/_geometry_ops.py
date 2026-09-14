"""The OCP-touching bodies, collected so only the worker imports them.

These moved out of ``export.py``, ``tools/measure.py`` and ``validate.py``
unchanged in behaviour. The point is location, not logic: every function
here runs inside ``_geometry_worker``, where a segfault costs a
subprocess rather than the server (issue #10, SPEC §7).

Nothing in this module may be imported from the server process. Doing so
loads OCP back into the address space the isolation exists to protect,
and ``tests/test_geometry_isolation.py`` fails if anything does.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def load_shape(brep_path: str | Path) -> Any:
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not BRepTools.Read_s(shape, str(brep_path), builder):
        msg = f"Failed to read BREP: {brep_path}"
        raise ValueError(msg)
    return shape


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
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


# ------------------------------------------------------------------
# measure
# ------------------------------------------------------------------


def measure_bbox(args: dict[str, Any]) -> dict[str, Any]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    shape = load_shape(args["brep_path"])
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return {
        "min": {"x": round(xmin, 3), "y": round(ymin, 3), "z": round(zmin, 3)},
        "max": {"x": round(xmax, 3), "y": round(ymax, 3), "z": round(zmax, 3)},
        "dimensions": {
            "x": round(xmax - xmin, 3),
            "y": round(ymax - ymin, 3),
            "z": round(zmax - zmin, 3),
        },
        "unit": "mm",
    }


def measure_volume(args: dict[str, Any]) -> dict[str, Any]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(load_shape(args["brep_path"]), props)
    return {"volume_mm3": round(props.Mass(), 3), "unit": "mm³"}


def measure_faces(args: dict[str, Any]) -> dict[str, Any]:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(load_shape(args["brep_path"]), TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return {"face_count": count}


SELECTOR_KINDS = ("faces", "edges", "vertices")


def _select(wp: Any, selector: str, kind: str) -> tuple[Any, int]:
    """Resolve a selector to (shape, match_count) for a given kind."""
    selected = getattr(wp, kind)(selector)
    values = selected.vals()
    if not values:
        msg = f"selector {selector!r} matched no {kind}"
        raise ValueError(msg)
    return selected, len(values)


def resolve_selector(
    wp: Any, selector: str, kind: str | None
) -> tuple[Any, str, int]:
    """Resolve against an explicit kind, or try faces then edges then vertices.

    Returns ``(selection, kind_used, match_count)``.
    """
    if kind:
        if kind not in SELECTOR_KINDS:
            msg = (
                f"Unknown selector kind {kind!r}. "
                f"Choose from {list(SELECTOR_KINDS)}."
            )
            raise ValueError(msg)
        selection, count = _select(wp, selector, kind)
        return selection, kind, count

    errors = []
    for candidate in SELECTOR_KINDS:
        try:
            selection, count = _select(wp, selector, candidate)
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        return selection, candidate, count
    msg = (
        f"selector {selector!r} matched nothing as a face, edge or "
        f"vertex ({'; '.join(errors)})"
    )
    raise ValueError(msg)


def _workplane(brep_path: str) -> Any:
    import cadquery as cq

    return cq.Workplane("XY").newObject([cq.Shape(load_shape(brep_path))])


def _point(p: Any) -> dict[str, float]:
    return {"x": round(p.X(), 3), "y": round(p.Y(), 3), "z": round(p.Z(), 3)}


def measure_distance(args: dict[str, Any]) -> dict[str, Any]:
    """True minimum distance between two selected entities.

    This used to return the distance between the two entities' *centroids*.
    For parallel planar faces that happens to equal the thickness; for a
    cylindrical bore, a filleted face, or a selector matching several
    entities it is a number with no physical meaning (CAD-018).
    """
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    wp = _workplane(args["brep_path"])
    from_sel, from_used, from_count = resolve_selector(
        wp, args["from_selector"], args.get("from_kind")
    )
    to_sel, to_used, to_count = resolve_selector(
        wp, args["to_selector"], args.get("to_kind")
    )

    tool = BRepExtrema_DistShapeShape(
        from_sel.val().wrapped, to_sel.val().wrapped
    )
    if not tool.IsDone():
        msg = "minimum-distance computation failed"
        raise ValueError(msg)

    result: dict[str, Any] = {
        "distance_mm": round(tool.Value(), 4),
        "from": {
            "selector": args["from_selector"],
            "kind": from_used,
            "matched": from_count,
        },
        "to": {
            "selector": args["to_selector"],
            "kind": to_used,
            "matched": to_count,
        },
        "unit": "mm",
    }
    if from_count > 1 or to_count > 1:
        result["note"] = (
            f"selector matched {from_count} {from_used} and "
            f"{to_count} {to_used}; the distance is to the nearest of them"
        )
    if tool.NbSolution() > 0:
        result["closest_point_from"] = _point(tool.PointOnShape1(1))
        result["closest_point_to"] = _point(tool.PointOnShape2(1))
    return result


def measure_center_distance(args: dict[str, Any]) -> dict[str, Any]:
    """Centroid-to-centroid distance, kept as an explicit mode.

    Useful for things like hole-centre spacing, where the centroids are
    what you actually mean.
    """
    wp = _workplane(args["brep_path"])
    from_sel, from_used, from_count = resolve_selector(
        wp, args["from_selector"], args.get("from_kind")
    )
    to_sel, to_used, to_count = resolve_selector(
        wp, args["to_selector"], args.get("to_kind")
    )

    a = from_sel.val().Center()
    b = to_sel.val().Center()
    return {
        "center_distance_mm": round(a.sub(b).Length, 4),
        "from": {
            "selector": args["from_selector"],
            "kind": from_used,
            "matched": from_count,
            "center": {
                "x": round(a.x, 3), "y": round(a.y, 3), "z": round(a.z, 3)
            },
        },
        "to": {
            "selector": args["to_selector"],
            "kind": to_used,
            "matched": to_count,
            "center": {
                "x": round(b.x, 3), "y": round(b.y, 3), "z": round(b.z, 3)
            },
        },
        "unit": "mm",
    }


def measure_clearance(args: dict[str, Any]) -> dict[str, Any]:
    """Minimum distance between two transformed parts using BRepExtrema."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    a, b = args["parts"]
    shape_a = transform_ocp_shape(
        load_shape(a["brep_path"]), tuple(a["translate"]), tuple(a["rotate"])
    )
    shape_b = transform_ocp_shape(
        load_shape(b["brep_path"]), tuple(b["translate"]), tuple(b["rotate"])
    )

    tool = BRepExtrema_DistShapeShape(shape_a, shape_b)
    if not tool.IsDone():
        return {"error": "Clearance computation failed."}

    result: dict[str, Any] = {
        "clearance_mm": round(tool.Value(), 4),
        "parts": [a["name"], b["name"]],
        "unit": "mm",
    }
    if tool.NbSolution() > 0:
        result["closest_point_a"] = _point(tool.PointOnShape1(1))
        result["closest_point_b"] = _point(tool.PointOnShape2(1))
    return result


# ------------------------------------------------------------------
# validate / assembly
# ------------------------------------------------------------------


def interference(args: dict[str, Any]) -> dict[str, Any]:
    """Overlap volume in mm³ between two transformed parts."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    a, b = args["parts"]
    shape_a = transform_ocp_shape(
        load_shape(a["brep_path"]), tuple(a["translate"]), tuple(a["rotate"])
    )
    shape_b = transform_ocp_shape(
        load_shape(b["brep_path"]), tuple(b["translate"]), tuple(b["rotate"])
    )

    common = BRepAlgoAPI_Common(shape_a, shape_b)
    if not common.IsDone():
        return {"volume_mm3": 0.0}

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(common.Shape(), props)
    return {"volume_mm3": float(abs(props.Mass()))}


def sew_mesh(args: dict[str, Any]) -> dict[str, Any]:
    """Sew a triangle soup into a shell and persist it as a B-rep."""
    import numpy as np
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_Sewing,
    )
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt

    data = np.load(args["mesh_path"])
    vertices = data["verts"]
    faces = data["faces"]

    sew = BRepBuilderAPI_Sewing(1e-3)
    for tri in faces:
        pts = [
            gp_Pnt(
                float(vertices[i][0]),
                float(vertices[i][1]),
                float(vertices[i][2]),
            )
            for i in tri
        ]
        wire = BRepBuilderAPI_MakePolygon(pts[0], pts[1], pts[2], True).Wire()
        sew.Add(BRepBuilderAPI_MakeFace(wire, True).Face())
    sew.Perform()

    BRepTools.Write_s(sew.SewedShape(), args["brep_out"])
    return {"brep_out": args["brep_out"], "faces": len(faces)}
