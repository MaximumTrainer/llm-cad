"""MCP server entrypoint for cad-mcp."""
from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from cad_mcp import _logging as cad_logging
from cad_mcp import prompts, resources
from cad_mcp.tools import (
    create_part,
    delete_part,
    execute_cad,
    export_model,
    gen_ai_mesh,
    list_parts,
    list_session,
    measure,
    ping,
    position_part,
    render_views,
    reset_session,
    set_active_part,
    validate_mesh,
)


def create_server(**kwargs: Any) -> MCPServer:
    """Create and configure an MCPServer with all tools registered."""
    server = MCPServer("cad-mcp", **kwargs)

    ping.register(server)
    execute_cad.register(server)
    render_views.register(server)
    validate_mesh.register(server)
    measure.register(server)
    export_model.register(server)
    gen_ai_mesh.register(server)
    list_session.register(server)
    reset_session.register(server)
    create_part.register(server)
    set_active_part.register(server)
    position_part.register(server)
    list_parts.register(server)
    delete_part.register(server)
    prompts.register(server)
    resources.register(server)

    @server.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server


# Default instance for tests and smoke script (no auth, stdio)
mcp = create_server()


def main() -> None:
    cad_logging.setup()

    # Start a spare sandbox worker so the first execute_cad does not pay
    # the ~3.3s CadQuery import on the critical path (CAD-014).
    from cad_mcp import sandbox

    sandbox.prewarm()

    from cad_mcp.transport import parse_args

    config = parse_args(sys.argv[1:])

    if config.transport == "stdio":
        mcp.run()
        return

    # Streamable HTTP — build server with auth if configured
    server_kwargs: dict[str, Any] = {}
    auth = config.auth_settings()
    if auth:
        verifier, settings = auth
        server_kwargs["token_verifier"] = verifier
        server_kwargs["auth"] = settings

    server = create_server(**server_kwargs) if server_kwargs else mcp

    run_kwargs = config.run_kwargs()

    if config.cors_origin:
        _run_with_cors(server, config, run_kwargs)
    else:
        server.run(transport="streamable-http", **run_kwargs)


def _run_with_cors(
    server: MCPServer,
    config: Any,
    run_kwargs: dict[str, Any],
) -> None:
    """Run HTTP transport with CORS middleware (SPEC 10.1 H5)."""
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    app = server.streamable_http_app(
        **{k: v for k, v in run_kwargs.items() if k not in ("host", "port")},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.cors_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Last-Event-ID",
            "Mcp-Method",
            "Mcp-Name",
            "Mcp-Protocol-Version",
            "Mcp-Session-Id",
        ],
        expose_headers=["Mcp-Session-Id"],
    )
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
