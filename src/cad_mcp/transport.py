"""Transport configuration for cad-mcp (SPEC 10.1).

Reads CLI args and env vars to select stdio or streamable-HTTP transport,
configure auth, CORS, and transport security.
"""
from __future__ import annotations

import argparse
import os
import secrets
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
        kwargs["transport_security"] = self.transport_security()
        return kwargs

    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "::1", "localhost")

    def transport_security(self) -> TransportSecuritySettings:
        """Host allowlist for DNS-rebinding protection (SPEC 10.1 H6).

        Never derived from the bind address: `0.0.0.0` and `::` are
        wildcards, never a real `Host` header, so adding them produced an
        allowlist that matched nothing and protected nothing — in exactly
        the deployment where protection matters.
        """
        hosts = list(self.allowed_hosts)
        if not hosts:
            if self.is_loopback():
                hosts = [
                    "127.0.0.1", "localhost", "::1",
                    f"127.0.0.1:{self.port}",
                    f"localhost:{self.port}",
                ]
            else:
                # Deny by default rather than pretend to be protected.
                hosts = []
        return TransportSecuritySettings(allowed_hosts=hosts)

    def warnings(self) -> list[str]:
        """Deployment risks worth telling the operator about."""
        out: list[str] = []
        if not self.is_loopback() and not self.allowed_hosts:
            out.append(
                f"Binding non-loopback host {self.host!r} with no "
                f"CAD_MCP_ALLOWED_HOSTS: every request will be rejected by "
                f"DNS-rebinding protection. Set CAD_MCP_ALLOWED_HOSTS to the "
                f"hostnames clients will use."
            )
        if not self.is_loopback() and not self.auth_token:
            out.append(
                f"Binding non-loopback host {self.host!r} with no "
                f"CAD_MCP_AUTH_TOKEN: the server is unauthenticated. Set a "
                f"token, and terminate TLS in front of it."
            )
        return out

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
        # Constant-time: `!=` on str short-circuits and leaks the token's
        # length and common prefix across repeated requests.
        if not secrets.compare_digest(
            token.encode("utf-8"), self._expected.encode("utf-8")
        ):
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
