"""Test the ping tool responds correctly."""

import pytest

from cad_mcp.server import mcp


@pytest.mark.anyio
async def test_ping_returns_pong() -> None:
    result = await mcp.call_tool("ping", {})
    assert result.content[0].text == "pong"  # type: ignore[union-attr]
