---
name: add-tool
description: Scaffold a new MCP tool for cad-mcp end to end — SPEC check, tool file, registration, response envelope, README row, prompt coverage, and tests. Use whenever adding, renaming, or removing a tool under src/cad_mcp/tools/, or when asked to "add a tool" to this server.
---

# Adding a tool to cad-mcp

Seven of the fourteen existing tools shipped with no prompt coverage and
four different response shapes because these steps were done from memory.
Do all of them, in order.

## 1. Refuse if SPEC.md does not cover it

Search SPEC.md for the tool. If it is absent, **stop** and say so. The
project rule (PLAN.md) is that SPEC.md changes first, in the same PR.
Offer to draft the SPEC entry — do not write the tool first.

## 2. Create `src/cad_mcp/tools/<name>.py`

One tool per file. Copy the shape of an existing tool:

```python
"""<name> tool — one line on what it does."""
from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool
from cad_mcp.envelope import fail, ok


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("<name>")
    def <name>(
        required_arg: str,
        optional_arg: int = 0,
        ctx: Context | None = None,
    ) -> str:
        """One line for the LLM, then Args: with every parameter.

        Args:
            required_arg: what it means, units if any.
            optional_arg: what it means, and the default's rationale.
        """
        sess = session.for_context(ctx)
        ...
```

Non-negotiables:
- `ctx: Context | None = None` **last**, so session state is per-client
  (CAD-007). Never `session.get_or_create()` with no argument.
- `@logged_tool("<name>")` — SPEC N6 wants every call logged.
- Mutating state? Take `with sess.lock:` (CAD-008).
- Return via `cad_mcp.envelope` so the response shape matches every
  other tool (CAD-019). Never invent a new shape.
- Return images as `ImageContent`, measurements as numbers. Never return
  a mesh or geometry blob — the LLM cannot read it.
- Any file write goes through `cad_mcp.paths.safe_output_path`.

## 3. Register it

Add to the import block and the `register(...)` calls in
`src/cad_mcp/server.py`, keeping alphabetical order.

## 4. Document it

- A row in README.md's tool table. The pre-commit hook fails without it.
- SPEC.md's consolidated tool table.

## 5. Teach it

Add the tool to `src/cad_mcp/prompts.py` — at minimum `design_workflow`,
saying *when* to reach for it. `tests/test_docs_consistency.py` fails if
a registered tool appears in no prompt.

## 6. Test it

In `tests/`, covering at least:
- the happy path through `mcp.call_tool`;
- one argument-validation failure, asserting the structured error;
- the no-model case if it reads geometry.

## 7. Verify

```bash
uv run ruff check . && uv run mypy && uv run pytest -q
```

Then confirm the schema is well-formed for a real provider:
`uv run pytest tests/test_llm_integration.py::test_tool_schemas_convert_to_openai_functions -m llm`
