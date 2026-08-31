"""export_model tool -- export model to various formats (assembly-aware)."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from cad_mcp import export, session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("export_model")
    def export_model(
        format: str,
        filename: str | None = None,
        tolerance: float = 0.1,
        parts: str | list[str] | None = None,
    ) -> str:
        """Export the current model to a file.

        STEP exports preserve the exact B-rep geometry.  STL, 3MF, and
        GLB export from a tessellated mesh whose resolution is controlled
        by ``tolerance``.

        For multi-part assemblies: exports each requested part
        individually plus a combined assembly file.

        Args:
            format: Output format — ``step``, ``stl``, ``3mf``, or ``glb``.
            filename: Base filename (extension added automatically).
                      Defaults to ``model.<ext>``.
            tolerance: Tessellation tolerance in mm for mesh formats.
                       Smaller = finer mesh. Default 0.1.
            parts: ``"all"`` (default), ``"active"``, or a list of
                   part names to export.

        Returns:
            JSON with ``path``, ``size_bytes``, and ``format`` for each
            exported file.
        """
        sess = session.get_or_create()
        fmt = format.lower()

        if fmt not in export.EXPORTERS:
            return _err(
                f"Unsupported format '{fmt}'. "
                f"Choose from: {list(export.EXPORTERS)}"
            )

        if parts is None or parts == "all":
            export_names = [
                n for n in sess.parts if sess.has_model(n)
            ]
        elif parts == "active":
            export_names = [sess.active_part]
        elif isinstance(parts, list):
            bad = [n for n in parts if n not in sess.parts]
            if bad:
                return _err(f"Unknown parts: {bad}")
            export_names = [n for n in parts if sess.has_model(n)]
        else:
            return _err(f"Invalid parts value: {parts}")

        if not export_names:
            return _err("No model to export. Run execute_cad first.")

        output_dir = sess.tmpdir / "output"
        ext = export.FORMAT_EXTENSIONS[fmt]
        base = filename or "model"

        if len(export_names) == 1:
            return _export_single(
                sess, export_names[0], output_dir, fmt, base, ext, tolerance
            )

        return _export_multi(
            sess, export_names, output_dir, fmt, base, ext, tolerance
        )


def _export_single(
    sess: session.Session,
    part_name: str,
    output_dir: Any,
    fmt: str,
    base: str,
    ext: str,
    tolerance: float,
) -> str:
    """Export a single part (backward-compat path)."""
    brep = sess.brep_path(part_name)
    fname = f"{base}{ext}"

    try:
        result = export.export_model(brep, output_dir, fmt, fname, tolerance)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Export failed: {type(exc).__name__}: {exc}")

    sess.exports.append({"format": fmt, "path": result["path"]})
    return json.dumps({"ok": True, **result})


def _export_multi(
    sess: session.Session,
    export_names: list[str],
    output_dir: Any,
    fmt: str,
    base: str,
    ext: str,
    tolerance: float,
) -> str:
    """Export multiple parts individually + combined assembly."""
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []

    for name in export_names:
        brep = sess.brep_path(name)
        fname = f"{base}_{name}{ext}"
        try:
            result = export.export_model(
                brep, output_dir, fmt, fname, tolerance
            )
            result["part"] = name
            exported.append(result)
            sess.exports.append({"format": fmt, "path": result["path"]})
        except Exception as exc:
            exported.append({
                "part": name,
                "error": f"{type(exc).__name__}: {exc}",
            })

    assembly_path = output_dir / f"{base}_assembly{ext}"
    assembly_result: dict[str, Any] = {}
    try:
        if fmt == "step":
            parts_data = [
                (
                    name,
                    sess.brep_path(name),
                    sess.parts[name].translate,
                    sess.parts[name].rotate,
                )
                for name in export_names
            ]
            export.export_assembly_step(parts_data, assembly_path)
        else:
            parts_data_mesh = [
                (
                    sess.brep_path(name),
                    sess.parts[name].translate,
                    sess.parts[name].rotate,
                )
                for name in export_names
            ]
            export.export_assembly_mesh(
                parts_data_mesh, assembly_path, fmt, tolerance
            )

        assembly_result = {
            "path": str(assembly_path),
            "size_bytes": assembly_path.stat().st_size,
            "format": fmt,
            "filename": assembly_path.name,
            "part": "assembly",
        }
        sess.exports.append({
            "format": fmt,
            "path": str(assembly_path),
        })
    except Exception as exc:
        assembly_result = {
            "part": "assembly",
            "error": f"{type(exc).__name__}: {exc}",
        }

    exported.append(assembly_result)

    return json.dumps({
        "ok": True,
        "files": exported,
        "total_files": len(exported),
    })


def _err(message: str) -> str:
    d: dict[str, Any] = {"ok": False, "error": message}
    return json.dumps(d)
