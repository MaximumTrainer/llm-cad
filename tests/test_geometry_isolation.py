"""Issue #10: an OCP crash must cost a subprocess, not the server.

SPEC §7 isolated *user* code and stopped there. Tessellation, measurement,
STEP export and boolean interference all loaded the B-rep and called OCP
inside the server process, where a degenerate shape is a segfault rather
than an exception -- so the MCP connection died mid-conversation and the
model got a transport error instead of the "what / where / hint" N3
promises.

The two things worth testing are therefore:

* OCP is genuinely not in the server's address space any more, because a
  single stray import puts the crash surface straight back;
* when the kernel process dies mid-operation, the caller gets a
  structured error and the *next* call works.

The crash is simulated by substituting a worker that exits with a signal
on its first request. That is deliberate: it exercises the parent's
recovery logic exactly, whereas hunting for a B-rep that reliably
segfaults a particular OCCT build would test the OS's behaviour and be
flaky on every other build.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cad_mcp import geometry
from cad_mcp.geometry import GeometryKernelError, GeometryWorker

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 10, 5)\n"


# ------------------------------------------------------------------
# The crash path
# ------------------------------------------------------------------


def _worker_that_dies(how: str) -> GeometryWorker:
    """A GeometryWorker whose child dies on its first request."""
    import tempfile

    script = textwrap.dedent(
        f"""
        import os, sys
        print({geometry.READY!r}, file=sys.stderr, flush=True)
        sys.stdin.readline()
        {how}
        """
    )
    worker = GeometryWorker()

    def spawn() -> object:
        path = Path(tempfile.mkdtemp(prefix="cad-crash-")) / "w.py"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stderr is not None
        proc.stderr.readline()  # consume READY
        return geometry._Worker(
            proc=proc, workdir=Path(tempfile.mkdtemp(prefix="cad-crashwd-"))
        )

    worker._spawn = spawn  # type: ignore[method-assign]
    return worker


@pytest.mark.parametrize(
    ("label", "how"),
    [
        ("hard exit", "os._exit(1)"),
        ("signal death", "os.kill(os.getpid(), 9)"),
    ],
)
def test_a_dying_kernel_becomes_a_structured_error(
    label: str, how: str
) -> None:
    """Not a traceback, not a hang, and not the server going down."""
    worker = _worker_that_dies(how)

    with pytest.raises(GeometryKernelError) as excinfo:
        worker.call("ping", {}, timeout=30)

    err = excinfo.value
    assert err.crashed is True
    assert err.hint, "SPEC N3 requires a hint, and a crash needs one most"
    # The message has to say what failed and that the server is fine,
    # because the LLM's next move depends on both.
    assert "ping" in str(err)
    assert "kernel" in str(err).lower()
    worker.shutdown()


def test_the_worker_is_replaced_after_a_crash() -> None:
    """Acceptance: the server answers the *next* request normally."""
    worker = _worker_that_dies("os._exit(1)")

    with pytest.raises(GeometryKernelError):
        worker.call("ping", {}, timeout=30)

    # Drop the crash-stub spawner; the next call gets a real kernel.
    worker._spawn = GeometryWorker._spawn.__get__(worker)  # type: ignore[method-assign]
    result = worker.call("ping", {}, timeout=120)

    assert "cadquery" in result, result
    worker.shutdown()


@pytest.mark.geometry
@pytest.mark.slow
def test_the_server_still_answers_ping_after_a_kernel_crash() -> None:
    """The whole point, end to end: MCP survives the kernel dying."""
    import anyio

    from cad_mcp.server import mcp

    # Kill whatever kernel process is live, mid-session.
    geometry.POOL.shutdown()

    async def scenario() -> str:
        result = await mcp.call_tool("ping", {})
        return str(result.content[0].text)  # type: ignore[union-attr]

    assert "pong" in anyio.run(scenario).lower()


# ------------------------------------------------------------------
# The isolation itself
# ------------------------------------------------------------------


@pytest.mark.geometry
@pytest.mark.slow
def test_ocp_never_enters_the_server_process(tmp_path: Path) -> None:
    """One stray import restores the whole crash surface.

    Run in a child interpreter because the test session imports cadquery
    directly in other modules, so `sys.modules` here says nothing.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            f"""
            import os, sys
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / "src")!r})
            os.environ["CAD_MCP_OUTPUT_DIR"] = {str(tmp_path / "out")!r}

            import anyio
            from cad_mcp.server import mcp

            async def main():
                await mcp.call_tool("execute_cad", {{"code": {BOX!r}}})
                await mcp.call_tool("render_views", {{}})
                await mcp.call_tool("validate_mesh", {{}})
                await mcp.call_tool("measure", {{"what": "bbox"}})
                await mcp.call_tool(
                    "export_model", {{"format": "step", "filename": "p"}}
                )

            anyio.run(main)

            leaked = sorted(
                m for m in sys.modules
                if m == "OCP" or m.startswith("OCP.") or m == "cadquery"
            )
            print("LEAKED:" + ",".join(leaked))
            sys.stdout.flush()
            os._exit(0)
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("LEAKED:")),
        None,
    )
    assert line is not None, (
        f"probe did not report:\nstdout={proc.stdout[-2000:]}\n"
        f"stderr={proc.stderr[-3000:]}"
    )
    leaked = [m for m in line[len("LEAKED:") :].split(",") if m]
    assert not leaked, (
        f"the server process imported the geometry kernel: {leaked}. "
        f"Every OCP call must go through cad_mcp.geometry."
    )


@pytest.mark.geometry
@pytest.mark.slow
def test_tessellation_is_cached_per_brep_and_tolerance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: the extra process hop must not multiply render cost."""
    from cad_mcp import render, sandbox

    geometry.clear_tessellation_cache()
    brep = tmp_path / "m.brep"
    assert sandbox.run(BOX, tmp_path, brep_out=brep).ok

    calls: list[str] = []
    real_call = geometry.call

    def counting_call(op: str, **args: object) -> dict[str, object]:
        calls.append(op)
        return real_call(op, **args)

    monkeypatch.setattr(geometry, "call", counting_call)

    first = render.load_and_tessellate(brep)
    second = render.load_and_tessellate(brep)

    assert calls.count("tessellate") == 1, (
        f"tessellated twice for one unchanged B-rep: {calls}"
    )
    assert (first[0] == second[0]).all()
    assert (first[1] == second[1]).all()

    # A different tolerance is a different mesh, so it must not be served
    # from the cache.
    render.load_and_tessellate(brep, 0.5, 0.5)
    assert calls.count("tessellate") == 2, calls


@pytest.mark.geometry
@pytest.mark.slow
def test_a_rebuilt_part_is_not_served_a_stale_mesh(tmp_path: Path) -> None:
    """`execute_cad` rewrites the same path, so path alone is not a key."""
    from cad_mcp import render, sandbox

    geometry.clear_tessellation_cache()
    brep = tmp_path / "m.brep"

    assert sandbox.run(BOX, tmp_path, brep_out=brep).ok
    small = render.load_and_tessellate(brep)[0]

    bigger = "import cadquery as cq\nresult = cq.Workplane('XY').box(80, 40, 20)\n"
    assert sandbox.run(bigger, tmp_path, brep_out=brep).ok
    large = render.load_and_tessellate(brep)[0]

    assert large.max() > small.max(), (
        "the cache served geometry from before the rebuild"
    )
