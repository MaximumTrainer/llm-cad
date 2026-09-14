"""Filename safety, durable exports, and transport hardening.

Covers CAD-003 (export path traversal), CAD-023 (exports outliving
reset) and CAD-006 (token comparison, host allowlist).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import anyio
import pytest

from cad_mcp import session
from cad_mcp.paths import UnsafeFilename, safe_output_path, validate_filename
from cad_mcp.server import mcp
from cad_mcp.transport import BearerTokenVerifier, TransportConfig

from .envelope_helpers import flat, summary

# Builds real geometry, so each test pays a sandbox subprocess.
# Deselect with -m "not geometry" for fast feedback (CAD-025).
# Most of this file is pure argument handling -- path validation,
# constant-time token comparison, host allowlists -- which needs no
# geometry at all. The blanket file-level marker put 37 such tests
# behind a sandbox subprocess they never used (CAD-025).

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)"


@pytest.fixture(autouse=True)
def _isolated_output(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setenv(
        "CAD_MCP_OUTPUT_DIR", tempfile.mkdtemp(prefix="cad-mcp-out-")
    )
    session.cleanup_all()


# ------------------------------------------------------------------
# CAD-003: filename validation
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "../../../../escape",
        r"..\..\escape",
        "/etc/passwd",
        "C:/Windows/system32/evil",
        r"C:\Windows\evil",
        "sub/dir",
        "sub\\dir",
        "",
        "   ",
        ".hidden",
        "con",
        "NUL",
        "lpt1.stl",
        "a" * 80,
    ],
)
def test_unsafe_filenames_are_rejected(bad: str) -> None:
    with pytest.raises(UnsafeFilename):
        validate_filename(bad)


@pytest.mark.parametrize(
    "good", ["bracket", "model", "part_1", "v2.0-final", "A1"]
)
def test_safe_filenames_are_accepted(good: str) -> None:
    assert validate_filename(good) == good


def test_safe_output_path_stays_inside(tmp_path: Path) -> None:
    resolved = safe_output_path(tmp_path, "bracket.stl")
    assert resolved.parent == tmp_path.resolve()
    assert ".." not in str(resolved)


@pytest.mark.geometry
def test_export_rejects_traversal_and_writes_nothing() -> None:
    """The escape proven during review, end to end through the tool."""
    escaped = Path.home() / "cadmcp_export_escape.stl"
    escaped.unlink(missing_ok=True)

    async def scenario() -> dict[str, object]:
        await mcp.call_tool("execute_cad", {"code": BOX})
        result = await mcp.call_tool(
            "export_model",
            {"format": "stl", "filename": "../../../../cadmcp_export_escape"},
        )
        return flat(result)

    payload = anyio.run(scenario)

    assert payload["ok"] is False
    assert "Invalid filename" in payload["error"]
    assert not escaped.exists(), "SANDBOX ESCAPE: export wrote outside"


@pytest.mark.geometry
def test_export_returns_a_resolved_path() -> None:
    async def scenario() -> dict[str, object]:
        await mcp.call_tool("execute_cad", {"code": BOX})
        result = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "bracket"}
        )
        return flat(result)

    payload = anyio.run(scenario)
    assert payload["ok"] is True
    path = Path(payload["path"])
    assert path.is_absolute()
    assert ".." not in path.parts
    assert path.exists()


# ------------------------------------------------------------------
# CAD-023: exports are durable
# ------------------------------------------------------------------


@pytest.mark.geometry
def test_exports_survive_reset_session() -> None:
    """`reset_session` used to rmtree the directory holding the STL."""

    async def scenario() -> tuple[str, str]:
        await mcp.call_tool("execute_cad", {"code": BOX})
        exported = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "keepme"}
        )
        path = flat(exported)["path"]  # type: ignore[union-attr]
        reset = await mcp.call_tool("reset_session", {})
        return path, summary(reset)

    path, message = anyio.run(scenario)

    assert Path(path).exists(), "reset_session destroyed the exported file"
    assert "kept" in message.lower(), (
        f"reset_session did not tell the user exports were kept: {message}"
    )


def test_export_dir_is_outside_the_session_tmpdir() -> None:
    sess = session.get_or_create()
    assert sess.tmpdir not in sess.output_dir().parents
    assert sess.output_dir() != sess.tmpdir


def test_output_dir_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    target = tempfile.mkdtemp(prefix="cad-mcp-custom-")
    monkeypatch.setenv("CAD_MCP_OUTPUT_DIR", target)
    session.cleanup_all()
    sess = session.get_or_create()
    assert str(sess.output_dir()).startswith(str(Path(target).resolve()))


# ------------------------------------------------------------------
# CAD-006: transport hardening
# ------------------------------------------------------------------


def test_token_comparison_is_constant_time() -> None:
    import inspect

    source = inspect.getsource(BearerTokenVerifier.verify_token)
    assert "compare_digest" in source, (
        "Token comparison must not use `!=`, which short-circuits and "
        "leaks length and prefix."
    )


def test_valid_token_is_accepted() -> None:
    verifier = BearerTokenVerifier("s3cret")
    token = anyio.run(verifier.verify_token, "s3cret")
    assert token is not None
    assert token.scopes == ["cad"]


@pytest.mark.parametrize("bad", ["", "s3cre", "s3cret ", "wrong", "S3CRET"])
def test_invalid_tokens_are_rejected(bad: str) -> None:
    verifier = BearerTokenVerifier("s3cret")
    assert anyio.run(verifier.verify_token, bad) is None


def test_wildcard_bind_never_enters_the_allowlist() -> None:
    """`0.0.0.0` is not a Host header; allowing it protected nothing."""
    for wildcard in ("0.0.0.0", "::"):
        config = TransportConfig(
            transport="streamable-http", host=wildcard, port=8000
        )
        hosts = config.transport_security().allowed_hosts or []
        assert wildcard not in hosts
        assert f"{wildcard}:*" not in hosts


def test_loopback_gets_a_real_allowlist() -> None:
    config = TransportConfig(transport="streamable-http", host="127.0.0.1")
    hosts = config.transport_security().allowed_hosts or []
    assert "127.0.0.1" in hosts
    assert "localhost" in hosts


def test_public_bind_without_config_is_warned_about() -> None:
    config = TransportConfig(transport="streamable-http", host="0.0.0.0")
    warnings = config.warnings()
    assert any("CAD_MCP_ALLOWED_HOSTS" in w for w in warnings)
    assert any("CAD_MCP_AUTH_TOKEN" in w for w in warnings)


def test_loopback_bind_is_not_warned_about() -> None:
    assert TransportConfig(transport="streamable-http").warnings() == []


def test_transport_security_is_always_applied() -> None:
    kwargs = TransportConfig(
        transport="streamable-http", host="127.0.0.1"
    ).run_kwargs()
    assert "transport_security" in kwargs


def test_env_allowed_hosts_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TransportConfig(
        transport="streamable-http",
        host="0.0.0.0",
        allowed_hosts=["cad.example.com"],
    )
    hosts = config.transport_security().allowed_hosts or []
    assert hosts == ["cad.example.com"]
    assert not any("ALLOWED_HOSTS" in w for w in config.warnings())


def test_os_environ_is_not_leaked_into_output_dir() -> None:
    """Guard against a stray absolute path from a bad env value."""
    assert os.environ.get("CAD_MCP_OUTPUT_DIR")
    assert session.output_root().is_absolute()


@pytest.mark.geometry
def test_export_collisions_do_not_overwrite() -> None:
    """A second export of the same name must not destroy the first."""

    async def scenario() -> tuple[str, str]:
        await mcp.call_tool("execute_cad", {"code": BOX})
        first = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "dupe"}
        )
        second = await mcp.call_tool(
            "export_model", {"format": "stl", "filename": "dupe"}
        )
        return (
            flat(first)["path"],  # type: ignore[union-attr]
            flat(second)["path"],  # type: ignore[union-attr]
        )

    first, second = anyio.run(scenario)
    assert first != second, "Second export silently overwrote the first"
    assert Path(first).exists() and Path(second).exists()
    assert Path(second).stem == "dupe-1"
