"""measure tool -- numeric measurements on the current model."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool


def _measure_bbox(brep_path: Path) -> dict[str, Any]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from cad_mcp.export import _load_ocp_shape

    shape = _load_ocp_shape(brep_path)
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


def _measure_volume(brep_path: Path) -> dict[str, Any]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from cad_mcp.export import _load_ocp_shape

    shape = _load_ocp_shape(brep_path)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    vol = props.Mass()
    return {"volume_mm3": round(vol, 3), "unit": "mm³"}


def _measure_faces(brep_path: Path) -> dict[str, Any]:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    from cad_mcp.export import _load_ocp_shape

    shape = _load_ocp_shape(brep_path)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return {"face_count": count}


def _measure_distance(
    brep_path: Path,
    from_selector: str,
    to_selector: str,
) -> dict[str, Any]:
    import cadquery as cq

    from cad_mcp.export import _load_ocp_shape

    ocp_shape = _load_ocp_shape(brep_path)
    shape = cq.Shape(ocp_shape)
    wp = cq.Workplane("XY").newObject([shape])

    try:
        from_obj = wp.faces(from_selector).val()
    except Exception as exc:
        return {"error": f"from_selector '{from_selector}' failed: {exc}"}

    try:
        to_obj = wp.faces(to_selector).val()
    except Exception as exc:
        return {"error": f"to_selector '{to_selector}' failed: {exc}"}

    from_center = from_obj.Center()
    to_center = to_obj.Center()
    dist = from_center.sub(to_center).Length
    return {
        "distance_mm": round(dist, 3),
        "from_center": {
            "x": round(from_center.x, 3),
            "y": round(from_center.y, 3),
            "z": round(from_center.z, 3),
        },
        "to_center": {
            "x": round(to_center.x, 3),
            "y": round(to_center.y, 3),
            "z": round(to_center.z, 3),
        },
        "unit": "mm",
    }


def _measure_clearance(
    sess: session.Session,
    part_names: list[str],
) -> dict[str, Any]:
    """Minimum distance between two parts using BRepExtrema."""
    if len(part_names) != 2:
        return {"error": "clearance requires exactly 2 part names."}

    name_a, name_b = part_names
    for name in (name_a, name_b):
        if name not in sess.parts:
            return {"error": f"Part '{name}' not found."}
        if not sess.has_model(name):
            return {"error": f"Part '{name}' has no geometry."}

    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    from cad_mcp.export import _load_ocp_shape, transform_ocp_shape

    part_a = sess.parts[name_a]
    part_b = sess.parts[name_b]
    shape_a = _load_ocp_shape(sess.brep_path(name_a))
    shape_b = _load_ocp_shape(sess.brep_path(name_b))
    shape_a = transform_ocp_shape(shape_a, part_a.translate, part_a.rotate)
    shape_b = transform_ocp_shape(shape_b, part_b.translate, part_b.rotate)

    dist_tool = BRepExtrema_DistShapeShape(shape_a, shape_b)
    if not dist_tool.IsDone():
        return {"error": "Clearance computation failed."}

    distance = dist_tool.Value()

    result: dict[str, Any] = {
        "clearance_mm": round(distance, 4),
        "parts": part_names,
        "unit": "mm",
    }

    if dist_tool.NbSolution() > 0:
        p1 = dist_tool.PointOnShape1(1)
        p2 = dist_tool.PointOnShape2(1)
        result["closest_point_a"] = {
            "x": round(p1.X(), 3),
            "y": round(p1.Y(), 3),
            "z": round(p1.Z(), 3),
        }
        result["closest_point_b"] = {
            "x": round(p2.X(), 3),
            "y": round(p2.Y(), 3),
            "z": round(p2.Z(), 3),
        }

    return result


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("measure")
    def measure(
        what: str,
        from_selector: str | None = None,
        to_selector: str | None = None,
        parts: list[str] | None = None,
    ) -> str:
        """Take numeric measurements of the current model.

        Args:
            what: What to measure. One of ``bbox``, ``volume``,
                  ``faces``, ``distance``, or ``clearance``.
            from_selector: CadQuery face selector for the start face
                           (required when ``what="distance"``).
            to_selector: CadQuery face selector for the end face
                         (required when ``what="distance"``).
            parts: Two part names (required when ``what="clearance"``).

        Returns:
            JSON with the requested measurement in mm or mm³.
        """
        sess = session.get_or_create()

        what = what.lower().strip()
        valid = ("bbox", "volume", "faces", "distance", "clearance")
        if what not in valid:
            return _err(
                f"Invalid measurement '{what}'. Choose from: {list(valid)}"
            )

        if what == "clearance":
            if not parts or len(parts) != 2:
                return _err(
                    "clearance requires parts=[\"part_a\", \"part_b\"]"
                )
            try:
                result = _measure_clearance(sess, parts)
            except Exception as exc:
                return _err(f"Clearance failed: {type(exc).__name__}: {exc}")
            if "error" in result:
                return _err(result["error"])
            return json.dumps({"ok": True, "measurement": what, **result})

        brep = sess.brep_path()
        if not brep.exists():
            return _err("No model to measure. Run execute_cad first.")

        try:
            if what == "bbox":
                result = _measure_bbox(brep)
            elif what == "volume":
                result = _measure_volume(brep)
            elif what == "faces":
                result = _measure_faces(brep)
            elif what == "distance":
                if not from_selector or not to_selector:
                    return _err(
                        "distance requires from_selector and to_selector"
                    )
                result = _measure_distance(brep, from_selector, to_selector)
            else:
                return _err(f"Unknown measurement: {what}")
        except Exception as exc:
            return _err(f"Measurement failed: {type(exc).__name__}: {exc}")

        return json.dumps({"ok": True, "measurement": what, **result})


def _err(message: str) -> str:
    d: dict[str, Any] = {"ok": False, "error": message}
    return json.dumps(d)
