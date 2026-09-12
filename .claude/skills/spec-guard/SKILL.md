---
name: spec-guard
description: Check a proposed change against SPEC.md before implementing it, and produce the SPEC edit when the change is a genuine deviation. Use before changing tool surface, session state, transport behaviour, sandbox guarantees, determinism, or latency budgets in cad-mcp.
---

# SPEC guard

PLAN.md's rule: "If Claude Code proposes deviating from SPEC.md, make it
update SPEC.md in the same PR." This makes that mechanical.

## When this fires

Any change to: the set of tools or their arguments; session state or
lifetime; transport/auth behaviour; what the sandbox guarantees; export
formats or determinism; latency budgets; prompts or resources.

## Procedure

1. **Locate the clause.** Grep SPEC.md for the feature. Quote the exact
   clause the change touches (G1–G6, N1–N6, 5.1–5.3, 9.x, 10.x).
2. **Classify:**
   - *Implements* an existing clause → proceed, cite the clause in the
     commit message.
   - *Contradicts* a clause → **stop**. Write the SPEC.md edit first,
     show it, and get agreement before touching code.
   - *Not covered* → **stop**. SPEC.md gains the requirement first.
3. **Never weaken a claim silently.** If the implementation cannot meet
   a stated guarantee, the SPEC must say what is actually guaranteed.
   Precedent: N1 claimed a sandbox that did not confine the filesystem,
   and §9.3 claimed a containment suite that tested nothing.
4. **Check the dependents.** A SPEC change usually needs README, the
   prompts, and `tests/test_docs_consistency.py` updated too.

## Refusal conditions

Refuse to proceed, and say why, if:
- the change adds a tool absent from SPEC.md (see `add-tool`);
- it relaxes N1/N4/N2 guarantees without a SPEC edit in the same change;
- it would make a shipped acceptance criterion untrue.
