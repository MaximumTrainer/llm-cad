---
name: docs-drift-guard
description: Keep documentation, generated websites and hand-maintained HTML twins truthful about the code, and add a check that fails the build when they drift. Use when changing anything under docs/, when a code change needs a documentation update, when asked why a website is stale or how it is published, when a README describes a removed feature, and when adding a new document to a generated site. Covers generate-from-source versus hand-maintained twins, what must never go into a generated page, and verifying a docs claim against the source rather than trusting the prose.
license: MIT
metadata:
  version: "1.0.0"
---

# Keep documentation from lying

Documentation that restates what the code does will eventually contradict it, and a contradiction is worse than an omission: a reader who finds the README wrong stops trusting the parts that are right. Two user-visible bug reports in these projects turned out to be documentation defects, not calculation defects — the number in the tooltip and the number in the code had drifted apart.

There are exactly two sustainable strategies. Pick one per document, and know which one you are in.

| Strategy | How it stays true | Use for |
|---|---|---|
| **Derived** — the page is generated from a source | A drift check fails the build when the page and its source disagree | Reference material: API docs, ontologies, ADR indexes, config tables, CLI help |
| **Twinned** — the page is hand-written and verified | A checklist re-validated against the source on every relevant change | Narrative material: user guides, landing pages, tutorials |

Everything else — a hand-written page that restates a generated fact, with nothing checking it — is drift waiting to be discovered by a user.

## Derived pages: generate, never edit

If a generator exists, nothing under its output directory is written by hand.

**If you find yourself editing a generated file, stop: edit its source instead.** The next `generate` run will silently discard your edit, or the drift check will fail and you will not know why.

A typical mapping — keep it in one place in the generator, not spread across the site config:

| Source | Page |
|---|---|
| `README.md` | `/` |
| `docs/ONTOLOGY.md`, `docs/TESTING.md` | `/guide/<name>` |
| `docs/adr/*.md` | `/adr/<file>` plus a generated index |
| `resources/ontology/v1/ontology.json` | `/reference/ontology` |
| `docs/api/openapi.json` | `/reference/api` |

Adding a document then means adding one line to the mapping and one sidebar entry. Nothing else.

```bash
cd website
npm ci             # first time only
npm run generate   # rewrite the output tree from the sources
npm run drift      # what the hook and CI run
npm run verify     # drift, unit tests, build, browser tests
```

A good drift check fails in **both** directions:

1. A generated page that is out of date with its source.
2. A page in the output tree that no source generates — an orphan, usually a hand-edit that survived.

### What must never go into a generated page

The drift check only works because every generated file is a pure function of its sources. Anything non-deterministic breaks it.

- **No timestamps.** A build time in a generated page makes the check fail on every run.
- **No commit SHA, build number or version string** baked into the output tree. Read these at build time in the site config, outside the checked tree.
- No content pulled from a network call at generate time.

If a page genuinely needs the SHA or the build date, render it in the layout at build time, not into the generated source.

### Generated documents exported from a running application

An OpenAPI document, a JSON schema or a CLI reference is often exported by running the app, not written:

```bash
cd backend
./gradlew integrationTest --tests "*OpenApiExportTest*" -DupdateOpenApi=true
```

Then regenerate the site. The build should fail if the committed document drifts from what the running application serves, so this step is not optional — and the ordering matters: export first, generate second.

## Twinned pages: hand-maintained, verified against source

Where a document has a hand-maintained HTML twin — a GitHub Pages landing page, a user guide — the twin will drift unless someone re-checks it. Two rules:

**Do not mangle the page.** Edit the specific block, preserving the surrounding structure, classes and formatting. Do not regenerate a hand-written HTML page from its Markdown sibling; they have diverged on purpose, and a wholesale rewrite loses the layout.

**Do not trust the existing prose.** It has been wrong before. Verify each claim against the source:

- Cross-check every UI claim — menu paths, tab and button labels, settings categories, keyboard shortcuts — against the UI definition files and handlers. Grep the source; do not guess and do not copy the old sentence.
- Cross-check build commands against the workflow files: the actual toolchain version, the modules, the invocation.
- Cross-check configuration tables against the single place that reads the environment. See `single-source-constants`.
- **Do not describe removed or dormant features.** A guide that documents a feature slated for removal teaches users something you are about to take away. Check the removal list before writing.
- Regenerate screenshots when the UI changed, rather than leaving an old image beside new prose. See `screenshot-verify`.

## Which documents exist, and keeping the list short

Enumerate the user-facing documents once, and give each a scope, so the same content is not maintained in two places:

- **README** — what it is, install, build from source, how to run the tests. Links to the guide for how to *use* the thing; does not duplicate it.
- **Landing page** — features, gallery, download, licence.
- **User guide** — the full how-to.
- **ADRs** — the decisions and their context. Append-only; supersede, never edit.
- **`.env.example` and the configuration table** — every configuration value, with its default.

Where two documents must both mention a fact, one of them describes the *shape* and points at the other, rather than restating the value. A README that says "the bands are defined in `STRENGTH_ZONE_BANDS`" cannot drift. A README that says "Tired is −10 to −20" can, and did.

## Add the check

A convention in a document will drift; a failing build will not. In rough order of value:

1. A **drift check on generated output**, run by CI and by a pre-commit hook.
2. A **link checker** over the docs tree, so a renamed page is caught.
3. A **code-example check** — extract fenced blocks from the docs and compile or run them, or reference them into the docs from real test files so they cannot rot.
4. A **required checklist item** in the PR template for the twinned pages, naming them explicitly.

## Publication

Publish from a run that passed, not from a push. A website workflow triggered by the **CI run** — running only when that run concluded `success` on the default branch, and downloading the artefact CI already built rather than rebuilding — guarantees that what is published is what was tested.

After a merge, confirm it:

```bash
gh run list --workflow=website.yml --branch main --limit 1
```

A red CI run publishes nothing and the previous site stays up. That is the intended behaviour, not a failure to investigate.

## Checklist

- [ ] For each document touched, you know whether it is derived or twinned
- [ ] No hand edit inside a generated output tree
- [ ] `generate` then `drift` run clean; no orphan pages
- [ ] No timestamp, SHA or version baked into generated sources
- [ ] Exported documents (OpenAPI, schema, CLI help) re-exported from the running app before regenerating
- [ ] Every claim in a twinned page verified by grepping the source, not by reading the old prose
- [ ] No removed or dormant feature described anywhere
- [ ] Configuration tables and `.env.example` match the one place that reads the environment
- [ ] Screenshots regenerated if the UI changed
- [ ] Publication confirmed after the merge

## Related skills

- `single-source-constants` — the root cause of most documentation contradictions
- `screenshot-verify` — regenerating screenshots the docs reference
- `deliberate-decisions` — documenting the choices that look wrong and are not
- `verify-and-ship` — running the drift check as part of the gate
