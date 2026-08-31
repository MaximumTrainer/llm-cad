# PLAN — Implementation guide (drive this with Claude Code)

How to use: work one phase per Claude Code session. Paste the phase prompt, let it build, run the gate checks, commit, move on. CLAUDE.md keeps context between sessions; SPEC.md is the contract. If Claude Code proposes deviating from SPEC.md, make it update SPEC.md in the same PR.

## Phase 0 — Scaffold (30 min)
**Prompt:**
> Read CLAUDE.md and SPEC.md. Scaffold the project: uv-managed Python 3.11 package `cad_mcp` with src layout, FastMCP server entrypoint exposing one `ping` tool, pytest with one test, ruff + mypy strict configs, and a README with MCP client config snippets for Claude Code and Claude Desktop. Don't implement any real tools yet.

**Gate:** `uv run pytest` green; server connects from an MCP client and `ping` responds; `mypy --strict` passes.

## Phase 1 — Sandboxed execution + session state (core, ~half day)
**Prompt:**
> Implement `sandbox.py` and the `execute_cad` tool per SPEC 5.1 and N1. Subprocess runner: 30s timeout, 2GB memory limit, per-session tmpdir cwd, socket blocking, import allowlist. The subprocess runs the accumulated code history, requires a `result` variable holding a cadquery shape, serializes it to `current.brep` in the tmpdir, and returns a JSON summary (solid count, bbox) on stdout. Implement `session.py` (history, tmpdir lifecycle), `list_session`, `reset_session`. Errors must follow SPEC N3 format with the line number mapped to the user's code, not the wrapper. Write tests including the malicious-code suite from SPEC 9.3.

**Gate:** bracket example executes; infinite loop killed at 30s; network attempt blocked; error for a bad fillet includes line number and hint.

## Phase 2 — Rendering (the differentiator, ~half day)
**Prompt:**
> Implement `render.py` and the `render_views` tool. Load `current.brep`, tessellate with fixed tolerances, render an 800×600 grid of front/right/top/isometric views using pyrender with EGL offscreen; matplotlib 3D fallback selected automatically when EGL is unavailable. Draw axis triad and mm scale ticks. Return as MCP ImageContent base64 PNG. Add a golden-image perceptual-hash test.

**Gate:** render of the example bracket under 2s; image visually shows scale ticks; works with EGL absent (fallback path tested in CI).

## Phase 3 — Validate, measure, export (~half day)
**Prompt:**
> Implement `validate_mesh`, `measure`, and `export_model` per SPEC 5.1. Validation uses trimesh + manifold3d: watertight, manifold, min-wall sampling, overhang angle report, volume and PLA mass estimate. Export: STEP from the B-rep via OCP; STL/3MF/GLB from tessellation with a `tolerance` parameter; deterministic output per SPEC N4 with hash-based golden tests. Files go to the session output dir; return path + size.

**Gate:** intentionally-broken open mesh flagged non-watertight; 0.8mm wall flagged under default 1.2mm; STEP reimports in FreeCAD; STL hash stable across two runs.

## Phase 4 — Teach the LLM: prompts + resources (~2 hrs)
**Prompt:**
> Add MCP prompts `design_workflow`, `cadquery_primer`, `printability_checklist` and resources `cad://session/current/code` plus 8 curated examples per SPEC 5.2–5.3. The primer must cover Workplane basics, extrude/cut, fillets with failure modes, shell, hole patterns via polarArray, and booleans — each with a runnable snippet. The workflow prompt must mandate: render after every execute, critique the render explicitly, verify dimensions with measure before export.

**Gate:** in a fresh Claude Code session with only the server connected, invoking the workflow prompt then asking for the SPEC 9.1 bracket succeeds within 4 iterations.

## Phase 5 — End-to-end hardening (~half day)
**Prompt:**
> Write `scripts/smoke.py` driving the full loop programmatically. Add structured stderr logging per SPEC N6. Then run the acceptance suite: SPEC 9.1–9.4. Fix whatever fails. Update README with troubleshooting (EGL setup on Linux, OCP install issues on macOS arm64).

**Gate:** all four SPEC acceptance criteria pass; smoke script green in CI.

## Phase 6a — Streamable HTTP transport with auth (SPEC 10.1, ~half day)
**Prompt:**
> Implement streamable HTTP transport per SPEC 10.1. Add `--transport http` CLI flag and env-var config (`CAD_MCP_HOST`, `CAD_MCP_PORT`, `CAD_MCP_AUTH_TOKEN`). Bearer token auth via SDK `TokenVerifier`. Health endpoint at `GET /health`. CORS support via `CAD_MCP_CORS_ORIGIN`. Stdio remains the default. Add `transport.py` for config; update `server.py` main(). Tests: server starts on HTTP, tool call succeeds over HTTP, 401 without token when token is set, health endpoint responds without auth.

**Gate:** `curl http://localhost:8000/health` returns 200; MCP client connects over HTTP and calls `ping`; unauthorized request returns 401; stdio mode unaffected.

## Phase 6b — `gen_ai_mesh` tool (SPEC 10.2, ~half day)
**Prompt:**
> Implement `gen_ai_mesh` per SPEC 10.2. Add `meshy.py` (async httpx client: create task, poll, download GLB) and `tools/gen_ai_mesh.py`. Preview mode with 3s poll / 120s timeout. Optional refine mode (180s timeout). Download GLB to session tmpdir, convert mesh to OCP compound shape, store as session BREP. Return JSON summary + thumbnail. When `MESHY_API_KEY` is unset, return structured missing-key error. Add `httpx>=0.28` to deps. Mock tests for poll loop, timeout, error paths. Live integration test gated on `MESHY_API_KEY` env var.

**Gate:** with live key, `gen_ai_mesh(prompt="a simple chess pawn")` produces a shape that `render_views` renders and `export_model` exports; without key, returns missing-key error; mock tests cover poll loop, 429 handling, and timeout.

## Phase 6c — Assembly support (SPEC 10.3, ~1 day)
**Prompt:**
> Implement assembly support per SPEC 10.3. Update `session.py` with `Part` dataclass (name, code_history, brep_path, color, position) and assembly state. Add tools: `create_part`, `set_active_part`, `position_part`, `list_parts`, `delete_part`. Modify `execute_cad` to target active part, `render_views` to compose all parts with distinct colors, `validate_mesh` to add interference check, `measure` to add `clearance` mode, `export_model` to produce per-part + combined files (XCAF for STEP). Backward compatible: sessions without `create_part` behave as v1 (implicit "main" part). Max 16 parts.

**Gate:** create box + lid, position lid above, render shows both colored, export produces 3 STLs, validate flags interference when overlapping, clearance returns 0 when touching and >0 when separated; all v1 tests still pass.

## Risk register
- **OCP/cadquery install pain** (heaviest dependency, platform-sensitive): pin exact versions in Phase 0; document conda fallback. Mitigate first — it's the most likely Day-1 blocker.
- **EGL on headless Linux**: keep the matplotlib fallback honest — test it in CI where EGL is absent.
- **LLM writes valid-but-wrong geometry**: this is why render + measure exist; the workflow prompt must force self-critique, not optional.
- **stdout corruption**: any stray `print` in server code breaks stdio MCP framing. Lint rule: no print outside the sandbox subprocess.
- **Meshy API availability/cost** (v2): API calls cost credits; generation takes 15-120s. Mock everything in unit tests; gate live integration tests on `MESHY_API_KEY` env var. Handle 429 gracefully.
- **Mesh-to-BREP fidelity** (v2): converting a triangle mesh to an OCP compound produces a tessellated B-rep, not NURBS. Fillets/shells on imported meshes will fail. Document this limitation; the workflow prompt should warn the LLM not to attempt parametric ops on AI-generated meshes.
- **Assembly complexity** (v2): 16 parts x render/validate = multiplicative cost. Cap part count and warn the LLM when approaching the limit.

## Connecting the finished server
Claude Code (project scope): `claude mcp add cad-mcp -- uv run cad-mcp` from the repo, or add to `.mcp.json`. For other hosts, use the equivalent stdio command config. Current syntax: https://docs.claude.com/en/docs/claude-code/mcp
