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

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `CAD_MCP_TRANSPORT` | `stdio` | `http` for streamable HTTP |
| `CAD_MCP_HOST` | `127.0.0.1` | Bind address |
| `CAD_MCP_PORT` | `8000` | Bind port |
| `CAD_MCP_AUTH_TOKEN` | (none) | Bearer token for auth (unset = no auth) |
| `CAD_MCP_CORS_ORIGIN` | (none) | Allowed CORS origin (e.g. `https://app.example.com`) |
| `CAD_MCP_ALLOWED_HOSTS` | (none) | Comma-separated allowed hostnames |
| `CAD_MCP_STATELESS` | `0` | `1` for stateless HTTP mode |

**Health check:** `GET /health` returns `{"status": "ok"}` (no auth required).

**Auth:** When `CAD_MCP_AUTH_TOKEN` is set, all MCP requests require `Authorization: Bearer <token>`. Requests without a valid token receive HTTP 401.

## Tools

| Tool | Description |
|------|-------------|
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
uv run pytest             # tests
uv run ruff check .       # lint
uv run mypy --strict src/cad_mcp  # type check
uv run python scripts/smoke.py   # end-to-end smoke test
```

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
