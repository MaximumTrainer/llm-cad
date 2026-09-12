"""reset_session tool — clear session state."""
from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from cad_mcp import session
from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("reset_session")
    def reset_session(ctx: Context | None = None) -> str:
        """Clear the current session: delete code history, model, and exports.

        Call this to start a completely new design from scratch.
        """
        return session.reset(session.resolve_id(ctx))
