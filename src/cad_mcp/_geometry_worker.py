"""Child process: the only place OCP is allowed to run (SPEC §7).

Started by ``geometry.py`` and kept alive across calls. It imports
CadQuery once, then serves one request at a time:

* the parent writes a request-file path to stdin;
* this reads the JSON there, dispatches, writes the response beside it;
* it prints ``<marker> <response path>`` to stdout so the parent knows.

The response goes to a *file* rather than stdout because CadQuery, VTK
and OCP all print to stdout uninvited -- the same reason ``sandbox.py``
uses a result file. The marker line is only a wake-up.

Unlike the sandbox worker this one is reused, and deliberately so: it
runs our code, not the user's, so a fresh interpreter per call would buy
no isolation and cost the 3.3s import every time. What it *does* provide
is crash isolation: when OCCT segfaults, this process dies and the
server does not.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

READY = "GEOMETRY-WORKER-READY"
DONE = "GEOMETRY-WORKER-DONE"


def _shape(brep_path: str) -> Any:
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not BRepTools.Read_s(shape, str(brep_path), builder):
        msg = f"Failed to read BREP: {brep_path}"
        raise ValueError(msg)
    return shape


# ------------------------------------------------------------------
# Operations. Each takes JSON-able args and returns a JSON-able dict;
# bulk arrays go to a file the caller named, because a 200k-triangle
# mesh through a JSON pipe is not a design.
# ------------------------------------------------------------------


def op_tessellate(args: dict[str, Any]) -> dict[str, Any]:
    import cadquery as cq
    import numpy as np

    shape = cq.Shape(_shape(args["brep_path"]))
    raw_verts, raw_faces = shape.tessellate(
        args["tolerance"], args["angular_tolerance"]
    )
    verts = np.array([(v.x, v.y, v.z) for v in raw_verts], dtype=np.float64)
    faces = np.array(raw_faces, dtype=np.int32)
    np.savez(args["out_path"], verts=verts, faces=faces)
    return {"vertices": len(verts), "faces": len(faces)}


def op_export_step(args: dict[str, Any]) -> dict[str, Any]:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from cad_mcp._determinism import pin_step_header

    writer = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    writer.Transfer(_shape(args["brep_path"]), STEPControl_AsIs)
    pin_step_header(writer)
    status = writer.Write(args["output_path"])
    if status != IFSelect_RetDone:
        msg = f"STEP export failed with status {status}"
        raise RuntimeError(msg)
    return {"output_path": args["output_path"]}


def op_export_assembly_step(args: dict[str, Any]) -> dict[str, Any]:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from cad_mcp._determinism import pin_step_header
    from cad_mcp._geometry_ops import transform_ocp_shape

    doc = TDocStd_Document(TCollection_ExtendedString("XBF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    for part in args["parts"]:
        transformed = transform_ocp_shape(
            _shape(part["brep_path"]),
            tuple(part["translate"]),
            tuple(part["rotate"]),
        )
        label = shape_tool.AddShape(transformed)
        TDataStd_Name.Set_s(
            label, TCollection_ExtendedString(part["name"])
        )

    Path(args["output_path"]).parent.mkdir(parents=True, exist_ok=True)
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc)
    pin_step_header(writer.ChangeWriter())
    status = writer.Write(args["output_path"])
    if status != IFSelect_RetDone:
        msg = f"STEP assembly export failed with status {status}"
        raise RuntimeError(msg)
    return {"output_path": args["output_path"]}


def op_measure(args: dict[str, Any]) -> dict[str, Any]:
    from cad_mcp import _geometry_ops

    handler = getattr(_geometry_ops, f"measure_{args['what']}")
    result: dict[str, Any] = handler(args)
    return result


def op_interference(args: dict[str, Any]) -> dict[str, Any]:
    from cad_mcp import _geometry_ops

    result: dict[str, Any] = _geometry_ops.interference(args)
    return result


def op_sew_mesh(args: dict[str, Any]) -> dict[str, Any]:
    from cad_mcp import _geometry_ops

    result: dict[str, Any] = _geometry_ops.sew_mesh(args)
    return result


def op_ping(args: dict[str, Any]) -> dict[str, Any]:
    """Cheap liveness probe that still proves the kernel is loaded."""
    import cadquery as cq

    return {"cadquery": cq.__version__}


OPS = {
    "tessellate": op_tessellate,
    "export_step": op_export_step,
    "export_assembly_step": op_export_assembly_step,
    "measure": op_measure,
    "interference": op_interference,
    "sew_mesh": op_sew_mesh,
    "ping": op_ping,
}


def _serve() -> None:
    # Pay the import now, not on the first request.
    import cadquery  # noqa: F401

    print(READY, file=sys.stderr, flush=True)

    for line in sys.stdin:
        request_path = line.strip()
        if not request_path:
            continue
        response_path = request_path + ".out"
        try:
            request = json.loads(
                Path(request_path).read_text(encoding="utf-8")
            )
            handler = OPS[request["op"]]
            payload = {"ok": True, "result": handler(request["args"])}
        except Exception as exc:
            payload = {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=6),
            }
        Path(response_path).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        print(f"{DONE} {response_path}", flush=True)


if __name__ == "__main__":
    _serve()
