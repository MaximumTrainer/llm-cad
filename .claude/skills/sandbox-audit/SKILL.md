---
name: sandbox-audit
description: Required procedure for any change to sandbox.py, _sandbox_worker.py, or _sandbox_policy.py in cad-mcp. Enumerates the escape vectors, demands effect-based evidence, and refuses wording-only test assertions.
---

# Sandbox audit

This code is the only thing between LLM-authored Python and the host.
A previous version passed its entire security suite while a filesystem
write to the user's home directory and a raw `_socket` both succeeded.

## 1. Know what is claimed

Read SPEC N1. Two layers, and they are not equivalent:

- **OS-enforced** (hard guarantees): timeout, memory, file size, process
  count, process-tree kill.
- **In-process** (defence in depth): filesystem confinement, network,
  process creation, import policy.

If your change weakens either, SPEC N1 changes in the same commit — see
the `spec-guard` skill.

## 2. Re-run every vector

```bash
uv run pytest tests/test_sandbox_containment.py -v
```

Vectors that must stay closed:

| Vector | Must fail with |
|---|---|
| write outside session dir | `SandboxViolation`, and no file at the target |
| read outside session dir | `SandboxViolation` |
| `os.open` / `pathlib` write outside | blocked, no file created |
| write inside session dir | must still SUCCEED |
| `import socket` / `import _socket` | `ImportError` |
| connect to a local listener | listener accepts nothing |
| `import subprocess` | `ImportError` |
| `importlib.import_module("subprocess")` | `ImportError` |
| `os.system` | blocked, command did not run |
| `os.fork` | blocked |
| infinite loop | `TimeoutError`, no surviving process |
| memory bomb | `MemoryError`, not the wall-clock timeout |

## 3. Evidence rules — refuse anything less

- **Reject** `assert "OK" not in text`, and any assertion on error
  wording alone. Assert the specific `error_type` AND the observable
  effect (no file, no connection, no process).
- Every payload must assign a valid `result`, so a successful escape
  reports `ok=true` and fails the test. Without this, a payload can
  "fail" at the result-type check *after* the escape already succeeded —
  which is exactly how the original suite stayed green.
- Prove the test can fail: run it against the pre-fix code, or
  temporarily disable the guard, and record that it failed.
- A platform-specific test is `skipif`-marked with a reason. Never
  silently vacuous.
- Never write an unbounded fork bomb. Bound the attempt.

## 4. Check the blast radius

- Does the change let library chatter reach stdout? The result channel is
  a dedicated file for exactly this reason ("VTK not installed" once
  corrupted the result protocol).
- Does a new deny-list entry break CadQuery's lazy imports? Run the full
  suite, not just the containment tests.
- Memory caps must sit above CadQuery's ~600MB import footprint, or the
  worker dies before it can report anything useful.

## 5. Report honestly

State which guarantees are OS-enforced and which are best-effort. Do not
describe the in-process layer as a jail for hostile code — it shares an
interpreter with the code it constrains.
