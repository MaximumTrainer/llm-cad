"""Actionable errors, discoverable examples, and AI-mesh provenance.

Covers CAD-034 (#35), CAD-021 (#22) and CAD-022 (#23).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import anyio
import pytest

from cad_mcp import sandbox, session
from cad_mcp.envelope import validate
from cad_mcp.resources import EXAMPLE_DESCRIPTIONS, EXAMPLES
from cad_mcp.server import mcp

from .envelope_helpers import flat

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
pytestmark = pytest.mark.geometry


def _run(code: str) -> sandbox.SandboxResult:
    tmp = Path(tempfile.mkdtemp(prefix="cad-mcp-hint-"))
    return sandbox.run(code, tmp, brep_out=tmp / "out.brep")


# ------------------------------------------------------------------
# CAD-034: every common failure carries a hint
# ------------------------------------------------------------------

# Each entry is (label, code, a word the hint must contain).
HINT_CASES = [
    (
        "no solid on the stack",
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').faces('>Z').hole(3)",
        "box",
    ),
    (
        "hallucinated method",
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10,10,10).pushToTop()",
        "pushPoints",
    ),
    (
        "near-miss method name",
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10,10,10).filet(2)",
        "fillet",
    ),
    (
        "invalid plane name",
        "import cadquery as cq\nresult = cq.Workplane('QQ').box(1,1,1)",
        "XY",
    ),
    (
        "fillet larger than the edge",
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(40,40,10).edges('|Z').fillet(20)",
        "radius",
    ),
    (
        "wrong argument count",
        "import cadquery as cq\nresult = cq.Workplane('XY').box(10)",
        "signature",
    ),
]


@pytest.mark.parametrize(
    ("label", "code", "expected"),
    HINT_CASES,
    ids=[c[0].replace(" ", "-") for c in HINT_CASES],
)
def test_common_errors_carry_an_actionable_hint(
    label: str, code: str, expected: str
) -> None:
    """A hintless repeat of the same error gives the model nothing.

    Found by the live-LLM test: `Cannot find a solid on the stack` fired
    seven times in one run with no hint, and the model never recovered.
    """
    result = _run(code)
    assert not result.ok, f"{label} was expected to fail"
    assert result.hint, (
        f"{label} ({result.error_type}: {result.message}) returned no "
        f"hint — SPEC N3 requires what, where AND a hint"
    )
    assert expected.lower() in result.hint.lower(), (
        f"{label}: hint does not mention {expected!r}: {result.hint}"
    )


def test_no_common_error_class_is_left_hintless() -> None:
    """Coverage guard, so a new error class is not silently unhinted."""
    unhinted = []
    for label, code, _ in HINT_CASES:
        result = _run(code)
        if not result.hint:
            unhinted.append(f"{label} -> {result.error_type}")
    assert not unhinted, f"error classes with no hint: {unhinted}"


def test_errors_include_surrounding_source_context() -> None:
    """The root cause is often the statement before the one that raised."""
    result = _run(
        "import cadquery as cq\n"
        "wp = cq.Workplane('XY')\n"
        "result = wp.faces('>Z').hole(3)\n"
    )
    assert not result.ok
    assert result.context, "no source context returned"
    assert any(">>" in line for line in result.context), (
        "the failing line is not marked in the context"
    )


def test_hint_reaches_the_llm_through_the_tool() -> None:
    async def scenario() -> dict[str, Any]:
        return flat(
            await mcp.call_tool(
                "execute_cad",
                {
                    "code": "import cadquery as cq\n"
                    "result = cq.Workplane('XY').faces('>Z').hole(3)"
                },
            )
        )

    payload = anyio.run(scenario)
    assert payload["ok"] is False
    assert payload["hint"], f"no hint in the tool response: {payload}"
    assert "Hint:" in payload["summary"]


def test_hint_table_is_a_table() -> None:
    """A chain of ifs is why rules were never added."""
    from cad_mcp import _sandbox_worker

    assert isinstance(_sandbox_worker._HINTS, tuple)
    assert len(_sandbox_worker._HINTS) >= 5
    for needle, hint in _sandbox_worker._HINTS:
        assert needle == needle.lower(), "matchers must be lowercase"
        assert len(hint) > 30, f"hint for {needle!r} is not prescriptive"


def test_method_suggestions_come_from_the_real_api() -> None:
    from cad_mcp._sandbox_worker import _workplane_suggestions

    assert "fillet" in _workplane_suggestions("filet")
    assert "pushPoints" in _workplane_suggestions("pushToTop")


# ------------------------------------------------------------------
# CAD-021: the examples are discoverable
# ------------------------------------------------------------------


def _resource_uris() -> list[str]:
    return [str(r.uri) for r in anyio.run(mcp.list_resources)]


def test_every_example_has_a_concrete_resource_uri() -> None:
    """Template-only exposure made them invisible to many hosts."""
    uris = _resource_uris()
    missing = [
        name for name in EXAMPLES if f"cad://examples/{name}" not in uris
    ]
    assert not missing, f"examples with no concrete URI: {missing}"


def test_there_is_an_index_resource() -> None:
    assert "cad://examples" in _resource_uris()


def test_the_index_lists_every_example_with_a_description() -> None:
    async def read() -> str:
        contents = await mcp.read_resource("cad://examples")
        return "".join(str(c.content) for c in contents)

    index = anyio.run(read)
    for name in EXAMPLES:
        assert name in index, f"{name} missing from the index"
        assert EXAMPLE_DESCRIPTIONS[name].split(";")[0][:20] in index, (
            f"{name} has no description in the index"
        )


def test_descriptions_cover_exactly_the_examples() -> None:
    """The old name list was a hand-maintained string that could drift."""
    assert set(EXAMPLE_DESCRIPTIONS) == set(EXAMPLES)


def test_unknown_example_returns_a_structured_error() -> None:
    """It used to return a Python comment, which is not an error."""

    async def read() -> str:
        contents = await mcp.read_resource("cad://examples/nope")
        return "".join(str(c.content) for c in contents)

    payload = validate(anyio.run(read))
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnknownExample"
    assert "bracket" in payload["error"]["hint"]


def test_template_description_is_generated_from_examples() -> None:
    templates = anyio.run(mcp.list_resource_templates)
    example_template = next(
        t for t in templates if "examples" in str(t.uri_template)
    )
    for name in EXAMPLES:
        assert name in (example_template.description or "")


# ------------------------------------------------------------------
# CAD-022: AI-mesh geometry is not pretend-reproducible
# ------------------------------------------------------------------


def test_parts_default_to_reproducible_cadquery_source() -> None:
    part = session.Part(name="main")
    assert part.source == "cadquery"
    assert part.ai_prompt is None


def test_execute_cad_refuses_to_overwrite_an_ai_mesh() -> None:
    """A replayed history would have silently destroyed the mesh."""

    async def scenario() -> dict[str, Any]:
        await mcp.call_tool(
            "execute_cad",
            {
                "code": "import cadquery as cq\n"
                "result = cq.Workplane('XY').box(5,5,5)"
            },
        )
        sess = session.for_context(None)
        part = sess.get_active_part()
        # Stand in for a completed gen_ai_mesh call.
        part.source = "ai_mesh"
        part.ai_prompt = "a chess pawn"
        part.code_history = ["# generated"]

        return flat(
            await mcp.call_tool(
                "execute_cad",
                {
                    "code": "import cadquery as cq\n"
                    "result = cq.Workplane('XY').box(1,1,1)"
                },
            )
        )

    payload = anyio.run(scenario)
    assert payload["ok"] is False
    assert payload["error_type"] == "NotReproducible"
    # The refusal must name the way forward, not just say no.
    assert "create_part" in payload["hint"]
    assert "set_active_part" in payload["hint"]


def test_ai_mesh_provenance_is_visible_in_list_parts() -> None:
    async def scenario() -> dict[str, Any]:
        await mcp.call_tool(
            "execute_cad",
            {
                "code": "import cadquery as cq\n"
                "result = cq.Workplane('XY').box(5,5,5)"
            },
        )
        sess = session.for_context(None)
        part = sess.get_active_part()
        part.source = "ai_mesh"
        part.ai_prompt = "a chess pawn"
        return flat(await mcp.call_tool("list_parts", {}))

    payload = anyio.run(scenario)
    main = next(p for p in payload["parts"] if p["name"] == "main")
    assert main["source"] == "ai_mesh"
    assert main["reproducible_from_code"] is False
    assert main["ai_prompt"] == "a chess pawn"


def test_list_session_reports_the_active_part_source() -> None:
    async def scenario() -> dict[str, Any]:
        return flat(await mcp.call_tool("list_session", {}))

    assert anyio.run(scenario)["active_part_source"] == "cadquery"


def test_mesh_import_reports_a_bounding_box() -> None:
    """part.bbox was left None for a part that had geometry."""
    import trimesh

    from cad_mcp.mesh_to_brep import glb_to_brep

    tmp = Path(tempfile.mkdtemp(prefix="cad-mcp-glb-"))
    glb = tmp / "cube.glb"
    trimesh.creation.box(extents=(4.0, 6.0, 8.0)).export(str(glb))

    stats = glb_to_brep(glb, tmp / "cube.brep")

    assert stats["face_count"] > 0
    bbox = stats["bbox"]
    assert bbox is not None
    assert bbox["xmax"] - bbox["xmin"] == pytest.approx(4.0, abs=0.01)
    assert bbox["ymax"] - bbox["ymin"] == pytest.approx(6.0, abs=0.01)
    assert bbox["zmax"] - bbox["zmin"] == pytest.approx(8.0, abs=0.01)
