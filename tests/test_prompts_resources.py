"""Tests for MCP prompts (SPEC 5.2) and resources (SPEC 5.3).

Covers:
  - All three prompts register and return non-empty content
  - design_workflow mandates render-after-execute, critique, measure
  - cadquery_primer has runnable snippets covering required topics
  - Session code resource returns current code
  - All 8 curated examples execute successfully in the sandbox
"""
from __future__ import annotations

import json

import pytest

from cad_mcp import session
from cad_mcp.resources import EXAMPLES
from cad_mcp.server import mcp

from .envelope_helpers import summary


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_prompts_list() -> None:
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert "design_workflow" in names
    assert "cadquery_primer" in names
    assert "printability_checklist" in names


@pytest.mark.anyio
async def test_design_workflow_content() -> None:
    result = await mcp.get_prompt("design_workflow", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert len(text) > 200

    assert "render_views" in text
    assert "execute_cad" in text
    assert "critique" in text.lower()
    assert "measure" in text
    assert "validate_mesh" in text
    assert "export_model" in text


@pytest.mark.anyio
async def test_design_workflow_mandates_render() -> None:
    result = await mcp.get_prompt("design_workflow", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert "after every" in text.lower()
    assert "never skip" in text.lower()


@pytest.mark.anyio
async def test_design_workflow_mandates_measure() -> None:
    result = await mcp.get_prompt("design_workflow", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert "verify dimensions" in text.lower() or "measure" in text.lower()
    assert "bbox" in text


@pytest.mark.anyio
async def test_cadquery_primer_content() -> None:
    result = await mcp.get_prompt("cadquery_primer", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert len(text) > 500

    required_topics = [
        "Workplane",
        "extrude",
        "fillet",
        "shell",
        "polarArray",
        "union",
    ]
    for topic in required_topics:
        assert topic in text, f"Primer missing topic: {topic}"


@pytest.mark.anyio
async def test_cadquery_primer_has_runnable_snippets() -> None:
    result = await mcp.get_prompt("cadquery_primer", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert text.count("```python") >= 5, "Primer needs at least 5 snippets"
    assert "result =" in text


@pytest.mark.anyio
async def test_printability_checklist_content() -> None:
    result = await mcp.get_prompt("printability_checklist", {})
    text = result.messages[0].content.text  # type: ignore[union-attr]
    assert "wall thickness" in text.lower()
    assert "overhang" in text.lower()
    assert "bed adhesion" in text.lower()
    assert "tolerance" in text.lower() or "mating" in text.lower()


# ------------------------------------------------------------------
# Resources
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_code_empty() -> None:
    data = await mcp.read_resource("cad://session/current/code")
    text = data[0].content  # type: ignore[union-attr]
    assert "No model code yet" in text


@pytest.mark.anyio
async def test_session_code_after_execute() -> None:
    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(10,10,10)"
    await mcp.call_tool("execute_cad", {"code": code})

    data = await mcp.read_resource("cad://session/current/code")
    text = data[0].content  # type: ignore[union-attr]
    assert "box(10" in text


@pytest.mark.anyio
async def test_example_resource_unknown() -> None:
    data = await mcp.read_resource("cad://examples/nonexistent")
    text = data[0].content  # type: ignore[union-attr]
    assert "Unknown example" in text
    assert "bracket" in text


@pytest.mark.anyio
async def test_all_examples_registered() -> None:
    expected = {
        "bracket",
        "enclosure",
        "flange",
        "pipe_clamp",
        "phone_stand",
        "threaded_cap",
        "gear",
        "desk_organizer",
    }
    assert set(EXAMPLES.keys()) == expected


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(EXAMPLES.keys()))
async def test_example_executes(name: str) -> None:
    """Every curated example must execute successfully."""
    code = EXAMPLES[name]
    result = await mcp.call_tool("execute_cad", {"code": code})
    text = summary(result)

    is_ok = text.startswith("OK")
    if not is_ok:
        data = json.loads(text)
        assert data.get("ok"), f"Example '{name}' failed: {text}"
    assert is_ok, f"Example '{name}' failed: {text}"
