---
name: render-check
description: Required procedure for changes to render.py or the render_views tool in cad-mcp — exercise every backend, check the N2 latency budget, and handle golden-image regeneration deliberately. Use when touching rendering, tessellation, view layout, or camera code.
---

# Render check

The render is the product. CLAUDE.md: "The visual feedback loop is the
product. Any change that makes renders slower than ~2s or breaks image
return via MCP content blocks is a regression."

## 1. Both backends, every time

There are two: matplotlib (always available, the de-facto default) and
pyrender/EGL (optional extra). A change that only exercises one is half
tested — the golden test once guarded a function the tool never called.

```bash
uv run pytest tests/test_render.py -q                    # active backend
CAD_MCP_FORCE_BACKEND=matplotlib uv run pytest tests/test_render.py -q
CAD_MCP_FORCE_BACKEND=pyrender  uv run pytest tests/test_render.py -q   # needs the gpu extra
```

Both must produce: a 4-view grid, an axis triad, mm scale ticks, and
per-view titles. SPEC 5.1 requires the ticks — without them the LLM
cannot judge scale, which is the whole point of rendering.

## 2. Drive the tool, not the helper

Assertions must go through `mcp.call_tool("render_views", ...)`. A test
that calls a module-level helper can pass while the shipped path is
broken.

## 3. Latency budget (SPEC N2, ≤2s)

```bash
uv run pytest tests/test_perf.py -q
```

If a render exceeds the budget, fix it or degrade tessellation tolerance
and say so in the response — do not quietly ship a slower loop.

## 4. Golden images

A perceptual-hash mismatch is a question, not a failure to silence.

1. Render the reference bracket before and after; look at both.
2. If the change is intended and an improvement, regenerate:
   `uv run python scripts/refresh_goldens.py`
3. Say in the commit message what visibly changed and why.

Never regenerate a golden to make a red test green without looking at
the image.

## 5. Verify the MCP contract

The tool must return `ImageContent` (base64 PNG) plus a text block.
Confirm with a real model — this is the only check that proves the image
survives the MCP → base64 → vision path and is legible:

```bash
uv run pytest -m llm -k render
```

That test asks a model to name the shape and read its height off the
axis ticks with no access to the source code.
