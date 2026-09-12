---
name: release-check
description: Pre-tag verification gate for cad-mcp — full suite, smoke, clean install, SPEC 9.1-9.4 acceptance criteria, and docs/code drift. Use before tagging a release or declaring the project done.
---

# Release check

Run all of it. A failure anywhere is a blocker, not a note.

## 1. Static

```bash
uv run ruff check .
uv run mypy
```

## 2. Tests, on a clean tree

```bash
uv run pytest -q
uv run python scripts/smoke.py
```

## 3. Clean install — declared deps must suffice

```bash
uv sync --no-dev && uv run python scripts/smoke.py
```

This catches imports that only resolve transitively. `matplotlib`,
`scipy` and `pillow` all arrived via `cadquery -> vtk` once, so a VTK
change would have broken rendering and validation at runtime.

## 4. SPEC acceptance criteria, re-verified

| Criterion | How |
|---|---|
| 9.1 bracket → watertight STL in ≤4 LLM iterations | `CAD_MCP_LLM_ACCEPTANCE=1 uv run pytest -m llm -k spec_9_1` — a real model, not replayed example code |
| 9.2 `measure` within 0.1mm | `uv run pytest -k measure` |
| 9.3 containment | `uv run pytest tests/test_sandbox_containment.py -v` |
| 9.4 fresh-machine setup < 10 min | time it on a clean checkout; record the number |

## 5. Drift

```bash
uv run pytest tests/test_docs_consistency.py -q
```

Registered tools == README table == SPEC table == prompt coverage.

## 6. Confirm the honesty of the claims

Re-read SPEC N1, N2, N4 and check each is still true of the code:

- N1: does the containment suite still assert effects, not wording?
- N2: do the perf tests still enforce the budgets?
- N4: is determinism proven across processes, for the formats SPEC
  actually claims?

If a claim is no longer true, fix the code or fix the SPEC before
tagging. Shipping a false guarantee is worse than shipping a known gap.
