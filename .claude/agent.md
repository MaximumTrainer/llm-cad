# Working agreement for agents on cad-mcp

`CLAUDE.md` says what this project *is* and which commands to run. This
file says how work is expected to be *done* — the five standing habits
from issue #37, each with the mechanism that enforces it, because a
practice nothing checks is a preference rather than an agreement.

Read `CLAUDE.md` first; nothing here repeats it.

---

## 1. Clean, fluent code design

The measure is whether the next reader learns something from the code
rather than from the diff that produced it.

- **Name the intent, not the mechanism.** `geometry.POOL.is_ready()`
  says what a caller wants to know. `_worker_proc_poll_is_none()` would
  say how it happens to be answered today.
- **One reason to exist per module.** `sandbox.py` isolates code we do
  not trust; `geometry.py` isolates a kernel that cannot raise. They look
  similar and are not the same thing, and SPEC §7 says so out loud.
  Resist merging things that merely resemble each other.
- **Fluent where the domain is fluent.** CadQuery chains, and model code
  should read like the chain it builds. Server code is not fluent for its
  own sake: a builder that exists only to avoid a keyword argument is
  noise.
- **Comments carry the reason, never the restatement.** `# scales with
  cpu_count because RLIMIT_NPROC counts threads` earns its line. `#
  increment i` does not. Every non-obvious constant in this repo should
  be traceable to a measurement or a SPEC clause.
- **Errors are a designed surface.** SPEC N3 requires every failure to
  return what, where and a hint. A bare traceback reaching the model is a
  bug in the same way a wrong dimension is.

Enforced by `ruff`, `mypy --strict`, and the `pre-commit` hook's
`print()` check — stdout belongs to the MCP stdio framing, so server code
logs to stderr via `cad_mcp._logging`.

## 2. Outside-in TDD

Drive each change from the outermost failing test inwards: acceptance
red → contract red → unit red → green. One commit per step, so the
history is evidence of the sequence rather than a claim about it.

The vendored **`outside-in-tdd`** skill is the procedure. Use it; do not
reconstruct it from memory.

Two project-specific notes:

- The acceptance layer here is usually `tests/test_acceptance.py` or
  `scripts/smoke.py` — the real core loop, not a mock of it.
- Never assert a security guarantee by the wording of an error. Assert
  the observable effect: no file, no connection, no surviving process.
  `sandbox-audit` refuses wording-only assertions for this reason.

## 3. Documentation moves with the code

A change is not finished when the tests pass. It is finished when
`SPEC.md`, `README.md`, `docs/` and the prompts describe what the code
now does.

- **SPEC first when the change is a deviation.** `spec-guard` is
  mandatory before altering tool surface, session state, transport
  behaviour, sandbox guarantees, determinism or latency. If the
  implementation cannot meet a stated guarantee, the SPEC must be
  corrected — never quietly left overstating the truth.
- **`docs/guide.html` is a hand-maintained twin of the README.** It
  drifts silently unless updated in the same change.
- `tests/test_docs_consistency.py` already fails the build for a tool
  with no README row, a README row for a tool that does not exist, or a
  default that disagrees between SPEC and code. Extend it rather than
  relying on review to notice.

The vendored **`docs-drift-guard`** skill covers generated-versus-twin
documents and how to verify a docs claim against source instead of
trusting the prose.

## 4. Use the skills registry

Before writing any new skill, runbook or repeated procedure, check the
shared catalogue at
[MaximumTrainer/agent-skills](https://github.com/MaximumTrainer/agent-skills):

```bash
python3 .claude/skills/skill-exchange/scripts/skills.py list
python3 .claude/skills/skill-exchange/scripts/skills.py status
python3 .claude/skills/skill-exchange/scripts/skills.py pull <name>
```

Vendor what already solves the problem; send genuinely general
improvements back so the other repositories get them too. See
`.claude/skills/skill-exchange/` for the contribution workflow.

Project-local skills — `add-tool`, `spec-guard`, `sandbox-audit`,
`render-check`, `pin-bump`, `release-check` — encode judgement that only
makes sense here. Hooks enforce mechanically; skills guide judgement. Use
both.

## 5. Git hooks for hygiene

```bash
./scripts/install-hooks.sh      # or scripts/install-hooks.ps1
```

| Hook | What it refuses |
|---|---|
| `commit-msg` | Empty, single-word, over-72-character, or contentless subjects (`wip`, `fix`, `stuff`). |
| `pre-commit` | `ruff` failures, `mypy --strict` failures, a stray `print()` in server code, a failing fast tier, and a new tool that is unregistered or undocumented. |
| `pre-push` | `mypy` on all three platforms, the full suite, and the end-to-end smoke run. |

`--no-verify` belongs on a WIP branch that will never reach `main`, and
nowhere else. A hook that is routinely bypassed should be fixed or
deleted, not tolerated.

---

## The loop these habits protect

```
describe → LLM writes CadQuery → execute_cad → render_views (PNGs back)
        → the model critiques its own render → revise → validate_mesh
        → export_model
```

The visual feedback loop is the product. Renders slower than ~2s, or
images that stop arriving as MCP content blocks, are regressions no
matter how clean the code that caused them.
