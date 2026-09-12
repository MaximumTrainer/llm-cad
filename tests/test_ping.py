"""Test the ping tool responds correctly."""

import pytest

from cad_mcp.server import mcp

from .envelope_helpers import summary


@pytest.mark.anyio
async def test_ping_returns_pong() -> None:
    result = await mcp.call_tool("ping", {})
    assert summary(result) == "pong"
