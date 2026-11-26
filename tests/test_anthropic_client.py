"""``AnthropicClient`` and provider selection for ``generate-course`` (SPEC 10.5).

The Messages API is never called live here: requests go through the real
``anthropic`` SDK into a mock transport, so the test sees the exact body
the SDK would send.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from cad_mcp import cli
from cad_mcp.llm_client import (
    ANTHROPIC_DEFAULT_MODEL,
    DEFAULT_MODEL,
    PROVIDER_ENV,
    AnthropicClient,
    Message,
    ModelError,
    OpenAICompatibleClient,
    default_model_for,
    provider_for,
)

KEY = "sk-ant-secret-123"


def _reply(text: str, stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": ANTHROPIC_DEFAULT_MODEL,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        **extra,
    }


def _client(handler: Any) -> AnthropicClient:
    sdk = anthropic.Anthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)
        ),
    )
    return AnthropicClient(KEY, client=sdk)


CONVERSATION: list[Message] = [
    {"role": "system", "content": "You write CadQuery."},
    {"role": "user", "content": "Plan the course."},
    {"role": "assistant", "content": "{}"},
    {"role": "user", "content": "Fix the error."},
]


def test_sends_system_on_top_and_the_rest_unchanged() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_reply("result = 1"))

    text = _client(handler).complete(
        CONVERSATION, model=ANTHROPIC_DEFAULT_MODEL, seed=7
    )

    assert text == "result = 1"
    request = seen[0]
    assert request.url.path == "/v1/messages"
    body = json.loads(request.content)
    assert body["model"] == ANTHROPIC_DEFAULT_MODEL
    assert body["system"] == "You write CadQuery."
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    # No sampling parameters: the current models reject them.
    assert "seed" not in body and "temperature" not in body
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert request.headers["x-api-key"] == KEY


def test_joins_only_the_text_blocks() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = _reply("")
        payload["content"] = [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "part one "},
            {"type": "text", "text": "part two"},
        ]
        return httpx2.Response(200, json=payload)

    assert (
        _client(handler).complete(CONVERSATION, model="m", seed=0)
        == "part one part two"
    )


def test_a_refusal_is_a_model_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json=_reply(
                "",
                "refusal",
                stop_details={
                    "type": "refusal",
                    "category": "cyber",
                    "explanation": "x",
                },
            ),
        )

    with pytest.raises(ModelError, match=r"declined.*cyber"):
        _client(handler).complete(CONVERSATION, model="m", seed=0)


def test_an_empty_reply_is_a_model_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_reply("", "max_tokens"))

    with pytest.raises(ModelError, match=r"empty reply.*max_tokens"):
        _client(handler).complete(CONVERSATION, model="m", seed=0)


def test_http_errors_never_leak_the_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": KEY},
            },
        )

    with pytest.raises(ModelError) as exc:
        _client(handler).complete(CONVERSATION, model="m", seed=0)
    assert "401" in str(exc.value)
    assert KEY not in str(exc.value)


def test_provider_follows_the_key_unless_told(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    assert provider_for("sk-ant-api03-x") == "anthropic"
    assert provider_for("sk-or-v1-x") == "openai-compatible"
    monkeypatch.setenv(PROVIDER_ENV, "anthropic")
    assert provider_for("sk-or-v1-x") == "anthropic"
    monkeypatch.setenv(PROVIDER_ENV, "bogus")
    with pytest.raises(ValueError, match=PROVIDER_ENV):
        provider_for("k")
    assert default_model_for("anthropic") == ANTHROPIC_DEFAULT_MODEL
    assert default_model_for("openai-compatible") == DEFAULT_MODEL


@pytest.mark.parametrize(
    ("key", "client_type", "model"),
    [
        ("sk-ant-x", AnthropicClient, ANTHROPIC_DEFAULT_MODEL),
        ("sk-or-v1-x", OpenAICompatibleClient, DEFAULT_MODEL),
    ],
)
def test_cli_picks_the_client_and_default_model_from_the_key(
    key: str,
    client_type: type,
    model: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("LLM_CAD_MODEL_API_KEY", key)
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    seen: dict[str, Any] = {}

    def fake_generate(
        course: Any, out: Any, *, client: Any, model: str, **_: Any
    ) -> Any:
        seen["client"], seen["model"] = client, model
        raise SystemExit(0)

    monkeypatch.setattr(cli.course_gen, "load_course", lambda path: object())
    monkeypatch.setattr(cli.course_gen, "generate", fake_generate)
    args = cli.build_parser().parse_args(
        [
            "generate-course",
            "--course-json",
            str(tmp_path / "c.json"),
            "--out",
            str(tmp_path / "o"),
        ]
    )
    with pytest.raises(SystemExit):
        cli.generate_course(args)
    assert isinstance(seen["client"], client_type)
    assert seen["model"] == model


def test_cli_rejects_an_unknown_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_CAD_MODEL_API_KEY", "k")
    monkeypatch.setenv(PROVIDER_ENV, "bogus")
    out = tmp_path / "o"
    args = cli.build_parser().parse_args(
        [
            "generate-course",
            "--course-json",
            str(tmp_path / "c.json"),
            "--out",
            str(out),
        ]
    )
    assert cli.generate_course(args) == cli.EXIT_USAGE
    assert not out.exists()
    assert PROVIDER_ENV in capsys.readouterr().err
