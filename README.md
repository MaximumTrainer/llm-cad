# cad-mcp

MCP server that lets any LLM design 3D models from text descriptions using CadQuery.

**[Documentation & User Guide](https://maximumtrainer.github.io/llm-cad/)** | **[GitHub](https://github.com/MaximumTrainer/llm-cad)**

## Quick start

```bash
# Install uv if you don't have it
# https://docs.astral.sh/uv/getting-started/installation/

# Clone and install
git clone https://github.com/MaximumTrainer/llm-cad.git && cd llm-cad
uv sync

# Run the smoke test (builds a bracket, renders, validates, exports)
uv run python scripts/smoke.py

# Run the server (stdio transport)
uv run cad-mcp
```

## Connecting to an MCP host

### Claude Code

From the repo directory:

```bash
claude mcp add cad-mcp -- uv run cad-mcp
```

Or add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "cad-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/cad-mcp", "cad-mcp"]
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cad-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/cad-mcp", "cad-mcp"]
    }
  }
}
```

### Any stdio MCP client

The server speaks MCP over stdio. Launch with:

```bash
uv run --directory /path/to/cad-mcp cad-mcp
```

### Streamable HTTP transport

For remote clients or web UIs, run the server over HTTP:

```bash
# Start on default port 8000
uv run cad-mcp --transport http

# Or via env var
CAD_MCP_TRANSPORT=http uv run cad-mcp
```

**Environment variables.** Every variable the server reads, grouped by what it
affects. Defaults are the ones in the code, not aspirations.

### Transport

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_TRANSPORT` | `stdio` | `http` for streamable HTTP |
| `CAD_MCP_HOST` | `127.0.0.1` | Bind address |
| `CAD_MCP_PORT` | `8000` | Bind port |
| `CAD_MCP_AUTH_TOKEN` | (none) | Bearer token for auth (unset = no auth) |
| `CAD_MCP_CORS_ORIGIN` | (none) | Allowed CORS origin (e.g. `https://app.example.com`) |
| `CAD_MCP_ALLOWED_HOSTS` | (none) | Comma-separated allowed hostnames |
| `CAD_MCP_PUBLIC_URL` | (none) | Externally reachable origin, e.g. `https://cad-mcp.example.com`. Required off loopback: the bind address is not a URL clients can use, and OAuth metadata would otherwise advertise `http://0.0.0.0:8000/mcp`. |
| `CAD_MCP_STATELESS` | `0` | `1` for stateless HTTP mode. **Not** a way to avoid session affinity — it breaks the core loop, because `render_views` reads state `execute_cad` wrote. |

### Session and output

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_OUTPUT_DIR` | `./cad-mcp-output` | Where exports are written. Deliberately outside the session temp dir, which `reset_session` deletes. |
| `CAD_MCP_SESSION_IDLE_TIMEOUT_S` | `300` | Seconds an unused HTTP session is kept before eviction. |

### Sandbox (SPEC N1)

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_SANDBOX_TIMEOUT_S` | `30` | Wall-clock limit for one execution. |
| `CAD_MCP_SANDBOX_MEM_MB` | `2048` | Memory cap. Enforced on Linux and Windows; **not on macOS** — see SPEC N1. |
| `CAD_MCP_SANDBOX_FILE_MB` | `512` | Largest file model code may write. |
| `CAD_MCP_SANDBOX_MAX_PROCS` | `max(256, cpus x 32)` | Runaway-spawn brake, not a precise cap — see SPEC N1. |
| `CAD_MCP_WARM_WORKER` | `1` | `0` disables the pre-warmed worker, so every execution pays the ~3.3s CadQuery import. |

### Geometry kernel (SPEC §7)

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_GEOMETRY_ISOLATION` | `1` | `0` runs OCP in the server process again. A debugging escape hatch, not a supported mode: a kernel fault then takes the server with it. |
| `CAD_MCP_GEOMETRY_TIMEOUT_S` | `120` | Wall-clock limit for one geometry operation (tessellate, measure, STEP export). |

### Rendering

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_FORCE_BACKEND` | (auto) | `pyrender` or `matplotlib`. Forcing `pyrender` fails loudly when EGL is unavailable, rather than falling back silently. |
| `PYOPENGL_PLATFORM` | (unset) | `egl` for headless GL, `osmesa` for Mesa software rendering. |

### AI mesh and logging

| Variable | Default | Description |
|---|---|---|
| `MESHY_API_KEY` | (none) | Required by `gen_ai_mesh`; the tool returns a structured missing-key error without it. |
| `CAD_MCP_MESHY_MAX_MB` | `128` | Largest GLB accepted from the Meshy API. |
| `CAD_MCP_MAX_SEW_TRIS` | `20000` | Triangle ceiling for sewing a downloaded mesh into a B-rep. |
| `CAD_MCP_LOG_LEVEL` | `INFO` | Level for the structured per-tool-call logs, which go to **stderr** (stdout belongs to the stdio framing). |

**Health check:** `GET /health` (no auth required) reports *readiness*, not liveness. It answers `503 {"status": "starting", ...}` for the ~3s the geometry kernel takes to import CadQuery, then `200 {"status": "ok", "kernel": "ready", "render_backend": "matplotlib", "version": "0.1.0"}`. Point a load balancer at it and no client reaches a machine that cannot yet run geometry; `render_backend` tells you which of the two backends N5 allows you actually got.

### Running it as a hosted server

There is a `Dockerfile` and a `fly.toml`, plus a deploy workflow and a
smoke test that drives the full core loop over the wire:

```bash
docker build -t cad-mcp .
docker run --rm -p 8000:8000 -e CAD_MCP_AUTH_TOKEN=dev-token   -e CAD_MCP_PUBLIC_URL=http://127.0.0.1:8000   -e CAD_MCP_ALLOWED_HOSTS=127.0.0.1:8000,localhost:8000 cad-mcp

uv run python scripts/smoke_remote.py   --url http://127.0.0.1:8000 --token dev-token --repeat 10
```

See **[docs/deploy.md](docs/deploy.md)** for session affinity, secrets,
sizing, cold start, rollback and cost. SPEC §10.4 states what is
guaranteed.

**Auth:** When `CAD_MCP_AUTH_TOKEN` is set, all MCP requests require `Authorization: Bearer <token>`. Requests without a valid token receive HTTP 401.

## Tools

| Tool | Description |
|------|-------------|
| `ping` | Connectivity check. Returns `pong`. |
| `execute_cad` | Run CadQuery Python code in a sandbox. Assign result to `result`. |
| `render_views` | Render multi-angle PNG preview (front/right/top/iso) |
| `validate_mesh` | Check watertight, manifold, wall thickness, overhangs, PLA mass |
| `measure` | Numeric measurements: bbox, volume, face count, distance, clearance |
| `export_model` | Export to STL, STEP, 3MF, or GLB (per-part + assembly) |
| `gen_ai_mesh` | Generate organic 3D mesh via Meshy API (requires `MESHY_API_KEY`) |
| `create_part` | Add a named part to the assembly |
| `set_active_part` | Switch which part `execute_cad` targets |
| `position_part` | Set translation/rotation for a part |
| `list_parts` | Overview of all parts in the assembly |
| `delete_part` | Remove a part from the assembly |
| `list_session` | Show parts, code history, and exports |
| `reset_session` | Clear all state and start fresh |

## Prompts

| Prompt | Description |
|--------|-------------|
| `design_workflow` | Step-by-step loop: execute, render, critique, measure, validate, export |
| `cadquery_primer` | CadQuery cheat sheet with runnable snippets |
| `printability_checklist` | FDM printing guidelines: walls, overhangs, tolerances |

## Resources

| URI | Description |
|-----|-------------|
| `cad://session/current/code` | Current model source code |
| `cad://examples/{name}` | Curated examples: bracket, enclosure, flange, pipe_clamp, phone_stand, threaded_cap, gear, desk_organizer |

## Development

```bash
uv run pytest             # tests, parallel (live LLM + serial excluded)
uv run pytest -m serial -n0   # wall-clock budgets, run un-contended
uv run ruff check .       # lint
uv run mypy --strict src/cad_mcp  # type check
uv run python scripts/smoke.py   # end-to-end smoke test
```

### Live LLM integration tests

`tests/test_llm_integration.py` drives the server with a real model over
[OpenRouter](https://openrouter.ai), which is the only way to verify the
things that matter solely to an LLM: that the published tool schemas are
accepted by a function-calling provider, that `render_views` images are
actually *readable* (including the mm scale ticks), that the structured
errors of SPEC N3 are actionable enough to recover from, and SPEC 9.1
itself — which is written about an LLM iterating, not about replaying a
known-good example.

These cost money and need network, so they are excluded from the default
run and gated on an API key:

```bash
cp .env.example .env        # then add your key
export OPENROUTER_API_KEY=sk-or-...

uv run pytest -m llm -v     # schema, render-readability, error-recovery
```

The full SPEC 9.1 design loop is gated a second time, because it is the
expensive one (roughly 100k+ tokens against a frontier model):

```bash
CAD_MCP_LLM_ACCEPTANCE=1 uv run pytest -m llm -v
```

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required. Tests skip without it. |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-5` | Must support tool calling **and** image input. |
| `OPENROUTER_MAX_TOKENS` | `2048` | OpenRouter bills against the requested cap, so a small balance needs a small value. |
| `CAD_MCP_LLM_ACCEPTANCE` | unset | Set to `1` to run the paid SPEC 9.1 loop. |

Run the bracket loop by hand and print a tool-call transcript:

```bash
uv run python tests/llm_harness.py
```

Pointing `OPENROUTER_MODEL` at a weak model will fail the acceptance test
for model-capability reasons rather than server reasons — the transcript
in the failure output distinguishes the two.

## Security model

Model code is executed, so it is worth being precise about what the
sandbox does and does not promise.

**Hard guarantees (enforced by the OS):** wall-clock timeout, memory cap,
file-size cap, process-count cap, and a process-tree kill on timeout.
These hold regardless of what the executed code does.

**Defence in depth (enforced in-process):** filesystem writes are
confined to the session temp directory and reads to that directory plus
the Python installation; networking is unavailable (including the
`_socket` accelerator); process creation is blocked; and the import
policy is enforced on both `__import__` and `sys.meta_path`, so
`importlib` cannot route around it. Exports are additionally validated:
`filename` must be a single safe component, and the resolved path is
re-checked against the output directory.

**What this is not.** The in-process layer shares an interpreter with the
code it constrains, so it stops accidents and casual misuse — not a
determined attacker. **Do not point this server at untrusted prompts
without OS-level isolation** (a container, seccomp/bwrap, or a Windows
restricted token) around the whole process.

Tunables: `CAD_MCP_SANDBOX_TIMEOUT_S` (30), `CAD_MCP_SANDBOX_MEM_MB`
(2048), `CAD_MCP_SANDBOX_FILE_MB` (512), `CAD_MCP_SANDBOX_MAX_PROCS` (64).

Over HTTP: set `CAD_MCP_AUTH_TOKEN` for bearer auth (compared in constant
time) and `CAD_MCP_ALLOWED_HOSTS` for DNS-rebinding protection. A
non-loopback bind without both logs a warning at startup; bearer auth
over plain HTTP is for localhost or a trusted network only — put a TLS
terminator in front of anything else.

## Where exports go

`export_model` writes to `CAD_MCP_OUTPUT_DIR` (default
`./cad-mcp-output/<session-id>/`). This is deliberately **not** the
session temp directory: `reset_session` clears modelling state but keeps
exported files, and tells you where they are.

## Troubleshooting

### CadQuery / OCP installation

**pip/uv install fails with "no matching distribution"**

CadQuery requires `cadquery-ocp`, which ships pre-built wheels for
Python 3.10-3.13 on Linux x86_64, macOS x86_64/arm64, and Windows x86_64.
If your Python version or platform isn't covered:

```bash
# Check your Python version
python --version

# Use a supported version via uv
uv python install 3.11
uv sync
```

**macOS arm64 (Apple Silicon) — OCP build errors**

cadquery-ocp wheels are available for arm64 since cadquery 2.4. If you
hit issues:

```bash
# Ensure you're not running under Rosetta
arch  # should print "arm64"

# Use a clean venv
uv sync --reinstall
```

If cadquery-ocp still fails to install, install cadquery via conda-forge
which has verified arm64 builds:

```bash
conda install -c conda-forge cadquery
```

**Segfault on exit (exit code 139/127)**

OCP (OpenCascade) sometimes segfaults during Python interpreter
shutdown. This is cosmetic — all output is correct before the crash.
The segfault happens after all work is done and does not affect results
or data integrity. It can be safely ignored in CI by checking the
process output rather than the exit code.

### Rendering

**pyrender / EGL not available**

The server automatically falls back to matplotlib 3D rendering when
pyrender + EGL is unavailable (common on Windows, CI, and headless
servers without GPU). The fallback produces correct multi-view images
with axes and scale ticks.

To enable the faster pyrender path on Linux:

```bash
# Install EGL libraries (Ubuntu/Debian)
sudo apt-get install libegl1-mesa-dev libgles2-mesa-dev

# Install pyrender
uv add pyrender

# Verify EGL works
uv run python -c "
import os; os.environ['PYOPENGL_PLATFORM'] = 'egl'
import pyrender
r = pyrender.OffscreenRenderer(64, 64)
r.delete()
print('EGL works')
"
```

On headless servers without a GPU, install Mesa's software renderer:

```bash
# Ubuntu/Debian
sudo apt-get install libosmesa6-dev

# Use osmesa instead of EGL
export PYOPENGL_PLATFORM=osmesa
```

**Render takes > 2 seconds**

The first render in a process pays a one-time matplotlib font cache
initialization cost (~1-3s). Subsequent renders should be well under 2s.
If renders are consistently slow, check that you're not re-initializing
matplotlib on each call.

### Validation

**trimesh warns about rtree**

The wall-thickness check uses scipy KDTree (not trimesh's ray casting),
so rtree is not required. The warning can be safely ignored.

**manifold3d API errors**

manifold3d v3.x changed its API. This project requires manifold3d
>= 3.5.2. The correct construction is:

```python
import manifold3d
mesh = manifold3d.Mesh(vert_properties=verts_f32, tri_verts=faces_u32)
m = manifold3d.Manifold(mesh)
print(m.status())  # Error.NoError for valid manifold
```

### Common CI issues

**Tests pass but exit code is non-zero (139)**

This is the OCP shutdown segfault. In CI, either:
- Ignore the exit code and check pytest's output for "passed"
- Or run with `|| true` and parse the output:

```bash
uv run pytest -v 2>&1 | tee test_output.txt; grep -q "passed" test_output.txt
```
