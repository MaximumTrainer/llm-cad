"""MCP server entrypoint for cad-mcp."""
from __future__ import annotations

import logging
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
    from cad_mcp import geometry, sandbox

    sandbox.prewarm()
    # And the geometry kernel, for the same reason: the first render
    # would otherwise pay that import again on the other side of the
    # isolation boundary (issue #10).
    geometry.POOL.prewarm()

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

    from cad_mcp import fly

    # Only take the explicit ASGI path when something needs to wrap the
    # app; `server.run()` stays the default so the common case keeps the
    # SDK's own wiring.
    if config.cors_origin or fly.machine_id():
        _run_asgi(server, config, run_kwargs)
    else:
        server.run(transport="streamable-http", **run_kwargs)


def _run_asgi(
    server: MCPServer,
    config: Any,
    run_kwargs: dict[str, Any],
) -> None:
    """Run HTTP transport with our own middleware stack.

    CORS is SPEC 10.1 H5; the Fly wrapper is SPEC 10.4 session affinity.
    """
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    from cad_mcp import fly

    app = server.streamable_http_app(
        **{k: v for k, v in run_kwargs.items() if k not in ("host", "port")},
    )
    if config.cors_origin:
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

    asgi: Any = app
    machine = fly.machine_id()
    if machine:
        # Outermost: it must see the session id before anything else and
        # rewrite it after everything else.
        asgi = fly.FlyReplayMiddleware(app, machine)
        logging.getLogger(__name__).info(
            "fly session affinity active on machine %s", machine
        )

    uvicorn.run(asgi, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
