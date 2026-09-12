"""validate_mesh tool -- mesh quality, printability, and interference checks."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session, validate
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok_data


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("validate_mesh")
    def validate_mesh(
        min_wall_mm: float = 1.2,
        max_overhang_deg: float = 45.0,
        part: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """Validate the current model for printability.

        Checks watertightness, manifold status, wall thickness,
        overhang angles, and estimates PLA volume/mass.

        When multiple parts exist, validates each independently and
        checks for part-to-part interference.

        Args:
            min_wall_mm: Minimum acceptable wall thickness in mm.
                         Default 1.2 (typical FDM minimum).
            max_overhang_deg: Maximum overhang angle from vertical
                              before flagging. Default 45°.
            part: Validate only this part. Default: all parts.

        Returns:
            JSON report with all validation results and a list of
            issues found.  ``printable`` is true when no issues.
        """
        sess = session.for_context(ctx)

        if part is not None:
            if part not in sess.parts:
                return fail(
                    "PartNotFound",
                    f"Part '{part}' not found.",
                    hint=f"Available: {list(sess.parts.keys())}.",
                )
            parts_to_validate = [part]
        else:
            parts_to_validate = [
                n for n in sess.parts if sess.has_model(n)
            ]

        if not parts_to_validate:
            return fail(
                "NoModel",
                "No model to validate.",
                hint="Run execute_cad to create geometry first.",
            )

        per_part: list[dict[str, Any]] = []
        all_issues: list[str] = []

        for name in parts_to_validate:
            brep = sess.brep_path(name)
            if not brep.exists():
                per_part.append({
                    "part": name,
                    "error": "No geometry (run execute_cad).",
                })
                continue

            try:
                report = validate.validate(
                    brep, min_wall_mm, max_overhang_deg
                )
            except Exception as exc:
                per_part.append({
                    "part": name,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            report["part"] = name
            per_part.append(report)
            for issue in report.get("issues", []):
                all_issues.append(f"[{name}] {issue}")

        interference: list[dict[str, Any]] = []
        names_with_model = [
            n for n in parts_to_validate if sess.has_model(n)
        ]
        if len(names_with_model) >= 2:
            interference = _check_all_interference(sess, names_with_model)
            for item in interference:
                if item.get("interference"):
                    all_issues.append(
                        f"Interference between '{item['part_a']}' and "
                        f"'{item['part_b']}': "
                        f"{item['volume_mm3']:.3f} mm³ overlap"
                    )

        printable = len(all_issues) == 0
        if printable:
            headline = f"Printable: {len(per_part)} part(s), no issues found"
        else:
            headline = (
                f"{len(all_issues)} issue(s) across {len(per_part)} part(s): "
                + "; ".join(all_issues[:3])
            )
            if len(all_issues) > 3:
                headline += f"; +{len(all_issues) - 3} more"

        return ok_data(
            headline,
            {
                "parts": per_part,
                "interference": interference,
                "issues": all_issues,
                "printable": printable,
            },
        )


def _check_all_interference(
    sess: session.Session,
    part_names: list[str],
) -> list[dict[str, Any]]:
    """Check pairwise interference between parts."""
    results: list[dict[str, Any]] = []
    for i, name_a in enumerate(part_names):
        for name_b in part_names[i + 1 :]:
            try:
                vol = validate.check_interference(
                    sess.brep_path(name_a),
                    sess.brep_path(name_b),
                    sess.parts[name_a].translate,
                    sess.parts[name_a].rotate,
                    sess.parts[name_b].translate,
                    sess.parts[name_b].rotate,
                )
                results.append({
                    "part_a": name_a,
                    "part_b": name_b,
                    "interference": vol > 0.01,
                    "volume_mm3": round(vol, 3),
                })
            except Exception as exc:
                results.append({
                    "part_a": name_a,
                    "part_b": name_b,
                    "interference": False,
                    "error": str(exc),
                })
    return results
