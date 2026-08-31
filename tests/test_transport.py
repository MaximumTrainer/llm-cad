"""Tests for SPEC 10.1 — streamable HTTP transport with auth."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cad_mcp.transport import BearerTokenVerifier, TransportConfig, parse_args

# ------------------------------------------------------------------
# Unit: parse_args
# ------------------------------------------------------------------


class TestParseArgs:
    def test_default_is_stdio(self) -> None:
        config = parse_args([])
        assert config.transport == "stdio"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.auth_token is None

    def test_cli_http(self) -> None:
        config = parse_args(["--transport", "http"])
        assert config.transport == "streamable-http"

    def test_env_http(self) -> None:
        with patch.dict(os.environ, {"CAD_MCP_TRANSPORT": "http"}):
            config = parse_args([])
        assert config.transport == "streamable-http"

    def test_cli_overrides_env(self) -> None:
        with patch.dict(os.environ, {"CAD_MCP_TRANSPORT": "http"}):
            config = parse_args(["--transport", "stdio"])
        assert config.transport == "stdio"

    def test_env_host_port(self) -> None:
        with patch.dict(
            os.environ,
            {"CAD_MCP_HOST": "0.0.0.0", "CAD_MCP_PORT": "9000"},
        ):
            config = parse_args([])
        assert config.host == "0.0.0.0"
        assert config.port == 9000

    def test_env_auth_token(self) -> None:
        with patch.dict(os.environ, {"CAD_MCP_AUTH_TOKEN": "secret123"}):
            config = parse_args([])
        assert config.auth_token == "secret123"

    def test_env_cors_origin(self) -> None:
        with patch.dict(
            os.environ, {"CAD_MCP_CORS_ORIGIN": "https://app.example.com"}
        ):
            config = parse_args([])
        assert config.cors_origin == "https://app.example.com"

    def test_env_allowed_hosts(self) -> None:
        with patch.dict(
            os.environ, {"CAD_MCP_ALLOWED_HOSTS": "a.com, b.com"}
        ):
            config = parse_args([])
        assert config.allowed_hosts == ["a.com", "b.com"]

    def test_env_stateless(self) -> None:
        with patch.dict(os.environ, {"CAD_MCP_STATELESS": "1"}):
            config = parse_args([])
        assert config.stateless is True

    def test_stateless_default_off(self) -> None:
        config = parse_args([])
        assert config.stateless is False


# ------------------------------------------------------------------
# Unit: TransportConfig.run_kwargs
# ------------------------------------------------------------------


class TestRunKwargs:
    def test_stdio_empty(self) -> None:
        config = TransportConfig(transport="stdio")
        assert config.run_kwargs() == {}

    def test_http_basic(self) -> None:
        config = TransportConfig(
            transport="streamable-http", host="127.0.0.1", port=9000
        )
        kwargs = config.run_kwargs()
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9000
        assert kwargs["stateless_http"] is False
        assert "transport_security" not in kwargs

    def test_http_custom_host_adds_security(self) -> None:
        config = TransportConfig(
            transport="streamable-http", host="mcp.example.com", port=8000
        )
        kwargs = config.run_kwargs()
        assert "transport_security" in kwargs

    def test_http_allowed_hosts(self) -> None:
        config = TransportConfig(
            transport="streamable-http",
            host="127.0.0.1",
            allowed_hosts=["mcp.example.com"],
        )
        kwargs = config.run_kwargs()
        assert "transport_security" in kwargs


# ------------------------------------------------------------------
# Unit: TransportConfig.auth_settings
# ------------------------------------------------------------------


class TestAuthSettings:
    def test_no_token_returns_none(self) -> None:
        config = TransportConfig()
        assert config.auth_settings() is None

    def test_with_token_returns_verifier_and_settings(self) -> None:
        config = TransportConfig(auth_token="mytoken", port=9000)
        result = config.auth_settings()
        assert result is not None
        verifier, settings = result
        assert isinstance(verifier, BearerTokenVerifier)
        assert "9000" in str(settings.resource_server_url)


# ------------------------------------------------------------------
# Unit: BearerTokenVerifier
# ------------------------------------------------------------------


class TestBearerTokenVerifier:
    @pytest.mark.anyio
    async def test_valid_token(self) -> None:
        v = BearerTokenVerifier("secret")
        token = await v.verify_token("secret")
        assert token is not None
        assert token.client_id == "bearer"
        assert token.scopes == ["cad"]

    @pytest.mark.anyio
    async def test_invalid_token(self) -> None:
        v = BearerTokenVerifier("secret")
        token = await v.verify_token("wrong")
        assert token is None


# ------------------------------------------------------------------
# Unit: create_server factory
# ------------------------------------------------------------------


class TestCreateServer:
    def test_creates_with_tools(self) -> None:
        from cad_mcp.server import create_server

        server = create_server()
        assert server.name == "cad-mcp"

    def test_module_level_mcp_works(self) -> None:
        from cad_mcp.server import mcp

        assert mcp.name == "cad-mcp"


# ------------------------------------------------------------------
# Integration: health endpoint via HTTP
# ------------------------------------------------------------------

SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "src" / "cad_mcp" / "server.py"


class TestHTTPIntegration:
    """Start the server as a subprocess on HTTP and test endpoints."""

    @pytest.fixture
    def server_proc(self) -> subprocess.Popen[bytes]:  # type: ignore[type-arg]
        """Start cad-mcp on HTTP in a subprocess, wait for ready."""
        env = {
            **os.environ,
            "CAD_MCP_TRANSPORT": "http",
            "CAD_MCP_PORT": "18923",
        }
        env.pop("CAD_MCP_AUTH_TOKEN", None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "cad_mcp.server"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_port(18923, timeout=15)
        yield proc  # type: ignore[misc]
        proc.terminate()
        proc.wait(timeout=5)

    @pytest.fixture
    def auth_server_proc(self) -> subprocess.Popen[bytes]:  # type: ignore[type-arg]
        """Start cad-mcp on HTTP with auth."""
        env = {
            **os.environ,
            "CAD_MCP_TRANSPORT": "http",
            "CAD_MCP_PORT": "18924",
            "CAD_MCP_AUTH_TOKEN": "test-secret-token",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "cad_mcp.server"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_port(18924, timeout=15)
        yield proc  # type: ignore[misc]
        proc.terminate()
        proc.wait(timeout=5)

    def test_health_endpoint(self, server_proc: subprocess.Popen[bytes]) -> None:
        import urllib.request

        resp = urllib.request.urlopen("http://127.0.0.1:18923/health")
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body == {"status": "ok"}

    def test_health_no_auth_required(
        self, auth_server_proc: subprocess.Popen[bytes]
    ) -> None:
        import urllib.request

        resp = urllib.request.urlopen("http://127.0.0.1:18924/health")
        assert resp.status == 200

    def test_mcp_endpoint_returns_401_without_token(
        self, auth_server_proc: subprocess.Popen[bytes]
    ) -> None:
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:18924/mcp",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401


# ------------------------------------------------------------------
# Integration: stdio still works
# ------------------------------------------------------------------


class TestStdioStillWorks:
    @pytest.mark.anyio
    async def test_ping_via_module_mcp(self) -> None:
        from cad_mcp.server import mcp

        result = await mcp.call_tool("ping", {})
        text = result.content[0].text  # type: ignore[union-attr]
        assert "pong" in text.lower()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _wait_for_port(port: int, timeout: float = 10) -> None:
    """Block until a TCP port is accepting connections."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    msg = f"Port {port} not open after {timeout}s"
    raise TimeoutError(msg)
