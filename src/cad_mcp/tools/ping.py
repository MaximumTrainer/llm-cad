"""Ping tool — connectivity check."""

from mcp.server.mcpserver import MCPServer

from cad_mcp._logging import logged_tool


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @logged_tool("ping")
    def ping() -> str:
        """Check that the cad-mcp server is reachable. Returns 'pong'."""
        return "pong"
