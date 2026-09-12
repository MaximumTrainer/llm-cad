"""Per-session isolation, idle eviction, and within-session concurrency.

Covers CAD-007 (every tool shared one global session) and CAD-008
(concurrent calls raced on shared sandbox files).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import anyio
import pytest

from cad_mcp import session

from .envelope_helpers import flat, summary

BOX_10 = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)"
BOX_50 = "import cadquery as cq\nresult = cq.Workplane('XY').box(50, 50, 50)"


class FakeContext:
    """Stands in for an MCP request context carrying a session header."""

    def __init__(self, session_id: str | None) -> None:
        self._session_id = session_id

    @property
    def headers(self) -> dict[str, str]:
        if self._session_id is None:
            return {}
        return {"Mcp-Session-Id": self._session_id}


class ExplodingContext:
    """A context outside a request — `headers` raises, as the SDK does."""

    @property
    def headers(self) -> dict[str, str]:
        raise ValueError("Context is not available outside of a request")


# Captured once, at import, so a nested or concurrent `as_session` can
# never restore an already-patched resolver and leak into later test
# files (which is exactly what happened the first time round).
_REAL_RESOLVE_ID = session.resolve_id


@pytest.fixture(autouse=True)
def _clean() -> Any:
    session.cleanup_all()
    yield
    # Belt and braces: never let a patched resolver escape this module.
    session.resolve_id = _REAL_RESOLVE_ID  # type: ignore[assignment]
    session.cleanup_all()


class as_session:
    """Run tool calls as a chosen MCP session.

    The SDK injects `Context` itself and strips it from tool arguments, so
    a test cannot pass one through `call_tool`. Overriding the resolver is
    the equivalent seam: it is exactly what `Context.headers` feeds, and
    `resolve_id` is unit-tested separately against real header shapes.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def __enter__(self) -> as_session:
        session.resolve_id = lambda ctx=None: self.session_id  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        session.resolve_id = _REAL_RESOLVE_ID  # type: ignore[assignment]


# ------------------------------------------------------------------
# CAD-007: session resolution
# ------------------------------------------------------------------


def test_no_tool_calls_get_or_create_without_a_session_id() -> None:
    """Regression guard: the bug was 14 zero-argument call sites."""
    offenders = []
    for path in Path("src/cad_mcp/tools").glob("*.py"):
        if "session.get_or_create()" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, (
        f"These tools resolve a global session instead of the request's: "
        f"{offenders}. Use session.for_context(ctx)."
    )


def test_distinct_headers_give_distinct_sessions() -> None:
    a = session.for_context(FakeContext("client-a"))
    b = session.for_context(FakeContext("client-b"))

    assert a.session_id == "client-a"
    assert b.session_id == "client-b"
    assert a is not b
    assert a.tmpdir != b.tmpdir


def test_missing_context_falls_back_to_default() -> None:
    assert session.resolve_id(None) == "default"
    assert session.resolve_id(FakeContext(None)) == "default"
    assert session.resolve_id(ExplodingContext()) == "default"


def test_header_lookup_is_case_insensitive() -> None:
    class LowerCtx:
        @property
        def headers(self) -> dict[str, str]:
            return {"mcp-session-id": "abc"}

    assert session.resolve_id(LowerCtx()) == "abc"


def test_two_sessions_do_not_see_each_others_models() -> None:
    """The core leak: client B's model overwrote client A's."""
    from cad_mcp.server import mcp

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        with as_session("sess-a"):
            await mcp.call_tool("execute_cad", {"code": BOX_10})
        with as_session("sess-b"):
            await mcp.call_tool("execute_cad", {"code": BOX_50})
        with as_session("sess-a"):
            ra = await mcp.call_tool("measure", {"what": "bbox"})
        with as_session("sess-b"):
            rb = await mcp.call_tool("measure", {"what": "bbox"})
        return (
            flat(ra),  # type: ignore[union-attr]
            flat(rb),  # type: ignore[union-attr]
        )

    a_box, b_box = anyio.run(scenario)

    assert a_box["dimensions"]["x"] == pytest.approx(10, abs=0.01), (
        "Session A's model was overwritten by session B"
    )
    assert b_box["dimensions"]["x"] == pytest.approx(50, abs=0.01)


def test_code_history_does_not_leak_between_sessions() -> None:
    from cad_mcp.server import mcp

    async def scenario() -> dict[str, Any]:
        with as_session("private"):
            await mcp.call_tool("execute_cad", {"code": BOX_10})
        with as_session("other"):
            listed = await mcp.call_tool("list_session", {})
        return flat(listed)

    other = anyio.run(scenario)
    assert other["session_id"] == "other"
    assert not other["has_model"]
    assert other["code"] is None, "Another session's code leaked"


def test_reset_only_affects_the_calling_session() -> None:
    from cad_mcp.server import mcp

    async def scenario() -> bool:
        with as_session("keeper"):
            await mcp.call_tool("execute_cad", {"code": BOX_10})
        with as_session("dropper"):
            await mcp.call_tool("execute_cad", {"code": BOX_10})
            await mcp.call_tool("reset_session", {})
        with as_session("keeper"):
            listed = await mcp.call_tool("list_session", {})
        return bool(flat(listed)["has_model"])  # type: ignore[union-attr]

    assert anyio.run(scenario), "reset_session wiped an unrelated session"


# ------------------------------------------------------------------
# CAD-007: idle eviction (SPEC 10.1 H4)
# ------------------------------------------------------------------


def test_idle_sessions_are_evicted_and_their_tmpdirs_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "0.001")
    sess = session.for_context(FakeContext("ephemeral"))
    tmpdir = sess.tmpdir
    assert tmpdir.exists()

    import time

    time.sleep(0.05)
    evicted = session.evict_idle()

    assert "ephemeral" in evicted
    assert not tmpdir.exists(), "Evicted session leaked its temp directory"
    assert "ephemeral" not in session.active_ids()


def test_active_sessions_are_not_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "3600")
    session.for_context(FakeContext("busy"))
    assert session.evict_idle() == []
    assert "busy" in session.active_ids()


def test_the_requested_session_is_never_swept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eviction must not destroy the session the caller is asking for.

    Found the hard way: under load a gap between two tool calls exceeded
    the idle timeout and `validate_mesh` reported NoModel for a model
    that had just been built.
    """
    monkeypatch.setenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "0.001")
    ctx = FakeContext("in-use")
    first = session.for_context(ctx)
    import time

    time.sleep(0.05)
    again = session.for_context(ctx)
    assert again is first, "the in-flight session was evicted and recreated"
    assert first.tmpdir.exists()


def test_stdio_default_session_is_never_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdio has one client for the process lifetime; H4 is about HTTP."""
    monkeypatch.setenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "0.001")
    sess = session.get_or_create()
    import time

    time.sleep(0.05)
    assert session.evict_idle() == []
    assert sess.tmpdir.exists()


def test_a_session_with_a_held_lock_is_not_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held lock means a tool is mid-flight against that session.

    The lock must be held by a *different* thread: it is an RLock, so a
    same-thread `acquire(blocking=False)` would succeed and the test
    would prove nothing.
    """
    import time

    monkeypatch.setenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", "0.001")
    sess = session.for_context(FakeContext("working"))
    time.sleep(0.05)

    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with sess.lock:
            holding.set()
            release.wait(timeout=10)

    worker = threading.Thread(target=hold, daemon=True)
    worker.start()
    assert holding.wait(timeout=5)

    assert session.evict_idle() == [], (
        "evicted a session with a request in flight"
    )
    assert sess.tmpdir.exists()

    release.set()
    worker.join(timeout=5)

    # Lock released: it may now be swept.
    assert "working" in session.evict_idle()


def test_idle_timeout_default_is_300s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAD_MCP_SESSION_IDLE_TIMEOUT_S", raising=False)
    assert session.idle_timeout_s() == 300.0


# ------------------------------------------------------------------
# CAD-008: within-session concurrency
# ------------------------------------------------------------------


def test_concurrent_executes_produce_whole_not_mixed_geometry() -> None:
    """Concurrent builds must never leave torn or blended geometry.

    Scope note: `active_part` is session state, so an interleaved
    `set_active_part` -> `execute_cad` *pair* is inherently racy and is
    the caller's to sequence — as it would be in any stateful protocol.
    What the per-session lock guarantees is that each `execute_cad` runs
    to completion against its own scratch files, so the BREP left behind
    is exactly one run's output rather than a mixture of two.
    """
    from cad_mcp.server import mcp

    sess = session.for_context(FakeContext("concurrent"))

    def build(code: str) -> str:
        async def go() -> str:
            with as_session("concurrent"):
                result = await mcp.call_tool("execute_cad", {"code": code})
            return summary(result)

        return anyio.run(go)  # type: ignore[no-any-return]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = [
            f.result()
            for f in [
                pool.submit(build, BOX_10),
                pool.submit(build, BOX_50),
            ]
        ]

    assert all(o.startswith("OK") for o in outputs), outputs

    # Exactly one of the two shapes survived, intact — not a blend.
    final = sess.brep_path()
    assert final.exists()

    from cad_mcp.render import load_and_tessellate

    verts, _ = load_and_tessellate(final)
    extent = float(verts[:, 0].max() - verts[:, 0].min())
    assert extent == pytest.approx(10, abs=0.5) or extent == pytest.approx(
        50, abs=0.5
    ), f"Geometry is a mixture of both runs: x-extent {extent}"


def test_concurrent_runs_use_separate_scratch_dirs() -> None:
    """The shared `user_code.py`/`current.brep` race (CAD-008)."""
    from cad_mcp import sandbox

    sess = session.for_context(FakeContext("parallel-runs"))
    seen: list[str] = []
    lock = threading.Lock()

    def go(code: str) -> None:
        result = sandbox.run(code, sess.tmpdir)
        with lock:
            seen.append(result.format_for_llm())

    with ThreadPoolExecutor(max_workers=3) as pool:
        for f in [
            pool.submit(go, BOX_10),
            pool.submit(go, BOX_50),
            pool.submit(go, BOX_10),
        ]:
            f.result()

    assert all(s.startswith("OK") for s in seen), seen
    run_dirs = sorted(sess.tmpdir.glob("run-*"))
    assert len(run_dirs) >= 3, (
        f"Concurrent runs shared scratch directories: {run_dirs}"
    )


def test_each_run_gets_its_own_working_files() -> None:
    """`user_code.py`/`current.brep` used to be shared per session."""
    from cad_mcp import sandbox

    sess = session.for_context(FakeContext("runs"))
    sandbox.run(BOX_10, sess.tmpdir)
    sandbox.run(BOX_50, sess.tmpdir)

    run_dirs = sorted(sess.tmpdir.glob("run-*"))
    assert len(run_dirs) >= 2, "Runs shared a directory"
    assert not (sess.tmpdir / "user_code.py").exists(), (
        "Sandbox still writes a shared user_code.py"
    )


def test_failed_run_does_not_clobber_good_geometry() -> None:
    from cad_mcp.server import mcp

    async def scenario() -> tuple[bytes, bytes, str]:
        with as_session("rollback"):
            await mcp.call_tool("execute_cad", {"code": BOX_10})
            sess = session.for_context(None)
            before = sess.brep_path().read_bytes()
            bad = await mcp.call_tool(
                "execute_cad", {"code": "result = nope()"}
            )
            after = sess.brep_path().read_bytes()
        return before, after, summary(bad)

    before, after, message = anyio.run(scenario)
    assert before == after, "A failed run overwrote the previous geometry"
    assert not message.startswith("OK")


def test_session_lock_is_reentrant() -> None:
    """Nested acquisition must not deadlock a single tool call."""
    sess = session.for_context(FakeContext("reentrant"))
    assert isinstance(sess.lock, type(threading.RLock()))
    with sess.lock, sess.lock:
        assert True
