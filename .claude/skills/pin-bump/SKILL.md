---
name: pin-bump
description: Procedure for changing a cadquery, OCP, trimesh, manifold3d, or mcp version pin in cad-mcp. Rendering and export are version-sensitive, so this runs the golden tests and records the evidence. Use for any dependency bump or lockfile refresh.
---

# Bumping a version pin

CLAUDE.md: "Don't upgrade cadquery/OCP pins casually — rendering and
export are version-sensitive; run golden tests after any bump." This is
that rule as a checklist.

## 1. Know what you are moving

| Pin | Breaks when wrong |
|---|---|
| `cadquery` / `OCP` | tessellation output, STEP topology, selector behaviour, fillet/shell success |
| `trimesh` | STL/3MF/GLB bytes, watertight verdicts |
| `manifold3d` | the manifold status string `validate.py` compares against |
| `mcp` | `MCPServer` API, Context injection, transport kwargs |
| `matplotlib` / `pyrender` | every golden image |

`mcp` must stay on a v2-compatible range: the code uses `MCPServer` and
`input_schema`, which do not exist in v1.

## 2. Bump and re-lock

```bash
# edit pyproject.toml, then
uv lock --upgrade-package <name>
uv sync --all-extras
```

Keep the upper bound. An unbounded `cadquery>=2.8.0` is how a breaking
OCP arrives unannounced.

## 3. Run the version-sensitive gates

```bash
uv run pytest tests/test_validate_export.py -q   # STEP/STL/3MF/GLB + determinism
uv run pytest tests/test_render.py -q            # golden perceptual hashes
uv run pytest -q                                 # everything else
uv run python scripts/smoke.py
```

## 4. Diff what actually changed

- **Export determinism:** the same code must still produce byte-stable
  STL across two *processes*. If STEP topology moved, say how.
- **Goldens:** a perceptual-hash change means the render moved. Look at
  the image before regenerating — see the `render-check` skill.
- **Manifold status:** `validate.py` compares `str(status)` against
  `"Error.NoError"`. A manifold3d bump can silently change that repr and
  turn every mesh non-manifold. Check it explicitly.

## 5. Record the evidence

In the PR body: old version, new version, which gates ran, whether any
golden was regenerated and why. A bump with no recorded evidence should
be treated as unreviewed.
