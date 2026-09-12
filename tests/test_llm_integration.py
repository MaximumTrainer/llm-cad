"""Live LLM integration tests: a real model driving cad-mcp via OpenRouter.

Every other test in this suite calls the tools directly with hard-coded
CadQuery source — `test_acceptance.py` even says so ("programmatic, no
LLM needed").  That leaves the parts of the product that only matter to
an LLM completely unverified:

* the tool JSON schemas the server publishes (a malformed one is
  rejected by the provider, not by `mcp.call_tool`);
* the image round-trip that SPEC G3 calls the whole point — whether a
  render is actually *readable*, including the mm scale ticks SPEC 5.1
  mandates;
* whether the structured errors of SPEC N3 are actionable enough for a
  model to recover from without a human;
* SPEC 9.1 itself, which is written about an LLM iterating, not about
  replaying a known-good example.

These tests cost money and need network, so they are gated twice: they
skip without `OPENROUTER_API_KEY`, and the full acceptance run also
needs `CAD_MCP_LLM_ACCEPTANCE=1` so nobody burns credits by accident.

Run them::

    export OPENROUTER_API_KEY=sk-or-...
    uv run pytest tests/test_llm_integration.py -m llm -v

    # including the full SPEC 9.1 design loop (needs a capable model)
    CAD_MCP_LLM_ACCEPTANCE=1 uv run pytest -m llm -v

Never commit a key.  See `.env.example`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cad_mcp import session
from cad_mcp.server import mcp

from . import llm_harness as H

pytestmark = [pytest.mark.llm, pytest.mark.integration]

CYLINDER_CODE = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').circle(10).extrude(60)"
)

# Fails on the first run: a 20mm fillet cannot fit a 10mm-thick plate.
# Used to prove the SPEC N3 error is actionable for a real consumer.
BAD_FILLET_CODE = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(40, 40, 10).edges('|Z').fillet(20)"
)


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:  # type: ignore[misc]
    session.cleanup_all()


@pytest.fixture(autouse=True)
def _require_key() -> None:  # type: ignore[misc]
    if not H.available():
        pytest.skip("OPENROUTER_API_KEY is not set")


async def _run(**kwargs: object) -> H.LoopResult:
    """Run the loop, turning a funding failure into a skip, not a failure."""
    try:
        return await H.run_design_loop(mcp, **kwargs)  # type: ignore[arg-type]
    except H.CreditsExhausted as exc:
        pytest.skip(f"OpenRouter credits exhausted: {exc}")


# ------------------------------------------------------------------
# 1. The published tool schemas are usable by a real model
# ------------------------------------------------------------------


def test_tool_schemas_convert_to_openai_functions() -> None:
    """Offline guard: every tool yields a well-formed function definition."""
    import anyio

    tools = anyio.run(mcp.list_tools)
    converted = H.mcp_tools_to_openai(tools)

    assert len(converted) == len(tools) == 14

    for fn_def in converted:
        fn = fn_def["function"]
        assert fn["name"], "tool with no name"
        assert fn["description"].strip(), f"{fn['name']} has no description"
        params = fn["parameters"]
        assert params["type"] == "object", f"{fn['name']} schema is not object"
        # A provider rejects `required` naming a property that isn't declared.
        declared = set(params.get("properties", {}))
        for req in params.get("required", []):
            assert req in declared, (
                f"{fn['name']}: required '{req}' is not in properties"
            )


@pytest.mark.anyio
async def test_model_can_call_a_tool() -> None:
    """The server's schemas are accepted end to end by a live provider.

    Deliberately the cheapest possible check: it proves auth, schema
    validity, tool dispatch and result plumbing without paying for
    reasoning.
    """
    result = await _run(
        user_request=(
            "Call the ping tool once, then tell me exactly what it returned."
        ),
        system_prompt="You are testing an MCP server. Use the tools available.",
        include_tools={"ping"},
        max_turns=4,
        model=H.CHEAP_MODEL,
        tokens=512,
    )

    assert result.count("ping") >= 1, (
        f"Model never called ping. Transcript:\n{result.transcript()}"
    )
    assert "pong" in result.final_text.lower(), (
        f"Model did not report the result.\n{result.transcript()}"
    )


# ------------------------------------------------------------------
# 2. The render is actually readable — SPEC G3 and 5.1
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_model_can_read_shape_and_scale_from_render() -> None:
    """A render must convey both form and dimension to the model.

    This is the closed visual feedback loop of SPEC G3. The model is
    given a 20mm-diameter, 60mm-tall cylinder and asked to name the form
    and read its height from the axis ticks — the "mm scale ticks"
    requirement of SPEC 5.1 — with no access to the source code.
    """
    setup = await mcp.call_tool("execute_cad", {"code": CYLINDER_CODE})
    assert setup.content[0].text.startswith("OK")  # type: ignore[union-attr]

    result = await _run(
        user_request=(
            "Call render_views once. Then, looking ONLY at the image, "
            "answer: is the shape a CYLINDER or a CUBE? Roughly how tall "
            "is it in mm, reading the axis tick labels? "
            "Answer in one sentence."
        ),
        system_prompt=(
            "You inspect 3D CAD renders. Use the tools, then answer "
            "from the image."
        ),
        include_tools={"render_views"},
        max_turns=4,
        model=H.CHEAP_MODEL,
        tokens=400,
    )

    assert result.count("render_views") >= 1, (
        f"Model never rendered.\n{result.transcript()}"
    )
    assert result.images_returned() >= 1, (
        "render_views returned no ImageContent — the visual feedback loop "
        f"is broken.\n{result.transcript()}"
    )

    answer = result.final_text.lower()
    assert "cylinder" in answer, (
        "Model could not identify the form from the render.\n"
        f"{result.transcript()}"
    )
    # Accept any reading near 60mm; this asserts the ticks are legible,
    # not that the model is a precision instrument.
    assert any(str(n) in answer for n in (55, 56, 57, 58, 59, 60, 61, 62)), (
        "Model could not read the scale from the render — check that the "
        f"active backend draws mm ticks (see CAD-010).\n{result.transcript()}"
    )


# ------------------------------------------------------------------
# 3. Structured errors are actionable — SPEC N3
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_model_recovers_from_a_structured_error() -> None:
    """SPEC N3 promises errors a model can act on. Verify with a model.

    The supplied code asks for a 20mm fillet on a 10mm-thick plate, which
    the kernel cannot do. The server must report what failed, where, and
    a usable hint; success is the model fixing it unaided.
    """
    result = await _run(
        user_request=(
            "Run this exact code with execute_cad:\n\n"
            f"```python\n{BAD_FILLET_CODE}\n```\n\n"
            "If it fails, read the error message carefully, fix the code, "
            "and call execute_cad again until it succeeds. Do not give up "
            "after the first failure. When it succeeds, say DONE."
        ),
        system_prompt=(
            "You write CadQuery code against an MCP server. Assign the "
            "final shape to `result`. Act on the error messages you get."
        ),
        include_tools={"execute_cad"},
        max_turns=6,
        model=H.CHEAP_MODEL,
        tokens=900,
    )

    calls = result.calls_to("execute_cad")
    assert len(calls) >= 2, (
        "Expected a failure then a fix; the first call may not have "
        f"failed as intended.\n{result.transcript()}"
    )

    first = calls[0].text_result
    assert not first.startswith("OK"), (
        f"The bad-fillet code was expected to fail.\n{result.transcript()}"
    )
    # N3: what failed, where, and a hint.
    assert "line" in first.lower(), (
        f"Error carries no line number (SPEC N3).\n{first}"
    )
    assert "hint" in first.lower(), f"Error carries no hint (SPEC N3).\n{first}"

    assert any(c.text_result.startswith("OK") for c in calls[1:]), (
        "Model never recovered — the error was not actionable enough.\n"
        f"{result.transcript()}"
    )


# ------------------------------------------------------------------
# 4. SPEC 9.1, as actually written: an LLM iterating to a valid STL
# ------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.anyio
async def test_spec_9_1_llm_designs_bracket_end_to_end() -> None:
    """SPEC 9.1 with a real model in the loop.

    "From a cold start ... the prompt 'design a wall-mount bracket for a
    30mm pipe, two M4 screw holes, 3mm walls' produces a validated,
    watertight STL within <=4 LLM iterations, no human code edits."

    The existing programmatic test replays `EXAMPLES["bracket"]`, so it
    cannot fail for the reasons this one can: unclear tool descriptions,
    an unteachable workflow prompt, or unreadable renders.

    The system prompt is the server's own `design_workflow` prompt, so a
    failure here is a finding about the shipped pedagogy (SPEC G6).

    Cost: a full run is roughly 100k+ prompt tokens against the default
    model. A weak `OPENROUTER_MODEL` will fail this for capability
    reasons, not server reasons; read the transcript in the failure
    output before filing anything.
    """
    if os.environ.get("CAD_MCP_LLM_ACCEPTANCE") != "1":
        pytest.skip("set CAD_MCP_LLM_ACCEPTANCE=1 to run the paid full loop")

    result = await _run(
        user_request=H.SPEC_9_1_REQUEST,
        max_turns=16,
        tokens=8000,
    )

    transcript = result.transcript()

    # The mandated loop: render after executing, not just at the end.
    assert result.count("execute_cad") >= 1, f"No geometry built.\n{transcript}"
    assert result.count("render_views") >= 1, (
        f"Model never rendered — the workflow prompt failed to "
        f"mandate it (SPEC 5.2).\n{transcript}"
    )
    assert result.images_returned() >= 1, f"No images reached the model.\n{transcript}"

    # <=4 iterations, where an iteration is a successful geometry build.
    builds = result.calls_to("execute_cad")
    assert len(builds) <= 4, (
        f"Took {len(builds)} execute_cad calls, SPEC 9.1 allows 4.\n"
        f"{transcript}"
    )

    # The model must have reached a watertight verdict through the tools.
    validations = result.calls_to("validate_mesh")
    assert validations, f"Model never validated.\n{transcript}"
    final_report = json.loads(validations[-1].text_result)
    watertight = final_report.get("watertight")
    if watertight is None:  # multi-part shape
        watertight = all(
            p.get("watertight") for p in final_report.get("parts", [])
        )
    assert watertight, f"Final model is not watertight.\n{transcript}"

    # And exported something real.
    exports = result.calls_to("export_model")
    assert exports, f"Model never exported.\n{transcript}"
    payload = json.loads(exports[-1].text_result)
    path = payload.get("path") or (payload.get("files") or [{}])[0].get("path")
    assert path and Path(path).exists(), f"Export path missing.\n{transcript}"
    assert Path(path).stat().st_size > 1000, (
        f"Export is suspiciously small.\n{transcript}"
    )
