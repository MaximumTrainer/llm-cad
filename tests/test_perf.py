"""SPEC N2 latency budgets, enforced (CAD-014 / #15).

CLAUDE.md calls a render slower than ~2s a regression, and SPEC N2 sets
execute <=5s, render <=2s, validate <=5s. Nothing measured any of them.

Measured before this work, on a simple bracket:

    execute_cad, cold subprocess   3.56s
    execute_cad, second call       5.51s   <- at/over the 5s budget
    tessellate (first call)        3.35s
    render_views, first call       1.65s

Essentially all of the execute cost was the CadQuery import (a bare
interpreter starts in 0.14s; one that has imported CadQuery takes 3.3s),
paid again on every call and again for every block of an append-mode
replay. That is what limited how many iterations the LLM could afford
inside SPEC 9.1's four-iteration target.

These tests are marked `slow`. They assert budgets with headroom, since
CI runners vary; the point is to catch an order-of-magnitude regression,
not to benchmark the host.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import anyio
import pytest

from cad_mcp import render, sandbox, session
from cad_mcp.server import mcp

from .envelope_helpers import flat

pytestmark = [pytest.mark.geometry, pytest.mark.slow]

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)"

# The SPEC 9.1 reference part.
BRACKET = """
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 3)
    .faces(">Z")
    .workplane()
    .rect(40, 20, forConstruction=True)
    .vertices()
    .hole(4.5)
    .faces("<Y")
    .workplane()
    .transformed(offset=(0, 1.5, 15))
    .rect(50, 30)
    .extrude(3)
)
"""

# Budgets with headroom over the SPEC figures.
EXECUTE_BUDGET_S = 5.0
RENDER_BUDGET_S = 2.0
VALIDATE_BUDGET_S = 5.0


@pytest.fixture
def warm() -> None:
    """Ensure a pre-warmed worker exists, as it does under the server."""
    sandbox.prewarm()
    # Give the spare time to finish importing CadQuery.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if sandbox.warm_worker.POOL._spare is not None:
            return
        time.sleep(0.25)
    pytest.skip("warm worker did not become ready in time")


# ------------------------------------------------------------------
# execute_cad
# ------------------------------------------------------------------


def test_execute_is_within_budget_when_warm(warm: None) -> None:
    """The server pre-warms at start-up, so this is the real path."""

    async def scenario() -> float:
        start = time.perf_counter()
        result = await mcp.call_tool("execute_cad", {"code": BRACKET})
        elapsed = time.perf_counter() - start
        assert flat(result)["ok"], "bracket failed to build"
        return elapsed

    elapsed = anyio.run(scenario)
    assert elapsed < EXECUTE_BUDGET_S, (
        f"execute_cad took {elapsed:.2f}s, SPEC N2 budget is "
        f"{EXECUTE_BUDGET_S}s"
    )


def test_append_replay_stays_within_budget(warm: None) -> None:
    """Append mode re-runs the whole history, so cost grows with the
    conversation. Five blocks must still fit the budget."""

    async def scenario() -> float:
        await mcp.call_tool("execute_cad", {"code": BOX})
        for i in range(4):
            await mcp.call_tool(
                "execute_cad",
                {
                    "code": (
                        f"result = result.faces('>Z').workplane()"
                        f".rect({2 + i}, {2 + i}).cutBlind(-1)"
                    ),
                    "mode": "append",
                },
            )
        sess = session.for_context(None)
        assert len(sess.get_active_part().code_history) == 5

        start = time.perf_counter()
        await mcp.call_tool(
            "execute_cad",
            {"code": "result = result.edges('|Z').fillet(0.5)", "mode": "append"},
        )
        return time.perf_counter() - start

    elapsed = anyio.run(scenario)
    assert elapsed < EXECUTE_BUDGET_S, (
        f"a 6-block append replay took {elapsed:.2f}s, budget is "
        f"{EXECUTE_BUDGET_S}s"
    )


def test_warm_worker_removes_the_import_from_the_critical_path() -> None:
    """The measured win: ~2.9s cold vs ~0.25s warm.

    Asserted as a ratio rather than an absolute, so it survives a slower
    machine but still fails if the warm path stops working.
    """
    tmp = Path(tempfile.mkdtemp(prefix="cad-mcp-perf-"))

    sandbox.warm_worker.POOL.shutdown()
    start = time.perf_counter()
    cold_result = sandbox.run(BOX, tmp, brep_out=tmp / "cold.brep")
    cold = time.perf_counter() - start
    assert cold_result.ok

    sandbox.prewarm()
    deadline = time.monotonic() + 60
    while (
        sandbox.warm_worker.POOL._spare is None
        and time.monotonic() < deadline
    ):
        time.sleep(0.25)
    if sandbox.warm_worker.POOL._spare is None:
        pytest.skip("warm worker did not become ready in time")

    start = time.perf_counter()
    warm_result = sandbox.run(BOX, tmp, brep_out=tmp / "warm.brep")
    warm_elapsed = time.perf_counter() - start
    assert warm_result.ok

    assert warm_elapsed < cold / 2, (
        f"warm run ({warm_elapsed:.2f}s) was not meaningfully faster than "
        f"cold ({cold:.2f}s) — the pre-warmed worker is not being used"
    )


def test_warm_worker_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator must be able to turn the optimisation off."""
    monkeypatch.setenv("CAD_MCP_WARM_WORKER", "0")
    assert sandbox.warm_worker.enabled() is False
    assert sandbox.warm_worker.POOL.take() is None

    tmp = Path(tempfile.mkdtemp(prefix="cad-mcp-perf-off-"))
    result = sandbox.run(BOX, tmp, brep_out=tmp / "o.brep")
    assert result.ok, "the cold path broke when warming was disabled"


# ------------------------------------------------------------------
# render and validate
# ------------------------------------------------------------------


def test_render_is_within_budget() -> None:
    """CLAUDE.md: slower than ~2s is a regression."""

    async def build() -> Path:
        await mcp.call_tool("execute_cad", {"code": BRACKET})
        return session.for_context(None).brep_path()

    brep = anyio.run(build)

    # Warm the in-process import once; the server is long-lived, so
    # steady state is what matters.
    render.render_views(brep)

    start = time.perf_counter()
    png = render.render_views(brep)
    elapsed = time.perf_counter() - start

    assert len(png) > 1000
    assert elapsed < RENDER_BUDGET_S, (
        f"render took {elapsed:.2f}s on the {render.active_backend()} "
        f"backend, budget is {RENDER_BUDGET_S}s"
    )


def test_validate_is_within_budget() -> None:
    async def scenario() -> float:
        await mcp.call_tool("execute_cad", {"code": BRACKET})
        # Warm the geometry stack once.
        await mcp.call_tool("validate_mesh", {})

        start = time.perf_counter()
        result = await mcp.call_tool("validate_mesh", {})
        elapsed = time.perf_counter() - start
        assert flat(result)["ok"]
        return elapsed

    elapsed = anyio.run(scenario)
    assert elapsed < VALIDATE_BUDGET_S, (
        f"validate_mesh took {elapsed:.2f}s, budget is "
        f"{VALIDATE_BUDGET_S}s"
    )


def test_the_whole_spec_9_1_loop_fits_a_sane_wall_clock(
    warm: None,
) -> None:
    """execute -> render -> measure -> validate -> export, once.

    Four iterations of this is what SPEC 9.1 asks an LLM to do, so the
    single-pass cost bounds whether that is realistic.
    """

    async def scenario() -> float:
        start = time.perf_counter()
        await mcp.call_tool("execute_cad", {"code": BRACKET})
        await mcp.call_tool("render_views", {})
        await mcp.call_tool("measure", {"what": "bbox"})
        await mcp.call_tool("validate_mesh", {})
        await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "perf"}
        )
        return time.perf_counter() - start

    elapsed = anyio.run(scenario)
    budget = EXECUTE_BUDGET_S + RENDER_BUDGET_S + VALIDATE_BUDGET_S + 5.0
    assert elapsed < budget, (
        f"one full design pass took {elapsed:.2f}s; four of those is "
        f"what SPEC 9.1 asks of the LLM (budget {budget}s)"
    )
