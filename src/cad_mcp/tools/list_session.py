"""list_session tool — inspect current session state."""
from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from cad_mcp import session


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def list_session() -> str:
        """Return the current session's parts, code history, and exports.

        Useful for recovering context after a long conversation or when
        the model needs to inspect what has been built so far.  Shows
        the active part, all parts with their positions and colors,
        and the code history for the active part.
        """
        sess = session.get_or_create()
        return json.dumps(sess.summary(), indent=2)
