"""Ping tool — connectivity check."""

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def ping() -> str:
        """Check that the cad-mcp server is reachable. Returns 'pong'."""
        return "pong"
