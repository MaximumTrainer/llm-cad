"""reset_session tool — clear session state."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from cad_mcp import session


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def reset_session() -> str:
        """Clear the current session: delete code history, model, and exports.

        Call this to start a completely new design from scratch.
        """
        return session.reset()
