# SPEC — cad-mcp: Text-to-3D MCP Server

## 1. Problem statement
LLMs are bad at emitting mesh data directly but excellent at writing code. This server gives any MCP-capable LLM host (Claude, Claude Code, Cursor, LM Studio, custom agents) the ability to design precise, editable 3D models from text by writing parametric CadQuery code, seeing rendered previews of the result, and iterating until correct.

## 2. Goals
- G1: Work with **any** MCP host over stdio; no host-specific assumptions.
- G2: Precise parametric modeling — "make the hole 2mm wider" must be a code edit, not a regeneration.
- G3: Closed visual feedback loop — the LLM must be able to see what it built.
- G4: Print-ready and CAD-ready output: STL, 3MF, STEP, GLB.
- G5: Contained execution of model code — time-limited, memory-limited, no network, filesystem confined to the session directory. See N1 for the precise threat model; this is not a hostile-code jail.
- G6: Teach the host — ship MCP prompts so an LLM that has never seen CadQuery can still succeed.

## 3. Non-goals (v1)
- No GUI/viewer app (hosts render the returned images).
- No multi-user server deployment (single-user stdio; HTTP transport added in v2, see 10.1).
- No constraints solver (assembly positioning is manual via `position_part`; see 10.3).
- No AI mesh generation in v1 (`gen_ai_mesh` added in v2, see 10.2).

## 4. Users and hosts
- Primary: a developer connecting the server to Claude Code / Claude Desktop / any MCP client config.
- The "user" of the tools is the LLM itself; tool descriptions and error messages are written for an LLM audience: actionable, structured, example-bearing.

## 5. Functional requirements

### 5.1 Tools
| Tool | Input | Output | Notes |
|---|---|---|---|
| `ping` | — | `"pong"` | Connectivity check; lets a host verify the server is reachable before doing work. |
| `execute_cad` | `code: str` (Python/CadQuery), `mode: "replace"\|"append"` | success + object summary (solids count, bbox) or structured error | Code must assign final shape to variable `result`. Append mode re-runs history + new code. |
| `render_views` | `views: list` (default front/right/top/iso), `width`, `height` | MCP ImageContent — one grid PNG | ≤2s target. Orthographic + one perspective iso. Include axes + mm scale ticks. |
| `validate_mesh` | `min_wall_mm: float = 1.2`, `max_overhang_deg: float = 45` | report: watertight, manifold, wall-thickness violations, overhang regions, est. volume/mass (PLA) | Runs on tessellated mesh via trimesh/manifold3d. |
| `measure` | `what: "bbox"\|"volume"\|"faces"\|"distance"`, optional selectors | numeric results in mm/mm³ | Lets the LLM verify stated dimensions actually happened. |
| `export_model` | `format: "stl"\|"step"\|"3mf"\|"glb"`, `filename` | file path + size; file written to the durable output dir (`CAD_MCP_OUTPUT_DIR`, default `./cad-mcp-output/<session>/`), **not** the session temp dir, so exports survive `reset_session`. `filename` must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` | STEP from B-rep, others from tessellation with configurable tolerance. |
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
- N1 Sandbox: model code runs in a subprocess under two layers of containment.
  - **OS-enforced (hard guarantees).** Wall-clock timeout (30s default, `CAD_MCP_SANDBOX_TIMEOUT_S`); address-space/memory cap (2GB default, `CAD_MCP_SANDBOX_MEM_MB`) via `RLIMIT_AS`/`RLIMIT_DATA` on Unix and a Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` on Windows; file-size cap (`RLIMIT_FSIZE`); process-count cap (`RLIMIT_NPROC` / `ActiveProcessLimit`); and a process-group/job kill on timeout so nothing is orphaned.
  - **In-process (defence in depth).** Filesystem writes confined to the session temp dir and reads to that dir plus the Python installation; all socket implementations neutralised including the `_socket` C accelerator; process creation blocked (`fork`, `spawn*`, `exec*`, `system`, `popen`); and an import policy enforced both by a `builtins.__import__` hook and a `sys.meta_path` finder, so `importlib.import_module` cannot route around it.
  - **Threat model.** The in-process layer runs in the same interpreter as user code and is therefore a barrier against accidents and casual misuse, **not** a jail for deliberately hostile code. Do not expose this server to untrusted prompts without OS-level isolation (a container, seccomp/bwrap, or a Windows restricted token) around the whole process.
- N2 Latency: execute ≤5s typical, render ≤2s, validate ≤5s for meshes under 500k tris.
- N3 Errors: every failure returns (a) what failed, (b) where (line number in the LLM's code), (c) a hint. Example: `KernelError on line 12: fillet radius 5 exceeds edge length 3.2 — reduce radius or pick fewer edges`.
- N4 Determinism: the same code produces **byte-identical exports in all four formats** (STL, STEP, 3MF, GLB) across separate processes on a given platform, at a fixed tessellation tolerance. Two writers embed *when* and *where* rather than *what*, and both are pinned rather than excluded:
  - STEP records a wall-clock timestamp in its `FILE_NAME` header entity. It is fixed to `1970-01-01T00:00:00`; on a 50×30×10 box that timestamp was the only difference between two runs.
  - 3MF mandates a `p:UUID` on objects, items and the build element, and trimesh generates each with `uuid.uuid4`. They are rewritten as UUID5s derived from first-encounter order, which keeps them unique within the document and preserves cross-references. The container is repacked with fixed member timestamps and a fixed `create_system`, without which the same document packs differently on Windows and Linux.
  - STL and GLB are byte-stable as written; all mesh exporters apply the same normal handling (`fix_normals`).
  - Verified by `scripts/determinism_check.py`, which exports twice in **separate processes** — two calls in one interpreter cannot see a difference that is seeded once at import. This is a blocking CI gate on Linux, macOS and Windows.
  - **Cross-platform byte equality is not yet guaranteed.** The container and header nondeterminism above is fully pinned, so the remaining variable is tessellation arithmetic: OCCT discretises curves through libm, and `macos-latest` is arm64 while the other runners are x86-64. A CI job compares hashes between platforms and reports differences, but is informational until the matrix has been observed agreeing. Topology and dimensions are platform-independent regardless; it is the last-ulp vertex coordinates that are in question.
- N5 Portability: Linux + macOS; headless EGL rendering, matplotlib fallback where EGL unavailable.
- N6 Observability: structured logs per tool call (duration, success, session id) to stderr; never stdout (stdio transport owns stdout).

## 7. Architecture
```
Host LLM ── MCP (stdio | streamable HTTP) ── server.py (MCPServer)
                                                │
                    ┌───────────────────────────┼──────────────────────┐
              tools/*.py                   session.py            prompts/, resources/
                    │                (state: parts[], active_part,
                    │                 code history, tmpdir)
                    │
                    ├── sandbox.py ──────► _sandbox_worker.py   [subprocess]
                    │   untrusted user      single-use, confined, rlimited,
                    │   code                30s; writes a .brep
                    │
                    ├── geometry.py ─────► _geometry_worker.py  [subprocess]
                    │   OUR code, but       long-lived, reused, restarted on
                    │   OCP is native       death. The ONLY place OCP runs:
                    │                       tessellate, measure, STEP/XCAF
                    │                       export, boolean interference,
                    │                       mesh sewing (_geometry_ops.py)
                    │
                    └── in-process, no native kernel:
                        render.py    (mesh → matplotlib/pyrender → PNG grid)
                        validate.py  (trimesh + manifold3d)
                        export.py    (STL/3MF/GLB from a tessellated mesh)
                        meshy.py     (httpx → Meshy API, v2 only)
```
**Why two subprocesses and not one.** They isolate different things.
`sandbox.py` isolates code we do not trust, so its worker is single-use,
confined to the session directory and rlimited — reusing it would leak
one execution's namespace into the next. `geometry.py` isolates code we
do trust from a kernel that cannot raise: OCP binds OCCT, and a
degenerate boolean, a malformed shape or a teardown bug is a segfault,
which no `except` will catch. Reuse is therefore correct *and* necessary
there, since a fresh interpreter per call would buy no isolation and cost
the ~3.3s CadQuery import every time. When that process dies the client
gets a `GeometryKernelError` with a hint (N3) and the next call gets a
new kernel; the server never goes down with it. Tessellations are cached
on (path, mtime, size, tolerances), so one shape is meshed once no matter
how many of render, validate and export ask for it (N2).
Session state: the B-rep object cannot cross the subprocess boundary cheaply, so the subprocess serializes the shape to a BREP file in the session tmpdir; server-side tools reload it. Code history is the source of truth; the BREP file is a cache. In v2, each session holds an assembly of named parts, each with its own BREP and code history.

## 8. Key decisions and rationale
- CadQuery over OpenSCAD: real B-rep kernel (OCCT) → STEP export, fillets/shells that OpenSCAD can't do, Python (better LLM fluency), selectors enable "the top face" style edits.
- Images over geometry as feedback: LLMs read images natively; a 4-view grid with axes/scale is the highest-bandwidth feedback per token.
- Code history as state: replayable, diffable, survives crashes, and makes `append` mode trivial.

## 9. Acceptance criteria (v1 done =)
1. From a cold start in Claude Code with only this server connected, the prompt "design a wall-mount bracket for a 30mm pipe, two M4 screw holes, 3mm walls" produces a validated, watertight STL within ≤4 LLM iterations, no human code edits.
2. `measure` confirms requested dimensions within 0.1mm.
3. Containment suite — every vector asserted by **observable effect**, not by error wording: file write outside the session dir (no file appears), file read outside it, `socket`/`_socket` connection attempt (a real local listener accepts nothing), `subprocess` and `importlib` import bypass, `os.system`, `os.fork`, infinite loop (killed by timeout, no surviving process), and memory exhaustion (killed by the memory cap, reported as `MemoryError`). Each payload assigns a valid `result`, so a successful escape would be reported as `ok=true` and fail the test. Scope is bounded by the N1 threat model above.
4. Fresh-machine setup (README steps) to first render in under 10 minutes.

## 10. v2 features

### 10.1 Streamable HTTP transport with auth

**Motivation:** stdio works for local MCP hosts but cannot serve remote clients, web UIs, or multi-tenant deployments. Streamable HTTP (MCP SDK v2) adds a stateful HTTP endpoint that any network-reachable client can connect to.

**Transport selection.** The server picks its transport at startup via CLI flag or env var. Only one transport is active per process.

| Startup | Transport |
|---|---|
| `uv run cad-mcp` (default) | stdio |
| `uv run cad-mcp --transport http` | streamable HTTP |
| `CAD_MCP_TRANSPORT=http uv run cad-mcp` | streamable HTTP |

**HTTP server defaults.**

| Setting | Default | Env override |
|---|---|---|
| Host | `127.0.0.1` | `CAD_MCP_HOST` |
| Port | `8000` | `CAD_MCP_PORT` |
| MCP path | `/mcp` | — |
| Stateless mode | off | `CAD_MCP_STATELESS=1` |

**Authentication.** When `CAD_MCP_AUTH_TOKEN` is set, the server requires `Authorization: Bearer <token>` on every request. Implementation uses the SDK's `TokenVerifier` protocol with a simple symmetric-token verifier (compare against env var). Requests without a valid token receive HTTP 401. When the env var is unset, the server runs without auth (local development).

Requirements:
- H1: `mcp.run(transport="streamable-http", host=host, port=port)` with kwargs from env.
- H2: Bearer token auth via a custom `TokenVerifier` that validates against `CAD_MCP_AUTH_TOKEN`. Return `AccessToken(token=t, client_id="bearer", scopes=["cad"])`.
- H3: Stdio remains the default; no behavior change when `--transport` is absent.
- H4: Session isolation: each HTTP MCP session gets its own `Session` (tmpdir, code history). Session cleanup on disconnect via `session_idle_timeout` (default 300s).
- H5: CORS headers when `CAD_MCP_CORS_ORIGIN` is set (for browser-based MCP clients). Expose `Mcp-Session-Id` header.
- H6: `TransportSecuritySettings(allowed_hosts=...)` derived from `CAD_MCP_HOST` and `CAD_MCP_ALLOWED_HOSTS` (comma-separated).
- H7: Health endpoint: `GET /health` returns `{"status": "ok"}` without auth (registered via `@mcp.custom_route()`).

**Files:** `src/cad_mcp/transport.py` (configure transport from env/args), updates to `server.py` main().

**Gate:** server starts on `--transport http`; `curl /health` returns 200; tool call via `mcp` client over HTTP succeeds; request without token returns 401 when `CAD_MCP_AUTH_TOKEN` is set; stdio mode still works.

---

### 10.2 `gen_ai_mesh` tool (Meshy text-to-3D)

**Motivation:** CadQuery excels at precise parametric parts but struggles with organic shapes (figurines, characters, terrain). The `gen_ai_mesh` tool delegates organic geometry to the Meshy API, downloads the result as GLB, and imports it into the session so the LLM can boolean-combine it with parametric parts.

**Prerequisite:** `MESHY_API_KEY` env var (format `msy_...`). When unset, the tool is still registered but returns a structured error telling the LLM the key is missing and how to set it.

**Tool spec:**

| Field | Value |
|---|---|
| Tool name | `gen_ai_mesh` |
| Input | `prompt: str`, `negative_prompt: str = ""`, `art_style: "realistic" \| "sculpture" = "realistic"`, `topology: "triangle" \| "quad" = "triangle"`, `target_polycount: int = 4000`, `refine: bool = False`, `ai_model: str = "latest"` |
| Output | JSON: `{ok, task_id, status, model_urls, thumbnail_url, polycount, format}` or structured error |
| Side effect | Downloads GLB to session tmpdir, imports as trimesh, converts to BREP if `refine=False`, stores as session shape so `render_views`/`export_model`/`validate_mesh` work on it |

**Workflow (async with polling):**
1. `POST https://api.meshy.ai/openapi/v2/text-to-3d` with `mode: "preview"`, auth via `Authorization: Bearer $MESHY_API_KEY`.
2. Poll `GET .../text-to-3d/{task_id}` every 3s until `status` is `SUCCEEDED` or `FAILED`. Timeout after 120s.
3. Download the GLB from `model_urls.glb`.
4. If `refine=True`: POST again with `mode: "refine"` and `preview_task_id`, poll again (timeout 180s), download refined GLB.
5. Import GLB via trimesh, convert to OCP shape (`BRepBuilderAPI_MakeShell` / sew from mesh triangles), serialize to session BREP.
6. Return summary to LLM with thumbnail URL (as `ImageContent` if possible) and mesh stats.

Requirements:
- M1: Network call uses `httpx` (async). The tool itself is async. No network access from the sandbox -- this runs in the server process, not the subprocess.
- M2: All API calls include `Authorization: Bearer {key}` header.
- M3: Poll interval 3s, preview timeout 120s, refine timeout 180s. On timeout, return `{ok: false, error: "generation_timeout", task_id}` so the LLM can retry or adjust the prompt.
- M4: On Meshy API error (4xx/5xx), return structured error with HTTP status, error message, and hint. 429 -> hint "rate limited, wait and retry".
- M5: Downloaded GLB goes to `{session.tmpdir}/meshy/{task_id}.glb`. Never outside the session dir.
- M6: Mesh-to-BREP conversion: triangulate via trimesh, build an OCP `TopoDS_Compound` from triangles using `BRepBuilderAPI_MakePolygon` + `BRepBuilderAPI_MakeFace` + `BRep_Builder.Add()`. This produces a tessellated B-rep (no NURBS surfaces) -- acceptable for organic shapes.
- M7: The resulting shape is stored as the session's current shape (same as `execute_cad`). Code history records a synthetic entry: `# gen_ai_mesh: "{prompt}" (task_id: {id})`.
- M8: `target_formats` sent to Meshy always includes `"glb"` (required for import). Additional formats are not requested to minimize generation time.
- M9: When `MESHY_API_KEY` is unset, the tool returns `{ok: false, error: "missing_api_key", hint: "Set MESHY_API_KEY env var (get one at https://meshy.ai)"}`.
- M10: Add `httpx>=0.28` to project dependencies.

**Files:** `src/cad_mcp/tools/gen_ai_mesh.py`, `src/cad_mcp/meshy.py` (API client), register in `server.py`.

**Gate:** with a live `MESHY_API_KEY`, `gen_ai_mesh(prompt="a simple chess pawn")` returns a shape that `render_views` can render and `export_model` can export as STL; without the key, returns the missing-key error; mock tests cover the poll loop, timeout, and error paths.

---

### 10.3 Assembly support

**Motivation:** Real-world prints often involve multiple parts (a box + lid, a bracket + cover plate, a phone stand with a cable clip). Assembly support lets the LLM design multi-part models, position them relative to each other, and export each part as a separate STL while also offering a combined preview.

**Concepts:**
- **Part**: a named shape with a position (translation + rotation). Each part has its own code history.
- **Assembly**: an ordered collection of parts. One assembly per session.
- **Active part**: the part that `execute_cad` writes to. Defaults to `"main"`. Switch with `set_active_part`.

**New tools:**

| Tool | Input | Output | Notes |
|---|---|---|---|
| `create_part` | `name: str`, `color: str = "steel"` | confirmation + part list | Creates an empty part and sets it active. Name must be unique within the assembly. |
| `set_active_part` | `name: str` | confirmation + active part info | Subsequent `execute_cad` calls write to this part. |
| `position_part` | `name: str`, `translate: [x,y,z] = [0,0,0]`, `rotate: [rx,ry,rz] = [0,0,0]` | confirmation + new position | Translation in mm, rotation in degrees (Euler XYZ). Applied during render/export, not baked into geometry. |
| `list_parts` | — | JSON array of `{name, bbox, solid_count, position, is_active}` | Overview of assembly state. |
| `delete_part` | `name: str` | confirmation + remaining parts | Cannot delete the last part. |

**Changes to existing tools:**

| Tool | Change |
|---|---|
| `execute_cad` | Writes to the active part's code history and BREP. |
| `render_views` | Renders all parts in a single scene with distinct colors. Optional `parts: list[str]` filter. |
| `validate_mesh` | Validates each part independently. Optional `part: str` to validate one. Reports per-part + assembly-level interference check. |
| `measure` | Adds `what: "clearance"` to measure minimum distance between two named parts. Existing modes operate on the active part. |
| `export_model` | Adds `parts: "all" \| "active" \| list[str]` (default `"all"`). When exporting multiple parts: one file per part (`{filename}_{partname}.stl`) + one combined file. STEP export writes a single file with named solids (XCAF). |
| `list_session` | Includes part list and active part name. |
| `reset_session` | Clears all parts. |

**Session state changes:**
- `session.py` gains `parts: dict[str, Part]` where `Part` has `name`, `code_history`, `brep_path`, `color`, `position` (translation + rotation), `bbox`.
- `active_part: str` tracks which part `execute_cad` targets.
- Backward compatible: sessions with no explicit parts behave as today (implicit single part named `"main"`).

Requirements:
- A1: `create_part` creates a new `Part`, sets it active, returns updated part list.
- A2: `set_active_part` validates the name exists, switches `session.active_part`.
- A3: `position_part` stores translation/rotation on the `Part`. Applied as a rigid transform during tessellation for render/export/validate. The BREP geometry stays at origin.
- A4: `render_views` composes all parts into one scene. Each part gets a distinct color from a preset palette (steel gray, blue, red, green, orange, purple). Color overridable via `create_part(color=)`.
- A5: `export_model` with multiple parts produces `{name}_{part}.{ext}` per part + `{name}_assembly.{ext}` combined. STEP assembly export uses `XCAFDoc_ShapeTool` to write named shapes.
- A6: `validate_mesh` per-part validation unchanged. Assembly-level: check for part-to-part interference using boolean intersection -- if intersection volume > 0.01 mm^3, flag as `interference` issue with the two part names.
- A7: `measure(what="clearance", parts=["lid", "box"])` computes minimum distance between the two parts' shapes using `BRepExtrema_DistShapeShape`.
- A8: Backward compatibility: a session that never calls `create_part` has one implicit part `"main"` and all existing behavior is unchanged. No migration needed.
- A9: Part names are validated: `[a-z][a-z0-9_]{0,31}` (lowercase, starts with letter, max 32 chars).
- A10: Maximum 16 parts per assembly (prevent runaway complexity).

**Files:** update `src/cad_mcp/session.py` (Part dataclass, assembly state), new `src/cad_mcp/tools/create_part.py`, `set_active_part.py`, `position_part.py`, `list_parts.py`, `delete_part.py`; modify `execute_cad.py`, `render_views.py`, `validate_mesh.py`, `measure.py`, `export_model.py`, `list_session.py`, `reset_session.py`.

**Gate:** create two parts (box + lid), position lid above box, render shows both with different colors, export produces 3 STLs (box, lid, assembly), validate flags interference when lid overlaps box, clearance measurement returns 0 when touching and >0 when separated.

---

## 11. v2+ backlog
Parametric "tweak sliders" resource · STEP import + modify · multi-material 3MF · image-to-3D via Meshy `v1/image-to-3d` · undo/redo per part · assembly constraints solver (mate, align, offset).
