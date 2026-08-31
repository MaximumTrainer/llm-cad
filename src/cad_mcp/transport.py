"""Transport configuration for cad-mcp (SPEC 10.1).

Reads CLI args and env vars to select stdio or streamable-HTTP transport,
configure auth, CORS, and transport security.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl


@dataclass(frozen=True)
class TransportConfig:
    """Resolved transport configuration from CLI args + env vars."""

    transport: Literal["stdio", "streamable-http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    auth_token: str | None = None
    cors_origin: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    stateless: bool = False

    def run_kwargs(self) -> dict[str, Any]:
        """Build kwargs for ``MCPServer.run()``."""
        if self.transport == "stdio":
            return {}
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "stateless_http": self.stateless,
        }
        hosts = list(self.allowed_hosts)
        if self.host not in ("127.0.0.1", "localhost"):
            hosts.append(self.host)
            hosts.append(f"{self.host}:*")
        if hosts:
            kwargs["transport_security"] = TransportSecuritySettings(
                allowed_hosts=hosts,
            )
        return kwargs

    def auth_settings(self) -> tuple[BearerTokenVerifier, AuthSettings] | None:
        """Build SDK auth objects if ``auth_token`` is set."""
        if not self.auth_token:
            return None
        resource_url = f"http://{self.host}:{self.port}/mcp"
        verifier = BearerTokenVerifier(self.auth_token)
        settings = AuthSettings(
            issuer_url=AnyHttpUrl(f"http://{self.host}:{self.port}"),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=["cad"],
        )
        return verifier, settings


class BearerTokenVerifier:
    """Simple symmetric-token verifier (SPEC 10.1 H2)."""

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != self._expected:
            return None
        return AccessToken(
            token=token,
            client_id="bearer",
            scopes=["cad"],
        )


def parse_args(argv: list[str] | None = None) -> TransportConfig:
    """Parse CLI args + env vars into a TransportConfig."""
    parser = argparse.ArgumentParser(
        prog="cad-mcp",
        description="MCP server for 3D modeling with CadQuery",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help="Transport: stdio (default) or http (streamable HTTP)",
    )
    args = parser.parse_args(argv)

    transport_cli = args.transport
    transport_env = os.environ.get("CAD_MCP_TRANSPORT", "").lower()

    # CLI takes precedence over env var
    chosen = transport_cli or transport_env or "stdio"
    if chosen == "http":
        transport: Literal["stdio", "streamable-http"] = "streamable-http"
    else:
        transport = "stdio"

    allowed_hosts_raw = os.environ.get("CAD_MCP_ALLOWED_HOSTS", "")
    allowed_hosts = [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()]

    return TransportConfig(
        transport=transport,
        host=os.environ.get("CAD_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("CAD_MCP_PORT", "8000")),
        auth_token=os.environ.get("CAD_MCP_AUTH_TOKEN"),
        cors_origin=os.environ.get("CAD_MCP_CORS_ORIGIN"),
        allowed_hosts=allowed_hosts,
        stateless=os.environ.get("CAD_MCP_STATELESS", "") == "1",
    )
