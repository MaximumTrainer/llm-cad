"""measure tool -- numeric measurements on the current model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok_data


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


SELECTOR_KINDS = ("faces", "edges", "vertices")


def _select(wp: Any, selector: str, kind: str) -> tuple[Any, int]:
    """Resolve a selector to (shape, match_count) for a given kind."""
    chooser = getattr(wp, kind)
    selected = chooser(selector)
    values = selected.vals()
    if not values:
        msg = f"selector {selector!r} matched no {kind}"
        raise ValueError(msg)
    return selected, len(values)


def _resolve_selector(
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


def _measure_distance(
    brep_path: Path,
    from_selector: str,
    to_selector: str,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> dict[str, Any]:
    """True minimum distance between two selected entities.

    This used to return the distance between the two entities' *centroids*.
    For parallel planar faces that happens to equal the thickness; for a
    cylindrical bore, a filleted face, or a selector matching several
    entities it is a number with no physical meaning — reported to three
    decimal places with no caveat, while the workflow prompt tells the LLM
    to use exactly this call to confirm dimensions "within 0.1mm"
    (CAD-018).
    """
    import cadquery as cq
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    from cad_mcp.export import _load_ocp_shape

    shape = cq.Shape(_load_ocp_shape(brep_path))
    wp = cq.Workplane("XY").newObject([shape])

    from_sel, from_used, from_count = _resolve_selector(
        wp, from_selector, from_kind
    )
    to_sel, to_used, to_count = _resolve_selector(wp, to_selector, to_kind)

    tool = BRepExtrema_DistShapeShape(
        from_sel.val().wrapped, to_sel.val().wrapped
    )
    if not tool.IsDone():
        msg = "minimum-distance computation failed"
        raise ValueError(msg)

    result: dict[str, Any] = {
        "distance_mm": round(tool.Value(), 4),
        "from": {
            "selector": from_selector,
            "kind": from_used,
            "matched": from_count,
        },
        "to": {
            "selector": to_selector,
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
        p1 = tool.PointOnShape1(1)
        p2 = tool.PointOnShape2(1)
        result["closest_point_from"] = {
            "x": round(p1.X(), 3),
            "y": round(p1.Y(), 3),
            "z": round(p1.Z(), 3),
        }
        result["closest_point_to"] = {
            "x": round(p2.X(), 3),
            "y": round(p2.Y(), 3),
            "z": round(p2.Z(), 3),
        }
    return result


def _measure_center_distance(
    brep_path: Path,
    from_selector: str,
    to_selector: str,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> dict[str, Any]:
    """Centroid-to-centroid distance, kept as an explicit mode.

    Useful for things like hole-centre spacing, where the centroids are
    what you actually mean.
    """
    import cadquery as cq

    from cad_mcp.export import _load_ocp_shape

    shape = cq.Shape(_load_ocp_shape(brep_path))
    wp = cq.Workplane("XY").newObject([shape])

    from_sel, from_used, from_count = _resolve_selector(
        wp, from_selector, from_kind
    )
    to_sel, to_used, to_count = _resolve_selector(wp, to_selector, to_kind)

    a = from_sel.val().Center()
    b = to_sel.val().Center()
    return {
        "center_distance_mm": round(a.sub(b).Length, 4),
        "from": {
            "selector": from_selector,
            "kind": from_used,
            "matched": from_count,
            "center": {
                "x": round(a.x, 3), "y": round(a.y, 3), "z": round(a.z, 3)
            },
        },
        "to": {
            "selector": to_selector,
            "kind": to_used,
            "matched": to_count,
            "center": {
                "x": round(b.x, 3), "y": round(b.y, 3), "z": round(b.z, 3)
            },
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


def _summarise(what: str, result: dict[str, Any]) -> str:
    """One readable line per measurement kind."""
    if what == "bbox":
        d = result["dimensions"]
        return f"bbox: {d['x']} x {d['y']} x {d['z']} mm"
    if what == "volume":
        return f"volume: {result['volume_mm3']} mm3"
    if what == "faces":
        return f"{result['face_count']} face(s)"
    if what == "distance":
        return (
            f"minimum distance: {result.get('distance_mm')} mm "
            f"({result['from']['kind']} -> {result['to']['kind']})"
        )
    if what == "center_distance":
        return f"centre-to-centre: {result.get('center_distance_mm')} mm"
    return what


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("measure")
    def measure(
        what: str,
        from_selector: str | None = None,
        to_selector: str | None = None,
        parts: list[str] | None = None,
        from_kind: str | None = None,
        to_kind: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """Take numeric measurements of the current model.

        Use this to verify that the dimensions you intended actually
        happened, before exporting.

        Args:
            what: What to measure.
                  ``bbox`` overall dimensions of the active part;
                  ``volume`` enclosed volume in mm3;
                  ``faces`` face count;
                  ``distance`` the **minimum** distance between two
                  selected entities — the right choice for wall
                  thickness, gaps and clearances;
                  ``center_distance`` the distance between the two
                  selections' centroids — the right choice for hole
                  spacing;
                  ``clearance`` the minimum distance between two named
                  parts.
            from_selector: CadQuery selector for the first entity
                           (``distance`` / ``center_distance``), e.g.
                           ``">Z"`` or ``"|Z"``.
            to_selector: CadQuery selector for the second entity.
            parts: Exactly two part names (``clearance``).
            from_kind: Force the first selector's entity kind —
                       ``faces``, ``edges`` or ``vertices``. Inferred
                       when omitted.
            to_kind: Same, for the second selector.

        Returns:
            The measurement in mm or mm3. ``distance`` and
            ``center_distance`` also report which entity kind each
            selector resolved to and how many entities it matched, so a
            selector that matched more than you expected is visible
            rather than silently using the first.
        """
        sess = session.for_context(ctx)

        what = what.lower().strip()
        valid = (
            "bbox",
            "volume",
            "faces",
            "distance",
            "center_distance",
            "clearance",
        )
        if what not in valid:
            return fail(
                "ValueError",
                f"Invalid measurement '{what}'.",
                hint=f"Choose from: {list(valid)}.",
            )

        if what == "clearance":
            if not parts or len(parts) != 2:
                return fail(
                    "ValueError",
                    "clearance needs exactly two part names.",
                    hint='Call measure(what="clearance", parts=["lid", "box"]).',
                )
            try:
                result = _measure_clearance(sess, parts)
            except Exception as exc:
                return fail(type(exc).__name__, f"Clearance failed: {exc}")
            if "error" in result:
                return fail("MeasurementError", str(result["error"]))
            return ok_data(
                f"clearance between {result['parts'][0]!r} and "
                f"{result['parts'][1]!r}: {result['clearance_mm']} mm",
                {"measurement": what, **result},
            )

        brep = sess.brep_path()
        if not brep.exists():
            return fail(
                "NoModel",
                "No model to measure.",
                hint="Run execute_cad to create geometry first.",
            )

        try:
            if what == "bbox":
                result = _measure_bbox(brep)
            elif what == "volume":
                result = _measure_volume(brep)
            elif what == "faces":
                result = _measure_faces(brep)
            elif what in ("distance", "center_distance"):
                if not from_selector or not to_selector:
                    return fail(
                        "ValueError",
                        f"{what} needs both from_selector and to_selector.",
                        hint='e.g. from_selector=">Z", to_selector="<Z".',
                    )
                measurer = (
                    _measure_distance
                    if what == "distance"
                    else _measure_center_distance
                )
                result = measurer(
                    brep,
                    from_selector,
                    to_selector,
                    from_kind,
                    to_kind,
                )
            else:
                return fail("ValueError", f"Unknown measurement: {what}")
        except Exception as exc:
            return fail(type(exc).__name__, f"Measurement failed: {exc}")

        if "error" in result:
            return fail("MeasurementError", str(result["error"]))
        part = sess.get_active_part()
        positioned = any(part.translate) or any(part.rotate)
        return ok_data(
            _summarise(what, result),
            {
                "measurement": what,
                "part": part.name,
                # bbox/volume/faces/distance read the part's own geometry,
                # which lives at the origin; only `clearance` applies the
                # assembly transform. Saying so stops the LLM comparing
                # positioned and unpositioned numbers (CAD-018).
                "part_transform_applied": False,
                "part_is_positioned": positioned,
                **result,
            },
        )
