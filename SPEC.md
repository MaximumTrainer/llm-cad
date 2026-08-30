# SPEC — cad-mcp: Text-to-3D MCP Server

## 1. Problem statement
LLMs are bad at emitting mesh data directly but excellent at writing code. This server gives any MCP-capable LLM host (Claude, Claude Code, Cursor, LM Studio, custom agents) the ability to design precise, editable 3D models from text by writing parametric CadQuery code, seeing rendered previews of the result, and iterating until correct.

## 2. Goals
- G1: Work with **any** MCP host over stdio; no host-specific assumptions.
- G2: Precise parametric modeling — "make the hole 2mm wider" must be a code edit, not a regeneration.
- G3: Closed visual feedback loop — the LLM must be able to see what it built.
- G4: Print-ready and CAD-ready output: STL, 3MF, STEP, GLB.
- G5: Safe execution of model code (sandboxed, time-limited, no network/filesystem escape).
- G6: Teach the host — ship MCP prompts so an LLM that has never seen CadQuery can still succeed.

## 3. Non-goals (v1)
- No GUI/viewer app (hosts render the returned images).
- No multi-user server deployment (single-user stdio; HTTP transport is v2).
- No assemblies/constraints solver (single-part focus; boolean ops OK).
- No AI mesh generation in v1 (`gen_ai_mesh` is a v2 optional tool behind an API key).

## 4. Users and hosts
- Primary: a developer connecting the server to Claude Code / Claude Desktop / any MCP client config.
- The "user" of the tools is the LLM itself; tool descriptions and error messages are written for an LLM audience: actionable, structured, example-bearing.

## 5. Functional requirements

### 5.1 Tools
| Tool | Input | Output | Notes |
|---|---|---|---|
| `execute_cad` | `code: str` (Python/CadQuery), `mode: "replace"\|"append"` | success + object summary (solids count, bbox) or structured error | Code must assign final shape to variable `result`. Append mode re-runs history + new code. |
| `render_views` | `views: list` (default front/right/top/iso), `width`, `height` | MCP ImageContent — one grid PNG | ≤2s target. Orthographic + one perspective iso. Include axes + mm scale ticks. |
| `validate_mesh` | `min_wall_mm: float = 1.2`, `max_overhang_deg: float = 45` | report: watertight, manifold, wall-thickness violations, overhang regions, est. volume/mass (PLA) | Runs on tessellated mesh via trimesh/manifold3d. |
| `measure` | `what: "bbox"\|"volume"\|"faces"\|"distance"`, optional selectors | numeric results in mm/mm³ | Lets the LLM verify stated dimensions actually happened. |
| `export_model` | `format: "stl"\|"step"\|"3mf"\|"glb"`, `filename` | file path + size; file written to session output dir | STEP from B-rep, others from tessellation with configurable tolerance. |
| `list_session` | — | code history, current bbox, exports so far | Recovery after context loss. |
| `reset_session` | — | confirmation | Clears state. |

### 5.2 MCP prompts (shipped with server)
- `design_workflow`: teaches the loop (write → render → critique → revise → validate → export) and mandates rendering after every execute.
- `cadquery_primer`: ~150-line cheat sheet of idioms (Workplane, extrude, fillet, shell, holes, patterns, booleans) with 5 worked examples.
- `printability_checklist`: wall thickness, overhangs, bed adhesion, tolerance for mating parts.

### 5.3 MCP resources
- `cad://session/current/code` — full current model code.
- `cad://examples/{name}` — 8–10 curated example models (bracket, enclosure, gear, threaded cap...).

## 6. Non-functional requirements
- N1 Sandbox: model code runs in a subprocess with 30s CPU timeout, memory cap (e.g. 2GB via resource limits), cwd = per-session temp dir, no network (block sockets), import allowlist (cadquery, math, numpy).
- N2 Latency: execute ≤5s typical, render ≤2s, validate ≤5s for meshes under 500k tris.
- N3 Errors: every failure returns (a) what failed, (b) where (line number in the LLM's code), (c) a hint. Example: `KernelError on line 12: fillet radius 5 exceeds edge length 3.2 — reduce radius or pick fewer edges`.
- N4 Determinism: same code → identical STEP topology and byte-stable STL (fixed tessellation seed/tolerances).
- N5 Portability: Linux + macOS; headless EGL rendering, matplotlib fallback where EGL unavailable.
- N6 Observability: structured logs per tool call (duration, success, session id) to stderr; never stdout (stdio transport owns stdout).

## 7. Architecture
```
Host LLM ── MCP (stdio) ── server.py (FastMCP)
                              │
              ┌───────────────┼───────────────────┐
        tools/*.py       session.py          prompts/, resources/
              │          (state: code history,
        sandbox.py        current shape, tmpdir)
        (subprocess:
         cadquery exec)
              │
        render.py (tessellate → pyrender/EGL → PNG grid)
        validate.py (trimesh + manifold3d)
        export.py (STEP via OCP, STL/3MF/GLB via tessellation)
```
Session state: the B-rep object cannot cross the subprocess boundary cheaply, so the subprocess serializes the shape to a BREP file in the session tmpdir; server-side tools reload it. Code history is the source of truth; the BREP file is a cache.

## 8. Key decisions and rationale
- CadQuery over OpenSCAD: real B-rep kernel (OCCT) → STEP export, fillets/shells that OpenSCAD can't do, Python (better LLM fluency), selectors enable "the top face" style edits.
- Images over geometry as feedback: LLMs read images natively; a 4-view grid with axes/scale is the highest-bandwidth feedback per token.
- Code history as state: replayable, diffable, survives crashes, and makes `append` mode trivial.

## 9. Acceptance criteria (v1 done =)
1. From a cold start in Claude Code with only this server connected, the prompt "design a wall-mount bracket for a 30mm pipe, two M4 screw holes, 3mm walls" produces a validated, watertight STL within ≤4 LLM iterations, no human code edits.
2. `measure` confirms requested dimensions within 0.1mm.
3. Malicious code test suite (network attempt, file escape, fork bomb, infinite loop) — all contained.
4. Fresh-machine setup (README steps) to first render in under 10 minutes.

## 10. v2 backlog
Streamable HTTP transport + auth · `gen_ai_mesh` (Meshy/Tripo) for organics · assembly support · parametric "tweak sliders" resource · STEP import + modify · multi-material 3MF.
