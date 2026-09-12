# BACKLOG — cad-mcp

Review date: 2026-09-12 · Reviewed at commit `80d660e` (Phases 0–6c complete, 135 tests green, ruff + `mypy --strict` clean).

**Scope guard.** Every item below serves the unchanged core requirement: *an MCP server that lets any LLM design 3D models from text descriptions using CadQuery*. Nothing here adds a new modelling paradigm, a GUI, or a tool outside SPEC.md. Where an item would change the contract (N4 determinism, N1 sandbox guarantees), the acceptance criteria require SPEC.md to be amended in the same PR — per PLAN.md's rule that deviation means updating the spec, not silently drifting.

**Tracking.** All 34 items are filed as GitHub issues ([#2–#35](https://github.com/MaximumTrainer/llm-cad/issues?q=is%3Aissue+label%3Abacklog-2026-09)), labelled `backlog-2026-09` plus a severity (`P0`/`P1`/`P2`) and an `area:*` label. This file stays the readable overview; the issues are where work is tracked.

## How to read this

| Field | Meaning |
|---|---|
| **Severity** | P0 = ships a false guarantee or loses user data/state · P1 = breaks the core loop or a stated SPEC requirement · P2 = hygiene, speed, maintainability |
| **Spec** | The SPEC.md clause the item defends |
| **Evidence** | What was actually observed on this machine, not inferred |

## Summary

| ID | Title | Sev | Area |
|---|---|---|---|
| [CAD-001](https://github.com/MaximumTrainer/llm-cad/issues/2) | Sandbox does not confine the filesystem | P0 | Sandbox |
| [CAD-002](https://github.com/MaximumTrainer/llm-cad/issues/3) | Import allowlist is bypassable; raw sockets reachable | P0 | Sandbox |
| [CAD-003](https://github.com/MaximumTrainer/llm-cad/issues/4) | `export_model` writes outside the session dir via `filename` | P0 | Export |
| [CAD-004](https://github.com/MaximumTrainer/llm-cad/issues/5) | No resource limits on Windows; `os.fork` unblocked; orphans survive timeout | P0 | Sandbox |
| [CAD-005](https://github.com/MaximumTrainer/llm-cad/issues/6) | Malicious-code test suite does not test containment | P0 | Tests |
| [CAD-006](https://github.com/MaximumTrainer/llm-cad/issues/7) | Bearer token compared non-constant-time; host allowlist ineffective | P0 | Transport |
| [CAD-007](https://github.com/MaximumTrainer/llm-cad/issues/8) | All MCP sessions share one global session | P0 | Session |
| [CAD-008](https://github.com/MaximumTrainer/llm-cad/issues/9) | Concurrent tool calls race on shared sandbox files | P1 | Session |
| [CAD-009](https://github.com/MaximumTrainer/llm-cad/issues/10) | OCP runs in-process; a kernel fault kills the server | P1 | Architecture |
| [CAD-010](https://github.com/MaximumTrainer/llm-cad/issues/11) | pyrender backend draws no axes or scale ticks | P1 | Render |
| [CAD-011](https://github.com/MaximumTrainer/llm-cad/issues/12) | The "preferred" renderer is undeclared and never exercised | P1 | Render |
| [CAD-012](https://github.com/MaximumTrainer/llm-cad/issues/13) | Three runtime imports are undeclared dependencies | P1 | Packaging |
| [CAD-013](https://github.com/MaximumTrainer/llm-cad/issues/14) | Golden-image test covers a code path the tool never calls | P1 | Tests |
| [CAD-014](https://github.com/MaximumTrainer/llm-cad/issues/15) | No latency budget enforcement for N2 | P1 | Performance |
| [CAD-015](https://github.com/MaximumTrainer/llm-cad/issues/16) | Wall-thickness check cannot distinguish material from air | P1 | Validate |
| [CAD-016](https://github.com/MaximumTrainer/llm-cad/issues/17) | Overhang report gives counts, not locations | P1 | Validate |
| [CAD-017](https://github.com/MaximumTrainer/llm-cad/issues/18) | Determinism proven for STL only | P1 | Export |
| [CAD-018](https://github.com/MaximumTrainer/llm-cad/issues/19) | `measure(what="distance")` measures centres, not distance | P1 | Measure |
| [CAD-019](https://github.com/MaximumTrainer/llm-cad/issues/20) | Tool responses have no single envelope | P1 | LLM contract |
| [CAD-020](https://github.com/MaximumTrainer/llm-cad/issues/21) | Prompts teach v1 only — assembly and `gen_ai_mesh` are untaught | P1 | Prompts |
| [CAD-021](https://github.com/MaximumTrainer/llm-cad/issues/22) | Example resources are not discoverable | P2 | Resources |
| [CAD-022](https://github.com/MaximumTrainer/llm-cad/issues/23) | `gen_ai_mesh` breaks the code-history invariant | P1 | AI mesh |
| [CAD-023](https://github.com/MaximumTrainer/llm-cad/issues/24) | Exports land in a temp dir that reset silently destroys | P1 | Export |
| [CAD-024](https://github.com/MaximumTrainer/llm-cad/issues/25) | No CI for tests, lint, or types | P1 | CI |
| [CAD-025](https://github.com/MaximumTrainer/llm-cad/issues/26) | Test suite takes 8 minutes | P2 | Tests |
| [CAD-026](https://github.com/MaximumTrainer/llm-cad/issues/27) | Structured logging is inert outside `main()` | P2 | Observability |
| [CAD-027](https://github.com/MaximumTrainer/llm-cad/issues/28) | Meshy client has no download cap, drifting clock, no retry | P2 | AI mesh |
| [CAD-028](https://github.com/MaximumTrainer/llm-cad/issues/29) | Mesh→BREP sewing is unbounded | P2 | AI mesh |
| [CAD-029](https://github.com/MaximumTrainer/llm-cad/issues/30) | Dependency pins contradict CLAUDE.md | P2 | Packaging |
| [CAD-030](https://github.com/MaximumTrainer/llm-cad/issues/31) | No git hooks | P1 | Tooling |
| [CAD-031](https://github.com/MaximumTrainer/llm-cad/issues/32) | No Claude Code skills to guide design and implementation | P1 | Tooling |
| [CAD-032](https://github.com/MaximumTrainer/llm-cad/issues/33) | Nothing detects SPEC/README/code drift | P2 | Process |
| [CAD-033](https://github.com/MaximumTrainer/llm-cad/issues/34) | No `.mcp.json`, so the documented connect step is manual | P2 | DX |
| [CAD-034](https://github.com/MaximumTrainer/llm-cad/issues/35) | Common CadQuery errors return no hint, so the LLM loops | P1 | Sandbox |

---

# P0 — False guarantees

## CAD-001 — Sandbox does not confine the filesystem

> Tracked as [#2](https://github.com/MaximumTrainer/llm-cad/issues/2)

**Severity:** P0 · **Area:** Sandbox · **Spec:** G5, N1, 9.3 · **Files:** `src/cad_mcp/_sandbox_worker.py`, `src/cad_mcp/sandbox.py`

**Problem.** SPEC N1 promises "cwd = per-session temp dir" and acceptance criterion 9.3 promises a "file escape" is contained. Neither is true. `builtins.open` is untouched and `os` file functions are not blocked, so user code reads and writes anywhere the server process can. CLAUDE.md's "don't let render or export write outside the session's temp directory" is likewise unenforced for the code path that matters most.

**Evidence.** Run against `sandbox.run()` on this machine:

```
sandbox result ok = True | error: None None
file written outside session tmpdir: True  C:\Users\danwo\cadmcp_ESCAPE_PROOF.txt
contents: sandbox escaped: wrote to user home
```

The tool reported **success**. Setting cwd is a convenience, not a boundary.

**Scope.** Decide and implement one of two postures, then make SPEC.md say exactly that:

- **(a) Real boundary** — run the worker under OS-level confinement (Linux: `bwrap`/seccomp or a container; macOS: `sandbox-exec` profile; Windows: job object + restricted token), with the session tmpdir as the only writable path.
- **(b) Honest advisory** — keep monkeypatching, wrap `open` and the `os` path functions with a tmpdir-prefix check, and rewrite SPEC N1/G5 to state that the sandbox defends against *accidents, not adversaries*, with a README warning that untrusted prompts must not reach this server.

(a) is the right answer for a server that accepts LLM-authored code from arbitrary hosts. (b) is acceptable only as an explicitly-labelled interim step, and must not keep claiming 9.3.

**Acceptance criteria**

- [ ] User code attempting `open(<path outside tmpdir>, "w")` fails, and `execute_cad` returns a structured error naming the blocked path — not `OK`.
- [ ] User code attempting to read a file outside the session tmpdir (the user's home directory, `/etc/passwd`, `%USERPROFILE%`) fails the same way.
- [ ] Writes *inside* the session tmpdir still succeed (CadQuery writes intermediate files there).
- [ ] `pathlib`, `os.open`, `os.rename`, `os.remove`, `shutil`-equivalents, and `numpy.save` are all covered, not just `builtins.open` — one test per vector.
- [ ] SPEC.md N1 and §9.3 state the actual guarantee and its platform coverage; README carries a matching "threat model" section.
- [ ] Tests assert the *effect* (no file exists at the target path after the call), not just the error string.

## CAD-002 — Import allowlist is bypassable; raw sockets reachable

> Tracked as [#3](https://github.com/MaximumTrainer/llm-cad/issues/3)

**Severity:** P0 · **Area:** Sandbox · **Spec:** N1, 9.3 · **Files:** `src/cad_mcp/_sandbox_worker.py`

**Problem.** Three independent holes in `_install_import_restriction` and `_block_sockets`:

1. The allowlist admits anything already in `sys.modules` (`top in snapshot`). Importing `cadquery` first populates hundreds of modules, so the effective allowlist is "whatever CadQuery happens to import" — a set that changes with every CadQuery version.
2. `_block_sockets` replaces `socket.socket`, but the C accelerator `_socket` is untouched and is in `snapshot`. `import _socket; _socket.socket()` succeeds.
3. `importlib.import_module` does not route through `builtins.__import__`, so the restriction is bypassed for any module whose own imports are already satisfied.

**Evidence.** `import _socket; s = _socket.socket()` inside the sandbox returned `ok=True` with a valid socket object.

**Scope.** Replace the snapshot-based allowlist with an explicit allowlist evaluated against the *resolved* module (`cadquery`, `math`, `numpy`, and the concrete set CadQuery needs at call time), block the C-level aliases (`_socket`, `_ssl`, `_ctypes`, `nt`/`posix` where feasible), and install an `importlib` meta-path finder so `import_module` is subject to the same policy. This item is a prerequisite for CAD-001(b) and is largely obsolete under CAD-001(a) — sequence them together.

**Acceptance criteria**

- [ ] `import _socket`, `import _ssl`, and `import _ctypes` are refused with the standard structured error.
- [ ] `importlib.import_module("subprocess")` and `__import__("subprocess")` are both refused.
- [ ] Creating a socket object by any route fails; a test asserts no outbound connection is possible, using a local listener rather than a public IP.
- [ ] The allowlist is a declared constant, not a runtime snapshot; a test asserts a module CadQuery happens to import (e.g. `json`) is *not* silently importable unless explicitly allowed.
- [ ] All eight SPEC 9.3 vectors (network, file read, file write, subprocess, fork, infinite loop, memory bomb, import bypass) have a dedicated test asserting containment.

## CAD-003 — `export_model` writes outside the session dir via `filename`

> Tracked as [#4](https://github.com/MaximumTrainer/llm-cad/issues/4)

**Severity:** P0 · **Area:** Export · **Spec:** CLAUDE.md ("Don't let render or export write outside the session's temp directory") · **Files:** `src/cad_mcp/tools/export_model.py`, `src/cad_mcp/export.py`

**Problem.** `filename` is concatenated onto the output directory with no validation: `output_dir / filename`. A relative-traversal filename escapes the session tmpdir. Because the LLM chooses the filename, a prompt-injected or simply careless model overwrites arbitrary files.

**Evidence.**

```
export_model(format="stl", filename="../../../../ESCAPED_EXPORT")
→ {"ok": true, "path": "...\\output\\..\\..\\..\\..\\ESCAPED_EXPORT.stl", "size_bytes": 684}
→ file created at C:\Users\danwo\AppData\ESCAPED_EXPORT.stl
```

**Scope.** Sanitise in one place used by every writer. Reject rather than silently rewrite, so the LLM learns the rule.

**Acceptance criteria**

- [ ] `filename` is validated against `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; anything else returns a structured error naming the rule.
- [ ] Path separators, `..`, absolute paths, drive letters, and NT device names (`CON`, `NUL`, …) are all rejected — one test each.
- [ ] After every write, the resolved path is asserted to be inside the session output dir; a mismatch raises before the file is created.
- [ ] The same guard covers the multi-part paths (`{base}_{part}{ext}`, `{base}_assembly{ext}`) and any future writer.
- [ ] Returned `path` values are `Path.resolve()`d — no `..` segments in what the LLM sees.

## CAD-004 — No resource limits on Windows; `os.fork` unblocked; orphans survive timeout

> Tracked as [#5](https://github.com/MaximumTrainer/llm-cad/issues/5)

**Severity:** P0 · **Area:** Sandbox · **Spec:** N1, 9.3 · **Files:** `src/cad_mcp/sandbox.py`, `src/cad_mcp/_sandbox_worker.py`

**Problem.** Three gaps in process-level containment:

1. `_preexec` returns `None` on `win32`, so the 2 GB cap does not exist on Windows — the primary development platform for this repo. A `numpy` allocation loop takes the machine down.
2. The blocked-`os` list covers `system`/`popen`/`exec*`/`spawn*` but **not `fork`**. On Linux and macOS a fork bomb runs.
3. `subprocess.run(timeout=…)` kills only the direct child. Grandchildren created before the timeout keep running after `execute_cad` returns `TimeoutError`.

**Evidence.** `grep -n fork src/cad_mcp/_sandbox_worker.py` → no match. `_preexec` short-circuits on `sys.platform == "win32"`. The existing fork-bomb test passes on Windows only because `os.fork` does not exist there.

**Scope.** Job objects on Windows (memory and process caps), `RLIMIT_NPROC` + `RLIMIT_CPU` + `RLIMIT_FSIZE` alongside `RLIMIT_AS` on Unix, process-group kill on timeout, and `fork`/`forkpty`/`posix_spawn` added to the blocked list.

**Acceptance criteria**

- [ ] A memory bomb (`numpy.zeros` in a loop) is killed on Windows, Linux, and macOS without the host running out of memory; the tool returns a structured `MemoryError`-class response.
- [ ] `os.fork` and `os.posix_spawn` are blocked; a fork-bomb test asserts the host process count returns to baseline within 5 s of the call.
- [ ] Timeout kills the whole process group; a test spawns a long-lived grandchild and asserts it is gone after `execute_cad` returns.
- [ ] `RLIMIT_FSIZE` (or the Windows equivalent) bounds how large a file user code can create inside the tmpdir.
- [ ] Limits are configurable via env (`CAD_MCP_SANDBOX_TIMEOUT_S`, `CAD_MCP_SANDBOX_MEM_MB`) with the SPEC defaults; documented in README.

## CAD-005 — Malicious-code test suite does not test containment

> Tracked as [#6](https://github.com/MaximumTrainer/llm-cad/issues/6)

**Severity:** P0 · **Area:** Tests · **Spec:** 9.3 · **Files:** `tests/test_acceptance.py`, `tests/test_execute_cad.py`

**Problem.** The suite that certifies acceptance criterion 9.3 passes while the system is wide open (CAD-001, CAD-002). Specifically:

- `test_spec_9_3_file_escape_blocked` executes `import shutil` and asserts the output does not start with `OK`. It never touches the filesystem. It tests an import rule, not a file escape.
- `test_spec_9_3_fork_bomb_blocked` and `test_fork_bomb_blocked` assert `"OK" not in text`. On Windows they pass because `os.fork` raises `AttributeError`; on Linux they would fork-bomb the runner and still assert nothing about containment.
- Several assertions are `assert "OK" not in text`, which any error message satisfies — including an error for an unrelated reason. Observed during this review: escape probes "failed" only at the `result`-type check, *after* the malicious action had already succeeded.

Green tests here are worse than no tests: they are the evidence behind a SPEC guarantee that does not hold.

**Scope.** Rewrite the 9.3 suite around observable effects, and make each test assert the specific error rather than the absence of a substring.

**Acceptance criteria**

- [ ] Every 9.3 test asserts a side-effect-based outcome: the target file does not exist, the local listener received no connection, the process table is clean, RSS stayed under the cap.
- [ ] No test in the suite asserts containment via `"OK" not in text` alone; each asserts the specific `error_type`.
- [ ] Each test constructs its payload so it would report `ok=True` if containment failed — i.e. the payload assigns a valid `result` — so the type check cannot mask an escape.
- [ ] Tests that cannot run on a platform are `skipif`-marked with a reason, never silently vacuous.
- [ ] The rewritten suite is run against the pre-fix code and demonstrably fails (recorded in the PR) before CAD-001/002/004 land.

## CAD-006 — Bearer token compared non-constant-time; host allowlist ineffective

> Tracked as [#7](https://github.com/MaximumTrainer/llm-cad/issues/7)

**Severity:** P0 · **Area:** Transport · **Spec:** 10.1 H2, H6 · **Files:** `src/cad_mcp/transport.py`

**Problem.** Two auth/transport defects:

1. `BearerTokenVerifier.verify_token` uses `token != self._expected`. Python's `str.__eq__` short-circuits, leaking token length and prefix over repeated requests.
2. `run_kwargs()` only sets `TransportSecuritySettings` when the host is *not* loopback, and then adds the bind address itself (`"0.0.0.0"`) to `allowed_hosts`. `0.0.0.0` is never a real `Host` header, so DNS-rebinding protection is inert in exactly the deployment where it matters. With `CAD_MCP_ALLOWED_HOSTS` unset and a public bind, there is effectively no host validation.

**Scope.** Constant-time comparison; explicit, always-on transport security whose allowlist derives from reachable names, not the bind address.

**Acceptance criteria**

- [ ] Token comparison uses `secrets.compare_digest` on UTF-8 bytes.
- [ ] Binding a non-loopback host without `CAD_MCP_ALLOWED_HOSTS` either refuses to start or logs a prominent warning and applies a deny-by-default allowlist — decided and documented in SPEC 10.1 H6.
- [ ] `0.0.0.0` and `::` are never inserted into `allowed_hosts`.
- [ ] Tests: valid token → 200; wrong token → 401; absent token with `CAD_MCP_AUTH_TOKEN` set → 401; `Host:` header outside the allowlist → rejected.
- [ ] README states that bearer auth over plain HTTP is for localhost/trusted networks and that remote use requires a TLS terminator.

---

# P0/P1 — Architecture and state

## CAD-007 — All MCP sessions share one global session

> Tracked as [#8](https://github.com/MaximumTrainer/llm-cad/issues/8)

**Severity:** P0 · **Area:** Session · **Spec:** 10.1 H4 ("each HTTP MCP session gets its own `Session`"), §7 · **Files:** `src/cad_mcp/session.py`, all 14 tools, `src/cad_mcp/resources.py`, `src/cad_mcp/_logging.py`

**Problem.** `session.get_or_create()` takes a `session_id` parameter that **no caller ever passes**. Every tool, the resource handler, and the logger call it bare, so `_sessions` only ever holds the key `"default"`. Over HTTP, two concurrent clients share one tmpdir, one parts dict, one code history: client B's `execute_cad` overwrites client A's model, `reset_session` wipes both, and `list_session` / `cad://session/current/code` leak A's code to B. H4 is unimplemented, and the `session_idle_timeout` cleanup it specifies does not exist — tmpdirs accumulate for the process lifetime and are never removed on disconnect.

**Evidence.** `grep -rn "get_or_create(" src/` → 14 call sites, all zero-argument. `session.get_or_create("some-other-http-session")` returns a different `Session` object that nothing in the server can ever reach.

**Scope.** Thread the MCP session identity from the request context to every tool (SDK context object), keep `"default"` as the stdio identity, add idle eviction, and delete the tmpdir on disconnect.

**Acceptance criteria**

- [ ] Every tool resolves its session from the MCP request context; the zero-argument call is removed from `src/` (a lint rule or test enforces this).
- [ ] Two concurrent HTTP MCP sessions each build a different model; neither sees the other's parts, code history, exports, or `cad://session/current/code`.
- [ ] `reset_session` from one HTTP session leaves the other untouched.
- [ ] Idle sessions are evicted and their tmpdirs removed after `session_idle_timeout` (default 300 s, env-overridable); a test asserts the directory is gone.
- [ ] Sessions are cleaned up on process shutdown (stdio EOF and HTTP SIGTERM).
- [ ] Log lines carry the real session id, not the constant `"default"` (see CAD-026).

## CAD-008 — Concurrent tool calls race on shared sandbox files

> Tracked as [#9](https://github.com/MaximumTrainer/llm-cad/issues/9)

**Severity:** P1 · **Area:** Session · **Spec:** §7, N4 · **Files:** `src/cad_mcp/sandbox.py`, `src/cad_mcp/tools/execute_cad.py`

**Problem.** Every sandbox run writes `tmpdir/user_code.py`, and the worker always writes `tmpdir/current.brep` regardless of which part is active. The MCP SDK dispatches synchronous tools on a thread pool, so two `execute_cad` calls in one session can interleave: the second overwrites `user_code.py` while the first is reading it, and `current.brep` from part A can be copied onto part B. This survives CAD-007 — it is a within-session race, not a cross-session one.

**Acceptance criteria**

- [ ] Sandbox input and output files are unique per invocation (e.g. `run-{uuid}/user_code.py`, `run-{uuid}/out.brep`), cleaned up after the copy.
- [ ] A per-session lock serialises mutating tools (`execute_cad`, `gen_ai_mesh`, part mutations, `reset_session`); read-only tools are unaffected.
- [ ] A test issues N concurrent `execute_cad` calls against one session and asserts each part's BREP matches its own code, with no cross-contamination.
- [ ] A failed run never leaves a partial BREP that a later tool can read as current.

## CAD-009 — OCP runs in-process; a kernel fault kills the server

> Tracked as [#10](https://github.com/MaximumTrainer/llm-cad/issues/10)

**Severity:** P1 · **Area:** Architecture · **Spec:** §7, N3 · **Files:** `src/cad_mcp/render.py`, `measure.py`, `export.py`, `validate.py`

**Problem.** The SPEC's architecture isolates *user code* in a subprocess, but everything downstream — tessellation, measurement, STEP/XCAF export, boolean interference — loads the BREP and calls OCP **inside the server process**. OCP is a C++ binding: a malformed shape, a degenerate boolean, or a teardown bug is a segfault, not a Python exception. When that happens the MCP server dies mid-conversation and the LLM gets a transport error rather than an actionable message, violating N3's "every failure returns what/where/hint".

**Evidence.** A script that imports `cadquery`, tessellates a BREP, and renders via matplotlib exits with code 139 (segmentation fault) after completing its work on this machine — OCP/VTK teardown. The same libraries are loaded into the long-lived server process.

**Scope.** Either move BREP post-processing into the same worker-subprocess mechanism used for user code (preferred — it reuses `sandbox.py`, adds crash isolation, and removes OCP from the server's address space), or wrap each OCP entry point in a subprocess call with a timeout. Do not paper over it with `try/except`: a segfault is not catchable.

**Acceptance criteria**

- [ ] A deliberately pathological BREP that segfaults OCP causes a structured tool error, and the server answers a subsequent `ping` on the same connection.
- [ ] Tessellation results are cached per (BREP mtime, tolerance) so the extra process boundary does not multiply render cost — measured against CAD-014's budget.
- [ ] No regression in the SPEC 9.1 acceptance path.
- [ ] SPEC §7's architecture diagram reflects where OCP actually runs.

---

# P1 — The core loop

## CAD-010 — pyrender backend draws no axes or scale ticks

> Tracked as [#11](https://github.com/MaximumTrainer/llm-cad/issues/11)

**Severity:** P1 · **Area:** Render · **Spec:** 5.1 (`render_views`: "Include axes + mm scale ticks"), G3 · **Files:** `src/cad_mcp/render.py`

**Problem.** The matplotlib fallback draws a triad, labelled axes, and nice mm ticks. The pyrender path — the one SPEC and CLAUDE.md call preferred — draws none of it: no triad, no ticks, no view titles. The LLM's only way to judge scale from a render is the axis annotation; without it the visual feedback loop degrades to "does it look roughly right", which is precisely the failure mode `measure` exists to catch and renders exist to prevent.

Two further defects in the same path:

- The grid is hardcoded to two columns (`cell_w = width // 2`, `col = idx % 2`). A 1-view or 3-view request leaves blank quadrants; more than 4 views overwrite each other. The matplotlib path computes `rows = ceil(n / cols)` correctly, so the two backends disagree on layout.
- `render_views` accepts arbitrary `width`/`height` with no bound, against CLAUDE.md's "800×600 max".

**Acceptance criteria**

- [ ] pyrender output carries an axis triad, per-view titles, and mm scale ticks legible at the default 800×600 — verified by a test that renders a 10 mm and a 100 mm cube and asserts the tick labels differ.
- [ ] Both backends produce the same grid layout for 1, 2, 3, and 4 views; a test asserts cell count and placement.
- [ ] `views` is de-duplicated and capped; unknown view names return a structured error listing valid choices (already true in `render.py` — must hold at the tool boundary).
- [ ] `width`/`height` are clamped to the documented maximum, with the clamp reported in the accompanying text block.
- [ ] A side-by-side visual check of both backends on the SPEC 9.1 bracket is attached to the PR.

## CAD-011 — The "preferred" renderer is undeclared and never exercised

> Tracked as [#12](https://github.com/MaximumTrainer/llm-cad/issues/12)

**Severity:** P1 · **Area:** Render · **Spec:** N5, PLAN Phase 2 gate ("works with EGL absent (fallback path tested in CI)") · **Files:** `pyproject.toml`, `src/cad_mcp/render.py`, `README.md`

**Problem.** `pyrender` is not a dependency and is not installed in this repo's `.venv`. `_check_pyrender()` therefore always returns `False`, and roughly 200 lines of pyrender code — two full render functions plus the camera-pose maths — have never run. Meanwhile CLAUDE.md and SPEC 5.1/N5 present pyrender+EGL as primary and matplotlib as the CI fallback. The reality is the inverse. The golden-image test, the latency claims, and the "fallback tested in CI" gate all describe a configuration nobody runs.

**Evidence.** `importlib.util.find_spec("pyrender")` → `False`. README §Rendering tells the user to `pip install pyrender` manually.

**Scope.** Pick the real primary and make the repo say so. Recommended: declare `pyrender` under an optional extra (`cad-mcp[gpu]`), keep matplotlib as the supported default, and test **both** paths — with the pyrender path gated on availability rather than dead.

**Acceptance criteria**

- [ ] `pyproject.toml` declares the renderer dependency explicitly (extra or core — decided, not implicit).
- [ ] CI runs the matplotlib path on every push and the pyrender path in at least one job (EGL or osmesa), so neither backend can rot.
- [ ] `_check_pyrender()` failures are logged at WARNING with the underlying exception, not swallowed — today an EGL misconfiguration is indistinguishable from "not installed".
- [ ] A `render_backend` field appears in the text block returned alongside the image, so the LLM and the user know which renderer produced the picture.
- [ ] SPEC N5 and CLAUDE.md are updated to match whichever backend is actually primary.

## CAD-012 — Three runtime imports are undeclared dependencies

> Tracked as [#13](https://github.com/MaximumTrainer/llm-cad/issues/13)

**Severity:** P1 · **Area:** Packaging · **Files:** `pyproject.toml`

**Problem.** `render.py` imports `matplotlib` and `PIL`; `validate.py` imports `scipy.spatial.KDTree`. None of the three appear in `[project.dependencies]`. They resolve today only because `cadquery` → `vtk` → `matplotlib` and `cadquery` → `scipy` happen to pull them in. The moment VTK drops its matplotlib dependency or CadQuery restructures, rendering and validation break at runtime in a user's session with an `ImportError` — the two features the product is built around.

**Evidence.** `uv.lock` line 3075 shows `matplotlib` as a dependency of `vtk`, not of `cad-mcp`. `find_spec` confirms all three are installed but unowned.

**Acceptance criteria**

- [ ] `matplotlib`, `pillow`, and `scipy` are declared direct dependencies with lower bounds.
- [ ] A check in CI (a test, or `deptry`-style tooling) fails when a module imported by `src/` is not declared.
- [ ] A clean-environment install (`uv sync --no-dev` in a fresh venv) can run `scripts/smoke.py` end to end — added as a CI job.

## CAD-013 — Golden-image test covers a code path the tool never calls

> Tracked as [#14](https://github.com/MaximumTrainer/llm-cad/issues/14)

**Severity:** P1 · **Area:** Tests · **Spec:** PLAN Phase 2 gate · **Files:** `tests/test_render.py`, `src/cad_mcp/render.py`, `src/cad_mcp/tools/render_views.py`

**Problem.** Since assembly support landed, the `render_views` **tool** always calls `render.render_assembly()` — both branches of its `if len(part_meshes) == 1` are byte-identical dead code. But `render.render_views()` and its two single-part helpers are still there, and they are what the golden perceptual-hash test exercises. The one test protecting the product's core output guards a function no user can reach.

**Evidence.** `grep -rn "render_views(" src/ tests/` → the module-level function is called only from `tests/test_render.py` (4 sites). `tools/render_views.py` lines 74–81 contain an if/else with identical bodies.

**Acceptance criteria**

- [ ] The duplicated `if/else` in `tools/render_views.py` is collapsed to a single call.
- [ ] `render.render_views` and the single-part helpers are either deleted or become thin wrappers over the assembly path — one implementation, not two.
- [ ] The golden perceptual-hash test drives the tool (`mcp.call_tool("render_views", …)`), so it covers what ships.
- [ ] Golden hashes are recorded per backend and per platform, with a documented regeneration command; a mismatch message tells the developer how to inspect the diff.
- [ ] Multi-part assembly rendering gets its own golden test (distinct colours, correct relative placement).

## CAD-014 — No latency budget enforcement for N2

> Tracked as [#15](https://github.com/MaximumTrainer/llm-cad/issues/15)

**Severity:** P1 · **Area:** Performance · **Spec:** N2 (execute ≤5 s, render ≤2 s, validate ≤5 s), CLAUDE.md ("renders slower than ~2s is a regression") · **Files:** new `tests/test_perf.py`, `src/cad_mcp/sandbox.py`, `src/cad_mcp/render.py`

**Problem.** CLAUDE.md names render latency as a regression gate, but nothing measures it. Measured on this machine for a modest bracket (2 556 triangles):

| Step | Time |
|---|---|
| `execute_cad`, cold subprocess | 3.56 s |
| `execute_cad`, second call | 5.51 s |
| tessellate (first, includes OCP import) | 3.35 s |
| `render_views`, first call | 1.65 s |
| `render_views`, warm | 0.57 s |

`execute_cad` is at or over the 5 s budget for a *simple* part, and essentially all of it is subprocess cold start — a fresh interpreter importing CadQuery on every call. In `append` mode the whole history re-runs, so cost grows with the conversation. That directly limits how many iterations the LLM can afford within SPEC 9.1's four-iteration target.

**Scope.** A warm sandbox worker pool (pre-imported CadQuery, recycled after each run for isolation) is the single biggest win. Combine with the tessellation cache from CAD-009.

**Acceptance criteria**

- [ ] A perf test asserts the N2 budgets on a defined reference model, marked `slow`, run in CI on a fixed runner size, failing the build on regression.
- [ ] `execute_cad` for the SPEC 9.1 bracket completes within budget including append-mode replay of a 5-block history.
- [ ] Cold-start cost is eliminated or amortised (worker reuse) **without** weakening isolation — each run still starts from a clean namespace, and the worker is recycled after every execution.
- [ ] Render stays ≤2 s for models up to a stated triangle budget; above it, tessellation tolerance degrades gracefully and the response says so.
- [ ] Before/after numbers are recorded in the PR and in README's performance notes.

## CAD-015 — Wall-thickness check cannot distinguish material from air

> Tracked as [#16](https://github.com/MaximumTrainer/llm-cad/issues/16)

**Severity:** P1 · **Area:** Validate · **Spec:** 5.1 (`validate_mesh` wall-thickness violations), PLAN Phase 3 gate · **Files:** `src/cad_mcp/validate.py`

**Problem.** `_wall_thickness` samples face centroids, finds the nearest centroid whose normal is roughly opposite (`dot < -0.3`), and reports that distance as wall thickness. That geometry describes *two surfaces facing each other* — which is a wall when the material is between them and a **gap** when the air is. A 0.5 mm slot, the clearance between a lid and a box, or the inside of a narrow pocket all read as sub-minimum "walls" and produce a false printability failure. Conversely a genuinely thin wall whose opposing face was not sampled (500-sample cap) is missed entirely.

This matters because the LLM is instructed to act on this report. A false "wall too thin" sends it to thicken geometry that was already correct.

**Scope.** Ray-cast inward along the inverted face normal and measure the distance to the first back-facing hit (trimesh's ray module, or the `manifold3d`/OCP distance tools already in the stack). That measures material, not proximity.

**Acceptance criteria**

- [ ] A 0.8 mm-walled box is flagged; a 3 mm-walled box with a 0.5 mm slot cut through it is **not** flagged.
- [ ] A plate with a 0.4 mm clearance gap to a second plate reports no wall violation.
- [ ] The report includes the location (xyz) of the thinnest measured point so the LLM can find it.
- [ ] The `min_mm` estimate is stable across runs (fixed seed retained) and documented as an estimate with its error bound.
- [ ] Runtime stays within N2's 5 s for meshes up to 500 k triangles.

## CAD-016 — Overhang report gives counts, not locations

> Tracked as [#17](https://github.com/MaximumTrainer/llm-cad/issues/17)

**Severity:** P1 · **Area:** Validate · **Spec:** 5.1 ("overhang regions") · **Files:** `src/cad_mcp/validate.py`

**Problem.** `_overhang_analysis` returns `overhang_faces`, `total_downward_faces`, and `max_overhang_angle_deg`. SPEC asks for *regions*. "412 faces exceed 45°" tells the LLM a problem exists but not where, so its only available fix is a blind global change. The build-plate exclusion (`centroid_z < min_z + 0.1`) is also a fixed 0.1 mm tolerance with no relation to the tessellation tolerance it must exceed.

**Acceptance criteria**

- [ ] Overhanging faces are clustered into connected regions; the report lists each region's bounding box, centroid, area, and worst angle, capped at the N worst regions.
- [ ] Region output is phrased for an LLM — e.g. "unsupported overhang, 58°, 12 mm² near (x=20.1, y=0.0, z=14.5)".
- [ ] The build-plate exclusion tolerance derives from the tessellation tolerance rather than a magic 0.1.
- [ ] A known test part (a horizontal bridge plus a 60° cantilever) yields exactly two regions with the expected angles.
- [ ] `max_overhang_angle_deg`'s convention (from vertical vs from horizontal) is documented in the tool docstring and the printability prompt, and is consistent between them.

## CAD-017 — Determinism proven for STL only

> Tracked as [#18](https://github.com/MaximumTrainer/llm-cad/issues/18)

**Severity:** P1 · **Area:** Export · **Spec:** N4 ("same code → identical STEP topology and byte-stable STL") · **Files:** `src/cad_mcp/export.py`, `tests/test_validate_export.py`

**Problem.** `test_export_stl_deterministic` exports twice within one process and compares hashes. That proves tessellation is stable in-process; it does not prove byte-stability across runs, processes, or machines, and nothing at all is asserted for STEP, 3MF, or GLB. STEP writers emit a timestamp in the header and 3MF containers carry UUIDs and zip mtimes, so those formats are almost certainly **not** byte-stable today — a claim the SPEC makes and the tests do not check.

A second inconsistency: `export_stl` does not call `fix_normals()` while `export_glb` and `export_3mf` do, so STL alone can ship inconsistent winding to slicers.

**Acceptance criteria**

- [ ] Determinism is tested across two separate *process* invocations, not two calls in one process.
- [ ] STEP determinism is defined precisely — either byte-stable (strip/pin the timestamp) or topology-stable (compare entity counts and geometry, not bytes) — and SPEC N4 is reworded to match what is actually guaranteed.
- [ ] 3MF and GLB are either made byte-stable (fixed UUIDs, zeroed mtimes) or explicitly excluded from N4 in SPEC.md.
- [ ] `export_stl` applies the same normal handling as the other mesh exporters; a test asserts consistent winding.
- [ ] A cross-platform determinism check runs in CI on at least two OSes.

## CAD-018 — `measure(what="distance")` measures centres, not distance

> Tracked as [#19](https://github.com/MaximumTrainer/llm-cad/issues/19)

**Severity:** P1 · **Area:** Measure · **Spec:** 5.1, 9.2 ("`measure` confirms requested dimensions within 0.1mm") · **Files:** `src/cad_mcp/tools/measure.py`

**Problem.** `_measure_distance` resolves two face selectors and returns the distance between their **centroids**. For parallel planar faces that equals the thickness; for anything else — a cylindrical bore, a filleted face, a face the selector matched in multiples — it is a number with no physical meaning, returned to three decimal places with no caveat. The workflow prompt tells the LLM to use exactly this call to confirm "hole spacing, wall thickness, feature positions within 0.1mm", so a confidently wrong number gets treated as verification. The tool also only accepts *face* selectors, so hole spacing (a vertex/edge property) cannot be measured directly at all.

**Acceptance criteria**

- [ ] `distance` reports true minimum distance between the selected entities via `BRepExtrema_DistShapeShape` (already used by `clearance`), plus the two closest points.
- [ ] Selectors accept faces, edges, and vertices; the selector kind is a parameter or inferred, and is echoed back in the response.
- [ ] When a selector matches more than one entity, the response says how many matched and what was measured — never silently uses the first.
- [ ] Centroid-to-centroid remains available as an explicit mode (`what="center_distance"`) for callers that want it.
- [ ] `bbox`, `volume`, and `faces` state in their output whether the part transform was applied (today `clearance` applies it and the others do not).
- [ ] A test measures M4 hole spacing on the SPEC 9.1 bracket and asserts agreement with the parametric value within 0.1 mm.

## CAD-019 — Tool responses have no single envelope

> Tracked as [#20](https://github.com/MaximumTrainer/llm-cad/issues/20)

**Severity:** P1 · **Area:** LLM contract · **Spec:** §4 ("tool descriptions and error messages are written for an LLM audience"), N3 · **Files:** all of `src/cad_mcp/tools/`

**Problem.** The 14 tools return at least four different shapes:

- `execute_cad` — human-readable text on success (`"OK -- 1 solid(s)\nBounding box: …"`), **JSON** on argument errors.
- `validate_mesh` — a bare report dict for one part, `{"ok": true, "parts": [...]}` for several; the single-part path has no `ok` key at all.
- `measure`, `export_model`, part tools — `{"ok": …}` JSON.
- `ping`, `reset_session` — plain strings.

`tests/test_prompts_resources.py` already contains the tell: it checks `text.startswith("OK")` *and* falls back to parsing JSON, because the caller genuinely cannot know which it will get. Every one of these shapes is something the LLM must parse; inconsistency costs tokens and invites mis-parsing on the error path, which is exactly when reliability matters most.

**Acceptance criteria**

- [ ] One documented envelope for every tool: `{ok, summary, data?, error?: {type, message, line?, snippet?, hint?}}`, with `summary` a short human/LLM-readable line.
- [ ] `execute_cad`'s success text becomes `summary` inside the envelope; the LLM-facing wording is preserved.
- [ ] `validate_mesh` returns the same top-level shape for one part and for many.
- [ ] A single test parametrised over all registered tools asserts every response — success and failure — validates against the envelope schema.
- [ ] Adding a tool without conforming fails that test (ties into CAD-031's tool-scaffolding skill).
- [ ] SPEC 5.1's output column is updated to reference the envelope.

## CAD-020 — Prompts teach v1 only; assembly and `gen_ai_mesh` are untaught

> Tracked as [#21](https://github.com/MaximumTrainer/llm-cad/issues/21)

**Severity:** P1 · **Area:** Prompts · **Spec:** G6, 5.2, PLAN risk register · **Files:** `src/cad_mcp/prompts.py`

**Problem.** `design_workflow` describes execute → render → measure → validate → export and never mentions parts. Grepping `prompts.py` for `create_part`, `set_active_part`, `position_part`, `gen_ai_mesh`, or "assembly" returns nothing but coincidental matches in the printability text. Seven of the fourteen tools — all of v2 — have no pedagogy at all.

G6 is the whole reason prompts ship with this server: "teach the host — an LLM that has never seen CadQuery can still succeed". Half the surface area is now untaught, so the LLM will default to cramming multi-part designs into one `result` solid, which is the failure mode assembly support exists to remove. PLAN's risk register explicitly requires the workflow prompt to warn against parametric ops on AI meshes; it does not.

**Acceptance criteria**

- [ ] `design_workflow` gains an assembly section: when to split into parts, `create_part` → `set_active_part` → `execute_cad` → `position_part`, and the render/validate/export implications.
- [ ] The mandatory-render rule is restated for assemblies (render the whole assembly after positioning, not just the active part).
- [ ] `clearance` and the interference check are taught as the way to verify fits before export.
- [ ] `gen_ai_mesh` is covered with its hard constraints: organic shapes only, tessellated B-rep, **no fillet/shell/chamfer afterwards**, and the code-history caveat from CAD-022.
- [ ] The 16-part cap and its cost implications are stated.
- [ ] A test asserts each v2 tool name appears in at least one prompt — the same guard CAD-032 enforces.

## CAD-021 — Example resources are not discoverable

> Tracked as [#22](https://github.com/MaximumTrainer/llm-cad/issues/22)

**Severity:** P2 · **Area:** Resources · **Spec:** 5.3 · **Files:** `src/cad_mcp/resources.py`

**Problem.** The eight curated examples are exposed only through the templated URI `cad://examples/{name}`. Many MCP hosts list concrete resources but not template expansions, so from the LLM's side the examples are invisible unless it already knows a name. The names live in a hand-written description string *and* in `EXAMPLES.keys()`, which can drift apart.

**Acceptance criteria**

- [ ] A concrete `cad://examples` index resource lists every example with a one-line description of what it demonstrates.
- [ ] Each example is additionally registered as a concrete resource URI so template-blind hosts can enumerate them.
- [ ] The description string is generated from `EXAMPLES`, not hand-maintained.
- [ ] The unknown-name response returns a structured error rather than a Python comment string.
- [ ] The existing "every example executes" test is retained and extended to assert each example also renders and validates as watertight.

## CAD-022 — `gen_ai_mesh` breaks the code-history invariant

> Tracked as [#23](https://github.com/MaximumTrainer/llm-cad/issues/23)

**Severity:** P1 · **Area:** AI mesh · **Spec:** §8 ("code history as state"), 10.2 M7 · **Files:** `src/cad_mcp/tools/gen_ai_mesh.py`, `src/cad_mcp/session.py`

**Problem.** SPEC §8 makes code history the source of truth and the BREP a cache. `gen_ai_mesh` writes a BREP that **no code can reproduce**, and records a Python *comment* in the history. The consequences:

1. A subsequent `execute_cad(mode="append")` replays the history — a comment plus the new code — and the sandbox overwrites the BREP. The generated mesh is silently destroyed, with no signal to the LLM.
2. `execute_cad(mode="replace")` does the same with no warning.
3. `part.bbox` is set to `None`, so `list_session`/`list_parts` report no bounding box for a part that has geometry.
4. Nothing recomputes the solid count or bbox from the imported mesh.

**Acceptance criteria**

- [ ] `Part` gains an explicit provenance field (`source: "cadquery" | "ai_mesh"`); the BREP is only treated as replayable when `source == "cadquery"`.
- [ ] `execute_cad` on an `ai_mesh` part returns a structured error explaining that the part's geometry is not code-reproducible, and names the options: target a different part, or `reset`/`create_part` first.
- [ ] The GLB is retained in the session dir and referenced by the part, so the mesh can be re-imported rather than lost.
- [ ] `gen_ai_mesh` computes and stores `bbox` and solid/face counts, matching what `execute_cad` reports.
- [ ] `list_session` shows AI-mesh parts with their prompt and task id in place of code.
- [ ] The `design_workflow` prompt states the constraint (see CAD-020).

## CAD-023 — Exports land in a temp dir that reset silently destroys

> Tracked as [#24](https://github.com/MaximumTrainer/llm-cad/issues/24)

**Severity:** P1 · **Area:** Export · **Spec:** G4 ("print-ready and CAD-ready output") · **Files:** `src/cad_mcp/tools/export_model.py`, `src/cad_mcp/session.py`, `README.md`

**Problem.** `export_model` writes to `{session.tmpdir}/output/`, a `mkdtemp` directory under the system temp root. `reset_session` calls `shutil.rmtree(tmpdir)` — deleting every exported STL the user just asked for — and the OS reclaims the directory eventually anyway. The tool returns a path, so a human *can* copy the file out, but the artefact the whole workflow exists to produce is stored in the one location guaranteed to be thrown away. G4's promise of print-ready output is not met end to end.

**Acceptance criteria**

- [ ] Exports go to a durable, configurable directory — `CAD_MCP_OUTPUT_DIR`, defaulting to a documented per-user location (e.g. `./cad-mcp-output/` under the launch cwd) — not the session tmpdir.
- [ ] The output directory is created on demand and its absolute path appears in the tool response and in `list_session`.
- [ ] `reset_session` never deletes exported files; its response says which exports were kept and where.
- [ ] Filename collisions are handled deterministically (documented suffixing), not by silent overwrite.
- [ ] The path guard from CAD-003 applies to the new directory.
- [ ] README documents where exports land and how to change it; SPEC 5.1's "session output dir" wording is updated.

---

# P2 — Process, speed, hygiene

## CAD-024 — No CI for tests, lint, or types

> Tracked as [#25](https://github.com/MaximumTrainer/llm-cad/issues/25)

**Severity:** P1 · **Area:** CI · **Spec:** PLAN Phase 2/5 gates · **Files:** new `.github/workflows/ci.yml`

**Problem.** `.github/workflows/` contains one file: a GitHub Pages deploy. Nothing runs `pytest`, `ruff`, `mypy`, or `scripts/smoke.py` on push or PR. PLAN's gates say the matplotlib fallback is "tested in CI where EGL is absent" and the smoke script is "green in CI" — neither CI exists. Every quality claim in this repo currently rests on a developer remembering to run things locally on Windows.

**Acceptance criteria**

- [ ] A CI workflow runs on push and PR: `uv sync`, `ruff check`, `mypy --strict`, `pytest`, `python scripts/smoke.py`.
- [ ] Matrix covers Linux and macOS at minimum (SPEC N5's supported platforms); Windows included, or its absence justified in the workflow file.
- [ ] One job runs with EGL unavailable to exercise the matplotlib fallback; one job exercises the pyrender path (CAD-011).
- [ ] A clean-install job (`uv sync --no-dev`) proves the declared dependencies are sufficient (CAD-012).
- [ ] Slow tests are separated so PR feedback arrives in a few minutes and the full suite runs on merge (CAD-025).
- [ ] Branch protection requires the workflow; any README badge reflects real status.

## CAD-025 — Test suite takes 8 minutes

> Tracked as [#26](https://github.com/MaximumTrainer/llm-cad/issues/26)

**Severity:** P2 · **Area:** Tests · **Files:** `tests/`, `pyproject.toml`

**Problem.** `uv run pytest` → **135 passed in 476.36s**. Nearly all of it is sandbox subprocess cold starts (~3.5 s each, and every geometry test does at least one) plus matplotlib rendering. At that length the suite stops being run before commits — precisely when it is most valuable — and it makes the git hooks in CAD-030 unusable unless the suite is split.

**Acceptance criteria**

- [ ] Markers separate `fast` (no sandbox, no render), `slow`, and `integration`; the default selection and marker semantics are documented in `pyproject.toml`.
- [ ] A `fast` subset runs in under 30 s and covers the envelope, validation, argument handling, and error paths via fixtures rather than live sandbox runs.
- [ ] Shared geometry fixtures are session-scoped so one bracket build serves many assertions.
- [ ] `pytest-xdist` (or equivalent) enabled; full-suite wall-clock reduced by at least 3× — measured before/after in the PR.
- [ ] CAD-014's worker reuse is reflected here; the two items should land together.

## CAD-026 — Structured logging is inert outside `main()`

> Tracked as [#27](https://github.com/MaximumTrainer/llm-cad/issues/27)

**Severity:** P2 · **Area:** Observability · **Spec:** N6 · **Files:** `src/cad_mcp/_logging.py`, `src/cad_mcp/tools/list_session.py`, `reset_session.py`, `ping.py`

**Problem.** Five defects against N6 ("structured logs per tool call … to stderr; never stdout"):

1. `cad_logging.setup()` is only called from `main()`. Under tests, the smoke script, or any embedding, the SDK's own handler takes over and output is Rich-formatted text, not JSON. Observed during this review: `[09/12/26 08:02:42] INFO  execute_cad completed in 3225.9ms  _logging.py:63`.
2. `setup()` appends a handler on every call, so a second call double-logs.
3. `session_id` is resolved by calling `get_or_create()` with no argument, so it is permanently `"default"` (CAD-007).
4. `list_session`, `reset_session`, and `ping` have no `@logged_tool` decorator — N6 says every tool call.
5. Nothing asserts the "never stdout" rule that PLAN's risk register calls out as a stdio-framing hazard.

**Acceptance criteria**

- [ ] `setup()` is idempotent and is applied for any entry point, including tests and the smoke script.
- [ ] Every registered tool emits exactly one structured line per call; a test iterates the registry and asserts it.
- [ ] Log lines carry the real session id and a per-call id.
- [ ] Failures log `error_type` and duration, not just `success: false`.
- [ ] A test runs the stdio server end to end and asserts **nothing** but MCP framing reaches stdout.

## CAD-027 — Meshy client has no download cap, drifting clock, no retry

> Tracked as [#28](https://github.com/MaximumTrainer/llm-cad/issues/28)

**Severity:** P2 · **Area:** AI mesh · **Spec:** 10.2 M3, M5 · **Files:** `src/cad_mcp/meshy.py`

**Problem.** Three issues in the API client:

1. `download_glb` does `resp.content` on an arbitrary URL with `follow_redirects=True` — the whole body lands in memory with no size limit and no content-type check.
2. `poll_until_done` increments `elapsed` by the sleep interval only, ignoring request latency. With slow responses, real elapsed time substantially exceeds the 120 s/180 s budgets M3 specifies.
3. No retry or backoff for transient 5xx or connection errors; a single blip fails the whole generation after the user has already spent credits.

**Acceptance criteria**

- [ ] Downloads stream to disk with a configurable maximum size (default stated in SPEC 10.2) and a content-type check; exceeding it is a structured error.
- [ ] Poll timeout is measured against a monotonic clock; a test with a slow mock transport asserts the tool gives up within the specified budget.
- [ ] Transient 5xx and connection errors are retried with bounded exponential backoff; 4xx is not retried; 429 keeps its existing hint and honours `Retry-After`.
- [ ] The download URL's host is validated against the expected Meshy domains before fetching.
- [ ] Mock tests cover each new path; no live API call is added to the unit suite.

## CAD-028 — Mesh→BREP sewing is unbounded

> Tracked as [#29](https://github.com/MaximumTrainer/llm-cad/issues/29)

**Severity:** P2 · **Area:** AI mesh · **Spec:** 10.2 M6, PLAN risk register · **Files:** `src/cad_mcp/mesh_to_brep.py`, `src/cad_mcp/tools/gen_ai_mesh.py`

**Problem.** `_sew_mesh` builds one `BRepBuilderAPI_MakePolygon` + `MakeFace` per triangle in a Python loop, then sews them all. At the 4 000-triangle default this is tolerable; `target_polycount` is caller-supplied and `refine=True` returns far denser meshes, so a single call can occupy the server for minutes and consume gigabytes — in-process, blocking, with no timeout (and see CAD-009: a sewing failure here can take the server down).

**Acceptance criteria**

- [ ] `target_polycount` is validated against the documented range (the docstring says 100–15 000; the code does not enforce it).
- [ ] Imported meshes above a stated triangle budget are decimated before sewing, and the response says the mesh was simplified and by how much.
- [ ] Sewing runs with a timeout and, ideally, out of process (shares CAD-009's mechanism); a timeout returns a structured error, not a hung tool.
- [ ] A test imports a large synthetic mesh and asserts the cap and the response wording.
- [ ] Peak memory for the worst permitted case is measured and recorded in README.

## CAD-029 — Dependency pins contradict CLAUDE.md

> Tracked as [#30](https://github.com/MaximumTrainer/llm-cad/issues/30)

**Severity:** P2 · **Area:** Packaging · **Files:** `pyproject.toml`, `CLAUDE.md`

**Problem.** CLAUDE.md requires "official `mcp` Python SDK **v2**, `MCPServer` (was `FastMCP` in v1)", but `pyproject.toml` pins `mcp[cli]>=1.9.0` — a range that resolves happily to a v1 without `MCPServer`, so a fresh resolve can fail at import. Separately, CLAUDE.md warns "don't upgrade cadquery/OCP pins casually — rendering and export are version-sensitive", yet `cadquery>=2.8.0` has no upper bound, so any resolve can pick up a breaking OCP. `uvicorn` and `starlette` are imported directly by `server.py` but only arrive transitively via the SDK.

**Acceptance criteria**

- [ ] `mcp` is pinned to a v2-compatible range consistent with the `MCPServer` API in use.
- [ ] `cadquery` (and OCP, if pinned directly) carry upper bounds; the CLAUDE.md warning becomes mechanically enforced rather than advisory.
- [ ] `starlette` and `uvicorn` are declared where imported directly.
- [ ] A documented procedure exists for bumping a geometry pin: run the golden tests, refresh goldens deliberately, record in the PR (this is the `pin-bump` skill in CAD-031).
- [ ] `uv.lock` is refreshed and the full suite passes against it in CI.

---

# Tooling — guiding design and implementation

## CAD-030 — No git hooks

> Tracked as [#31](https://github.com/MaximumTrainer/llm-cad/issues/31)

**Severity:** P1 · **Area:** Tooling · **Files:** new `.githooks/`, `scripts/install-hooks.ps1`, `scripts/install-hooks.sh`, `CONTRIBUTING.md`

**Problem.** `.git/hooks/` contains nothing but samples. With no CI either (CAD-024), nothing prevents a commit that fails `ruff`, `mypy --strict`, or the tests — despite CLAUDE.md stating both "must pass before any commit". PLAN's risk register names a specific hazard only a hook can catch cheaply: a stray `print()` in server code corrupts stdio MCP framing.

**Scope.** Repo-local hooks under `.githooks/`, enabled with `git config core.hooksPath .githooks`, installable by one command on Windows (PowerShell) and POSIX (bash) — the repo is developed on Windows and targets Linux/macOS, so both must work. Keep hooks fast; defer the 8-minute suite to pre-push and CI.

**Acceptance criteria**

- [ ] `pre-commit` runs, on staged files only: `ruff check --fix`, `ruff format --check`, `mypy --strict`, and the `fast` test subset (CAD-025) — completing in under ~20 s on a warm cache.
- [ ] `pre-commit` fails on any `print(` added under `src/cad_mcp/` outside `_sandbox_worker.py`, with a message citing the stdio-framing risk.
- [ ] `pre-commit` fails when a file under `src/cad_mcp/tools/` is added or renamed without a matching registration in `server.py` and a row in README's tool table (see CAD-032).
- [ ] `commit-msg` enforces the agreed convention and rejects an empty or single-word subject.
- [ ] `pre-push` runs the full test suite and `scripts/smoke.py`.
- [ ] `scripts/install-hooks.ps1` and `scripts/install-hooks.sh` both set `core.hooksPath`; `CONTRIBUTING.md` documents installation, what each hook enforces, and that `--no-verify` is permitted only for WIP branches never merged to `main`.
- [ ] Hooks are exercised in CI (running the same checks) so hook logic cannot rot unnoticed.

## CAD-031 — No Claude Code skills to guide design and implementation

> Tracked as [#32](https://github.com/MaximumTrainer/llm-cad/issues/32)

**Severity:** P1 · **Area:** Tooling · **Files:** new `.claude/skills/*/SKILL.md`, `.claude/settings.json`, `CLAUDE.md`

**Problem.** PLAN.md is written as a sequence of prompts pasted by hand, and CLAUDE.md's conventions ("one tool per file registered in `server.py`", "all execution through `sandbox.py`", "never return geometry blobs", "update SPEC.md before adding a tool") exist only as prose an agent may or may not honour. The evidence that prose alone is insufficient is this backlog: v2 shipped seven tools with no prompt coverage (CAD-020), four response shapes (CAD-019), a dead render path (CAD-013), and a security suite that tests nothing (CAD-005). Skills turn those conventions into procedures that run every time.

**Scope.** A small set of skills, each earning its place by encoding a rule this review found violated. Resist adding skills for anything a lint rule already covers.

- **`add-tool`** — scaffold a new MCP tool end to end: confirm SPEC.md covers it (refuse if not), create `src/cad_mcp/tools/<name>.py` from the house template, register in `server.py`, apply the CAD-019 envelope, add the README row, add prompt coverage per CAD-020, and generate the test skeleton including an error-path case.
- **`spec-guard`** — run before any change that alters tool surface, session state, or transport behaviour: diff the intent against SPEC.md and either point at the clause that authorises it or produce the SPEC.md edit for the same PR. This is PLAN.md's stated rule, made executable.
- **`sandbox-audit`** — the procedure for changing anything under `sandbox.py`/`_sandbox_worker.py`: enumerate the escape vectors from CAD-001/002/004, run the containment suite, require effect-based evidence in the PR. Refuses to sign off on `"OK" not in text`-style assertions.
- **`render-check`** — the procedure for touching `render.py`: render the reference bracket on every available backend, compare against goldens, check the N2 latency budget, and walk the deliberate regeneration path when a golden legitimately changes.
- **`pin-bump`** — CLAUDE.md's "don't upgrade cadquery/OCP casually" as a checklist: bump, re-lock, run golden export and render tests, diff STEP topology, record results (pairs with CAD-029).
- **`release-check`** — pre-tag gate: full suite, smoke script, clean-install job, SPEC acceptance criteria 9.1–9.4 re-verified, drift check clean.

**Acceptance criteria**

- [ ] Each skill lives at `.claude/skills/<name>/SKILL.md` with valid frontmatter (`name`, `description`), and a description stating its trigger precisely enough to fire unprompted.
- [ ] Each skill is a numbered procedure with commands to run and explicit refusal conditions — not a restatement of CLAUDE.md.
- [ ] `add-tool` is validated by using it to create one real tool (or a scratch tool then reverted), producing a file, a registration, a README row, prompt coverage, and tests with no manual fixups.
- [ ] `sandbox-audit` is validated by running it against the CAD-001 fix and catching at least one gap the author missed.
- [ ] `.claude/settings.json` carries the hook configuration backing the automated parts (e.g. a `PostToolUse` hook running `ruff` on edited Python files), with permissions scoped to this project.
- [ ] CLAUDE.md gains a short "Skills" section naming each skill and when it fires, so a fresh session discovers them.
- [ ] Skills do not duplicate the git hooks (CAD-030) — hooks enforce mechanically, skills guide judgement; the split is documented.

## CAD-032 — Nothing detects SPEC/README/code drift

> Tracked as [#33](https://github.com/MaximumTrainer/llm-cad/issues/33)

**Severity:** P2 · **Area:** Process · **Files:** new `tests/test_docs_consistency.py`, `CONTRIBUTING.md`, `SPEC.md`

**Problem.** Four descriptions of the tool surface exist — SPEC 5.1's tables, README's tool table, the registrations in `server.py`, and the prompts — and nothing checks they agree. They have already drifted: prompts omit seven tools (CAD-020), `create_part`'s default colour is `"steel"` in SPEC and `""` in code, and SPEC 5.1 lists seven tools where the server registers fourteen (the v2 tables live in §10, so no single list is authoritative).

**Acceptance criteria**

- [ ] A test asserts the set of registered tool names equals the set documented in README's table.
- [ ] A test asserts every registered tool name appears in at least one prompt, or is explicitly listed as exempt with a reason.
- [ ] A test asserts every registered tool has a docstring with an `Args:` section covering each parameter.
- [ ] SPEC.md gains one consolidated tool table (v1 + v2) that the same test checks against.
- [ ] Defaults appearing in both SPEC and code (part colour, `min_wall_mm`, `max_overhang_deg`, timeouts, poll intervals, part cap) are asserted equal — or sourced from one constant.
- [ ] `CONTRIBUTING.md` states the definition of done: SPEC updated, tests added, prompts updated, README row, envelope conformed, hooks green.

## CAD-033 — No `.mcp.json`, so the documented connect step is manual

> Tracked as [#34](https://github.com/MaximumTrainer/llm-cad/issues/34)

**Severity:** P2 · **Area:** DX · **Spec:** 9.4 ("fresh-machine setup to first render in under 10 minutes") · **Files:** new `.mcp.json`, `README.md`

**Problem.** PLAN.md's "connecting the finished server" section offers `claude mcp add cad-mcp -- uv run cad-mcp` *or* adding to `.mcp.json`, and the repo ships neither. Anyone cloning it must read the README and hand-edit host config before the first render — friction against acceptance criterion 9.4, and an easy place to get the command wrong.

**Acceptance criteria**

- [ ] `.mcp.json` at the repo root configures the stdio server so a cloned checkout connects with no manual editing.
- [ ] README's quick start leads with it, keeping the manual snippets for other hosts.
- [ ] A documented HTTP-transport variant is included (commented, or as a second entry).
- [ ] The 9.4 setup path is timed on a clean machine and the result recorded in README.

---

## CAD-034 — Common CadQuery errors return no hint, so the LLM loops

> Tracked as [#35](https://github.com/MaximumTrainer/llm-cad/issues/35)

**Severity:** P1 · **Area:** Sandbox · **Spec:** N3, G6 · **Files:** `src/cad_mcp/_sandbox_worker.py`

**Problem.** `_add_hint()` carries four rules — fillet, chamfer, `brep_api ... command not done`, and selector/"does not exist". None match the errors an LLM actually hits most often, so those failures return an exception type and a line number with **no hint**, against SPEC N3's what/where/hint requirement.

**Evidence.** Surfaced by the live LLM test added on 2026-09-12 (`tests/test_llm_integration.py`), the first test to put a real model in the loop. In one run the model made 16 tool calls and never recovered; `Cannot find a solid on the stack or in the parent chain` fired **seven times** with no hint attached, leaving the model no new information between attempts. Also uncovered: `AttributeError` on hallucinated methods (`pushToTop`), selector `ParseException`, and escape-character `SyntaxError` — all hintless.

**Scope.** Extend `_add_hint()` to cover the common failure classes with prescriptive text, and restructure it as a matcher table so rules are cheap to add. Full rule table in [#35](https://github.com/MaximumTrainer/llm-cad/issues/35).

**Acceptance criteria**

- [ ] Each common error class (no-solid-on-stack, unknown `Workplane` attribute, selector `ParseException`, escape-character `SyntaxError`, `TypeError` on a Workplane method) returns a hint; one unit test per rule.
- [ ] The `AttributeError` hint includes near-match suggestions via `difflib.get_close_matches` against the real `Workplane` API.
- [ ] A regression test enforces hint coverage rather than leaving it best-effort.
- [ ] `_add_hint` becomes a table of (matcher, hint) pairs, unit-tested directly.
- [ ] The live LLM acceptance test recovers from a `Cannot find a solid` error within the SPEC 9.1 iteration budget on the default model.

---

## Suggested sequencing

1. **Stop the bleeding, honestly** — CAD-005 first (make the tests able to detect failure), then CAD-001, CAD-002, CAD-004, CAD-003, CAD-006. Do not ship the current SPEC 9.3 claim in the meantime; amend SPEC.md the moment the posture is decided.
2. **Make state correct** — CAD-007, CAD-008, then CAD-009.
3. **Put guardrails under the work** — CAD-024, CAD-030, CAD-031, CAD-025. These make everything after them cheaper, and they are what keeps items 1–2 from regressing.
4. **Repair the core loop** — CAD-019, CAD-020, CAD-010–CAD-014, CAD-023, CAD-022.
5. **Sharpen the measurements** — CAD-015, CAD-016, CAD-017, CAD-018.
6. **Hygiene** — CAD-012, CAD-021, CAD-026–CAD-029, CAD-032, CAD-033.
