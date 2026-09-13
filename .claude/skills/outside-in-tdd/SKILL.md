---
name: outside-in-tdd
description: Drive a change test-first from the outside in — acceptance test red, then contract/API red, then unit red, then the green implementation — with one commit per step so the history proves the sequence. Use whenever implementing a feature, fixing a bug, or starting work on an issue number in a repository that expects TDD, and whenever asked to "do issue N", "implement X test-first", "red-green-refactor", or "work through the backlog". Also use when reviewing whether a change was actually driven by tests.
license: MIT
metadata:
  version: "1.0.0"
---

# Outside-in TDD

Write the test that describes the behaviour you want, watch it fail for the right reason, then write the least code that makes it pass. Work from the outermost observable behaviour inward, so the design is pulled into existence by a caller that already exists rather than guessed at in advance.

The point is not test coverage. The point is that you cannot write a test for a design you have not yet understood, so the test forces the understanding to happen first. A test written after the code tests the code you wrote; a test written before it tests the behaviour you wanted.

## The rings

Start at the outermost ring you can automate, and move inward only as far as the current failing test forces you.

```
ring 1  acceptance criterion (from the issue)       ─ red ─┐
ring 2  end-to-end / journey / simulator                   │  each ring's failing
ring 3  contract, API or component test                    │  test justifies the
ring 4  unit test over a pure function            ─── green┘  next ring inward
```

Which ring you *enter* at depends on the change:

| Change | Enter at |
|---|---|
| New user-visible capability | Ring 1/2 — the journey, then work in |
| New endpoint, message or CLI command | Ring 3 — the contract, then the use case |
| New rule inside existing plumbing | Ring 4 — the pure function, no outer test needed |
| Bug fix | The ring that *should* have caught it. If none would have, that is the finding. |
| Refactor | No new test. Existing tests must stay green throughout. |

If you cannot name the ring, you do not yet know what you are changing.

## The sequence

Each step is its own commit. The commit history is the evidence that the sequence happened, so do not squash it away, and do not write the steps out of order and reorder the commits afterwards.

1. **Red acceptance.** Translate the acceptance criterion (Gherkin, Given/When/Then, or a plain sentence) into a test. Run it. Confirm it fails for the right reason. Commit.
   `test: add red acceptance test for <thing> (#N)`
2. **Red contract or API test.** The REST/GraphQL/consumer-contract test for the surface the acceptance test needs. Run, confirm red, commit.
   `test: add red API test for <thing> (#N)`
3. **Red unit tests.** Domain rules as plain unit tests; use cases with faked ports. Commit red unless the issue's plan explicitly allows folding these into step 4.
4. **Green.** Write the minimum implementation until every suite passes. Commit.
   `feat: <what now works> (#N)`
5. **Refactor.** Improve naming, structure and duplication with the suite green the whole time. Behaviour must not change.

One criterion, one red test, one green, one commit is the target rhythm. Do not batch: a change that adds five classes and one test at the end will be sent back, because nothing in it shows which test drove which class.

## Red for the right reason

This distinction does most of the work, and it is the step most often skipped.

**Red for the right reason** — the behaviour genuinely does not exist yet:

- a step definition is undefined
- a function, class or module does not exist
- the code does not compile (a compilation failure counts as red)
- the assertion fails because the value produced is the old behaviour

**Not red for the right reason** — fix the test, then commit it:

- a typo in the expected value
- a test-harness or fixture wiring error
- a missing environment variable
- an import path mistake

If you did not watch the test fail, you do not know that it tests anything. A test that has never failed proves only that it runs.

## Committing red is expected

A red commit is not a broken build to be hidden; it is the record of the step. Hooks in a repo that expects this workflow deliberately run formatting and static analysis on commit but **never tests**, precisely so that committing red is possible without bypassing anything.

Never reach for `--no-verify`. If a hook fails, the hook is right. Fix the cause.

## Test doubles

- **Fake at the seam, not below it.** Double the port you own, not the vendor type behind it. A fake that records `createField`/`setData` calls in order is testable; an attempt to fake the vendor SDK's own class is not.
- **Inject, don't fetch.** Domain objects take configuration as constructor arguments. Only the application layer reads settings, the environment, the clock or the random source. This is what makes the domain runnable without a database, a radio or a network. See `hexagonal-architecture`.
- **Prefer an in-memory implementation of the port** over a mocking framework for anything stateful. `InMemoryFlowRepository` implementing `IFlowRepository` reads better than five `when(...).thenReturn(...)` lines, and it cannot drift from the interface because the compiler checks it.
- **Prefer a new pure function over a new stateful class**, and prefer passing a value in over reading a property inside. Both make the next test easier to write.

## How tests should read

Name the behaviour, not the method. The test name is documentation that cannot go stale:

```kotlin
@Test
fun `create flow with duplicate name throws ConflictException`() { … }
```

Where the repo uses requirement ids, put the id in the test name so a reader can trace a test back to the requirement that asked for it.

Keep tests deterministic and isolated: a fixed reference date, no `Math.random`, no dependence on the real weekday or the local timezone, no ordering dependency between tests. A suite that fails on Tuesdays or in another timezone will be ignored, and an ignored suite is worse than no suite. See `api-quirk-fixtures` for fixture discipline.

## Definition of done

- [ ] Every acceptance criterion is a passing test, or a documented manual step whose result is recorded honestly
- [ ] Each test failed before its implementation existed, and you saw it fail
- [ ] The full suite is green, and the linters and type-checker are clean
- [ ] No test was weakened, skipped or deleted to get to green
- [ ] No `--no-verify`, no new lint suppressions, no `any`, no unexplained cast
- [ ] Boundaries respected: no business logic in a controller, no adapter type in the domain
- [ ] Negative tests exist wherever the path is security-sensitive
- [ ] Docs updated if behaviour, architecture or configuration changed — see `docs-drift-guard`
- [ ] The criteria are ticked because they are true, not because you have stopped working

## When something is genuinely untestable

Some things cannot be automated in some ecosystems: real Bluetooth stack behaviour, screen layout, hardware timing, a vendor SDK that needs an interactive login. That is a legitimate reason to have no test — and a reason to be explicit, never a reason to go quiet.

Say so in the pull request, and replace the test with a written manual verification step and its actual result. "Not run — no hardware available" is an acceptable line. A fabricated green is not. See `gap-issue` for filing the gap so it is tracked rather than forgotten.

## Related skills

- `hexagonal-architecture` — the structure that makes the inner rings fast and the doubles honest
- `spec-by-example-issue` — turning an issue into the criteria this workflow consumes
- `verify-and-ship` — running the full gate and landing the work once it is green
- `test-theatre-audit` — finding tests that pass while asserting nothing
- `container-integration-tests` — when the ring needs a real database or emulator
- `gap-issue` — recording what was deliberately left unverified
