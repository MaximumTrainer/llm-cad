# cad-mcp — Text-to-3D MCP Server

## What this project is
An MCP server that lets any LLM design 3D models from text descriptions. The LLM writes parametric CadQuery (Python) code; this server executes it in a sandbox, renders multi-angle previews back to the LLM as images, validates printability, and exports STL/STEP/GLB/3MF. See SPEC.md for full requirements and PLAN.md for the phased build plan.

## Core loop (never break this)
describe → LLM writes CadQuery code → `execute_cad` → `render_views` returns PNGs → LLM critiques its own render → revise → `validate_mesh` → `export_model`

The visual feedback loop is the product. Any change that makes renders slower than ~2s or breaks image return via MCP content blocks is a regression.

## Stack
- Python 3.11+, `uv` for dependency management
- MCP: official `mcp` Python SDK, `FastMCP` server, stdio transport (add streamable HTTP later)
- Geometry: `cadquery` (B-rep via OCP). Do NOT swap to trimesh/numpy-stl for modeling — they're mesh-only, used solely in validation.
- Rendering: headless — export to mesh, render with `pyrender` + `EGL` offscreen (fallback: matplotlib 3D for CI). No GUI dependencies ever.
- Validation: `trimesh` for watertightness, `manifold3d` for manifold checks
- Tests: `pytest`, golden-file tests compare exported STEP/STL hashes and rendered image perceptual hashes

## Conventions
- One tool per file under `src/cad_mcp/tools/`, registered in `server.py`
- All user code execution goes through `sandbox.py` (subprocess, 30s timeout, no network, temp cwd). Never `exec()` in the server process.
- Session state (current Workplane object + history) lives in `session.py`, keyed by MCP session. Model state persists across tool calls within a session.
- Tool results: return errors as structured text the LLM can act on (exception type, line number, offending snippet) — never bare tracebacks, never silent failures.
- Images return as MCP `ImageContent` (base64 PNG), 800×600 max, 4-view grid by default.
- Type hints everywhere; `ruff` + `mypy --strict` must pass before any commit.

## Commands
- `uv run pytest` — full test suite
- `uv run cad-mcp` — start server on stdio
- `uv run python scripts/smoke.py` — end-to-end: builds a bracket, renders, validates, exports

## What NOT to do
- Don't add tools beyond SPEC.md without updating SPEC.md first
- Don't return meshes/geometry blobs to the LLM — it can't read them; return images and measurements
- Don't let render or export write outside the session's temp directory
- Don't upgrade cadquery/OCP pins casually — rendering and export are version-sensitive; run golden tests after any bump
