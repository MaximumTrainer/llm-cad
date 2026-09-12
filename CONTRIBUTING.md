# Contributing to cad-mcp

## Setup

```bash
uv sync --all-extras
./scripts/install-hooks.sh        # POSIX
# or
pwsh scripts/install-hooks.ps1    # Windows
```

Installing the hooks is not optional for regular contributors — they are
what keep `ruff`, `mypy --strict`, and the stdio-framing rule enforced
between CI runs.

## Definition of done

A change is done when **all** of these are true:

- [ ] **SPEC.md updated** if the change touches tool surface, session
      state, transport, sandbox guarantees, determinism, or latency.
      Deviating from SPEC without editing SPEC is the one rule this
      project has had since PLAN.md was written.
- [ ] **Tests added**, and they can actually fail. For anything
      security-shaped, assert the observable effect, not the error text.
- [ ] **Prompts updated** if you added or changed a tool — an untaught
      tool is an unused tool (SPEC G6).
- [ ] **README tool table** has a row for any new tool.
- [ ] **Response envelope** conformed to (`cad_mcp.envelope`), so the LLM
      sees one shape across all tools.
- [ ] `uv run ruff check . && uv run mypy && uv run pytest -q` green.
- [ ] Hooks green without `--no-verify`.

## The rules that exist because they were broken

| Rule | What went wrong |
|---|---|
| No `print()` in server code outside `_sandbox_worker.py` | stdout is the stdio MCP framing channel |
| Never `session.get_or_create()` with no argument | 14 call sites shared one global session across all HTTP clients |
| Security tests assert effects, not wording | `assert "OK" not in text` passed while the sandbox was wide open |
| Every file write goes through `cad_mcp.paths` | `filename="../../x"` escaped the session directory |
| Test the tool, not the helper | the golden-image test guarded a function the tool never called |
| Declare what you import | `matplotlib`, `scipy`, `pillow` arrived only via `cadquery -> vtk` |

## Skills

`.claude/skills/` holds procedures for the recurring, easy-to-botch jobs:
`add-tool`, `spec-guard`, `sandbox-audit`, `render-check`, `pin-bump`,
`release-check`. Hooks enforce mechanically; skills guide judgement.
Use them rather than working from memory.

## Test markers

```bash
uv run pytest -q                      # default: everything except live LLM tests
uv run pytest -m "not slow" -q        # fast feedback
uv run pytest -m llm -v               # live OpenRouter tests (needs a key)
```
