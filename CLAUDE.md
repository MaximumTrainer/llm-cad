# cad-mcp — Text-to-3D MCP Server

## What this project is
An MCP server that lets any LLM design 3D models from text descriptions. The LLM writes parametric CadQuery (Python) code; this server executes it in a sandbox, renders multi-angle previews back to the LLM as images, validates printability, and exports STL/STEP/GLB/3MF. See SPEC.md for full requirements and PLAN.md for the phased build plan.

## Core loop (never break this)
describe → LLM writes CadQuery code → `execute_cad` → `render_views` returns PNGs → LLM critiques its own render → revise → `validate_mesh` → `export_model`

The visual feedback loop is the product. Any change that makes renders slower than ~2s or breaks image return via MCP content blocks is a regression.

## Stack
- Python 3.11+, `uv` for dependency management
- MCP: official `mcp` Python SDK v2, `MCPServer` (was `FastMCP` in v1), stdio + streamable HTTP transports (SPEC 10.1)
- Geometry: `cadquery` (B-rep via OCP). Do NOT swap to trimesh/numpy-stl for modeling — they're mesh-only, used solely in validation.
- Rendering: headless — export to mesh, render with `pyrender` + `EGL` offscreen (fallback: matplotlib 3D for CI). No GUI dependencies ever.
- Validation: `trimesh` for watertightness, `manifold3d` for manifold checks
- AI mesh: `httpx` async client for Meshy API (SPEC 10.2); `MESHY_API_KEY` env var required
- Tests: `pytest`, golden-file tests compare exported STEP/STL hashes and rendered image perceptual hashes

## Conventions
- One tool per file under `src/cad_mcp/tools/`, registered in `server.py`
- All user code execution goes through `sandbox.py` (subprocess, 30s timeout, no network, temp cwd). Never `exec()` in the server process.
- Session state (parts with code history + BREP) lives in `session.py`, keyed by MCP session. Multi-part assembly support per SPEC 10.3.
- Tools resolve their session with `session.for_context(ctx)` and take `ctx: Context | None = None` as the last parameter. **Never** call `session.get_or_create()` with no argument — that shared one global session across every HTTP client.
- Mutating tools take `with sess.lock:` — tools are dispatched on a thread pool.
- Every file write goes through `cad_mcp.paths.safe_output_path` / `unique_output_path`.
- Tool results: return errors as structured text the LLM can act on (exception type, line number, offending snippet) — never bare tracebacks, never silent failures.
- Images return as MCP `ImageContent` (base64 PNG), 800×600 max, 4-view grid by default.
- Type hints everywhere; `ruff` + `mypy --strict` must pass before any commit.

## Skills (use these, don't work from memory)
`.claude/skills/` — hooks enforce mechanically, skills guide judgement:
- `add-tool` — adding/renaming a tool: SPEC check → file → registration → envelope → README row → prompt coverage → tests
- `spec-guard` — before changing tool surface, session state, transport, sandbox guarantees, determinism or latency
- `sandbox-audit` — any change to `sandbox.py` / `_sandbox_worker.py` / `_sandbox_policy.py`
- `render-check` — any change to `render.py` or `render_views`
- `pin-bump` — changing a cadquery/OCP/trimesh/manifold3d/mcp pin
- `release-check` — before tagging

## Commands
- `uv run pytest` — full test suite
- `uv run cad-mcp` — start server on stdio
- `uv run cad-mcp --transport http` — start server on streamable HTTP (port 8000)
- `uv run python scripts/smoke.py` — end-to-end: builds a bracket, renders, validates, exports
- `uv run pytest -m llm -v` — live LLM tests via OpenRouter (needs `OPENROUTER_API_KEY`)
- `./scripts/install-hooks.sh` (or `scripts/install-hooks.ps1`) — install git hooks

## What NOT to do
- Don't add tools beyond SPEC.md without updating SPEC.md first
- Don't return meshes/geometry blobs to the LLM — it can't read them; return images and measurements
- Don't let render or export write outside the session's temp or output directory — use `cad_mcp.paths`
- Don't assert security behaviour by error wording; assert the observable effect (no file, no connection, no process)
- Don't upgrade cadquery/OCP pins casually — rendering and export are version-sensitive; run golden tests after any bump
- Don't attempt parametric ops (fillet, shell) on AI-generated meshes — they're tessellated B-rep, not NURBS
- Don't make live Meshy API calls in unit tests — mock httpx; gate integration tests on `MESHY_API_KEY`

## Shared agent skills

Shared skills live in [MaximumTrainer/agent-skills](https://github.com/MaximumTrainer/agent-skills). Before writing a new
skill, runbook or repeated procedure, check the catalogue - and send genuinely
general improvements back so the other repositories get them too.

```bash
python3 .claude/skills/skill-exchange/scripts/skills.py list
python3 .claude/skills/skill-exchange/scripts/skills.py status
```

See `.claude/skills/skill-exchange/` for the workflow.
