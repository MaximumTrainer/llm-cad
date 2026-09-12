"""Guards against SPEC / README / code / prompt drift (CAD-032).

Four descriptions of the tool surface exist — SPEC.md's tables, README's
tool table, the registrations in `server.py`, and the prompts — and
nothing checked that they agreed. They had already drifted: the prompts
omitted all seven v2 tools, and `create_part`'s default colour was
`"steel"` in SPEC and `""` in code.
"""
from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest

from cad_mcp.server import mcp

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SPEC = (ROOT / "SPEC.md").read_text(encoding="utf-8")

# Tools that legitimately need no prompt coverage, with the reason.
PROMPT_EXEMPT = {
    "ping": "connectivity check, not part of the design workflow",
    "list_session": "recovery aid; described in its own docstring",
    "reset_session": "trivial and self-describing",
    "list_parts": "covered implicitly by the assembly section",
    "delete_part": "covered implicitly by the assembly section",
}


def tool_names() -> list[str]:
    return sorted(t.name for t in anyio.run(mcp.list_tools))


def test_every_registered_tool_has_a_readme_row() -> None:
    missing = [n for n in tool_names() if f"`{n}`" not in README]
    assert not missing, (
        f"Tools registered but absent from README's tool table: {missing}"
    )


def _readme_tool_table() -> str:
    """Just the '## Tools' section — README has other backticked tables."""
    start = README.index("## Tools")
    end = README.index("## Prompts", start)
    return README[start:end]


def test_readme_does_not_document_tools_that_do_not_exist() -> None:
    registered = set(tool_names())
    # Rows in the tool table look like: | `name` | description |
    documented = set(
        re.findall(r"^\|\s*`(\w+)`\s*\|", _readme_tool_table(), re.M)
    )
    phantom = documented - registered
    assert not phantom, (
        f"README documents tools that are not registered: {sorted(phantom)}"
    )


def test_every_tool_appears_in_spec() -> None:
    missing = [n for n in tool_names() if f"`{n}`" not in SPEC]
    assert not missing, (
        f"Tools absent from SPEC.md — SPEC changes first (PLAN.md): {missing}"
    )


def test_every_tool_is_taught_or_explicitly_exempt() -> None:
    """SPEC G6: an untaught tool is an unused tool."""
    from cad_mcp.prompts import (
        _CADQUERY_PRIMER,
        _DESIGN_WORKFLOW,
        _PRINTABILITY_CHECKLIST,
    )

    corpus = _DESIGN_WORKFLOW + _CADQUERY_PRIMER + _PRINTABILITY_CHECKLIST
    untaught = [
        n
        for n in tool_names()
        if n not in PROMPT_EXEMPT and n not in corpus
    ]
    assert not untaught, (
        f"Tools that no prompt mentions: {untaught}. Either teach them in "
        f"prompts.py or add them to PROMPT_EXEMPT with a reason."
    )


def test_exempt_list_has_no_stale_entries() -> None:
    registered = set(tool_names())
    stale = set(PROMPT_EXEMPT) - registered
    assert not stale, f"PROMPT_EXEMPT names non-existent tools: {stale}"


@pytest.mark.parametrize("tool_name", tool_names())
def test_tool_docstrings_document_every_parameter(tool_name: str) -> None:
    """The tool description is the LLM's only manual (SPEC 4)."""
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    tool = tools[tool_name]
    description = tool.description or ""
    assert description.strip(), f"{tool_name} has no description"

    params = [
        p for p in tool.input_schema.get("properties", {}) if p != "ctx"
    ]
    if not params:
        return

    assert "Args:" in description, (
        f"{tool_name} takes {params} but its docstring has no Args: section"
    )
    args_block = description.split("Args:", 1)[1]
    undocumented = [p for p in params if p not in args_block]
    assert not undocumented, (
        f"{tool_name} does not document: {undocumented}"
    )


def test_shared_defaults_agree_between_spec_and_code() -> None:
    """Values that appear in both places must not drift apart."""
    from cad_mcp import session

    assert f"{session.MAX_PARTS} parts" in SPEC or str(
        session.MAX_PARTS
    ) in SPEC, "MAX_PARTS is not stated in SPEC.md"
    assert session.PART_NAME_RE.pattern.strip("^$") in SPEC.replace(
        "\\", ""
    ) or "a-z0-9_" in SPEC, "Part-name rule is not stated in SPEC.md"


def test_default_part_colour_matches_spec() -> None:
    from cad_mcp.session import Part

    assert Part(name="x").color == "steel", (
        "SPEC 10.3 documents create_part(color='steel') as the default"
    )
