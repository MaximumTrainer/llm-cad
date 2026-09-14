"""measure tool -- numeric measurements on the current model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import geometry, session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok_data


def _measure_bbox(brep_path: Path) -> dict[str, Any]:
    return geometry.call("measure", what="bbox", brep_path=str(brep_path))


def _measure_volume(brep_path: Path) -> dict[str, Any]:
    return geometry.call("measure", what="volume", brep_path=str(brep_path))


def _measure_faces(brep_path: Path) -> dict[str, Any]:
    return geometry.call("measure", what="faces", brep_path=str(brep_path))


def _measure_distance(
    brep_path: Path,
    from_selector: str,
    to_selector: str,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> dict[str, Any]:
    return geometry.call(
        "measure",
        what="distance",
        brep_path=str(brep_path),
        from_selector=from_selector,
        to_selector=to_selector,
        from_kind=from_kind,
        to_kind=to_kind,
    )


def _measure_center_distance(
    brep_path: Path,
    from_selector: str,
    to_selector: str,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> dict[str, Any]:
    return geometry.call(
        "measure",
        what="center_distance",
        brep_path=str(brep_path),
        from_selector=from_selector,
        to_selector=to_selector,
        from_kind=from_kind,
        to_kind=to_kind,
    )


def _measure_clearance(
    sess: session.Session,
    part_names: list[str],
) -> dict[str, Any]:
    """Minimum distance between two parts using BRepExtrema."""
    if len(part_names) != 2:
        return {"error": "clearance requires exactly 2 part names."}

    for name in part_names:
        if name not in sess.parts:
            return {"error": f"Part '{name}' not found."}
        if not sess.has_model(name):
            return {"error": f"Part '{name}' has no geometry."}

    return geometry.call(
        "measure",
        what="clearance",
        parts=[
            {
                "name": name,
                "brep_path": str(sess.brep_path(name)),
                "translate": list(sess.parts[name].translate),
                "rotate": list(sess.parts[name].rotate),
            }
            for name in part_names
        ],
    )


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
