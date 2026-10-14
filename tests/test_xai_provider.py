import json

import httpx
import pytest

from app.core.composition import build_llm_provider
from app.core.config import Settings
from app.llm.fallback import FallbackProvider
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.xai import XAIProvider


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeAsyncClient:
    last = None
    response_payload = {
        "model": "grok-4.7",
        "choices": [
            {
                "message": {"role": "assistant", "content": '{"ok":true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }

    def __init__(self, *, base_url, timeout):
        self.base_url = base_url
        self.timeout = timeout
        self.request = None
        FakeAsyncClient.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, path, *, headers, json):
        self.request = {"path": path, "headers": headers, "json": json}
        return FakeResponse(self.response_payload)


@pytest.mark.asyncio
async def test_xai_provider_maps_request_and_usage(monkeypatch):
    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", FakeAsyncClient)
    provider = XAIProvider(
        api_key="xai-test-secret",
        model="grok-4.7",
        reasoning_effort="medium",
    )
    response = await provider.generate(
        LLMRequest(
            system_prompt="Return JSON",
            messages=[LLMMessage(role="user", content="hello")],
            max_tokens=123,
            metadata={"conversation_id": "servgo-review"},
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
    )

    sent = FakeAsyncClient.last.request
    assert sent["path"] == "chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer xai-test-secret"
    assert sent["headers"]["x-grok-conv-id"] == "servgo-review"
    assert sent["json"]["model"] == "grok-4.7"
    assert sent["json"]["max_completion_tokens"] == 123
    assert sent["json"]["reasoning_effort"] == "medium"
    assert sent["json"]["messages"][0] == {"role": "system", "content": "Return JSON"}
    assert sent["json"]["response_format"]["type"] == "json_schema"
    assert sent["json"]["response_format"]["json_schema"]["strict"] is True
    assert sent["json"]["response_format"]["json_schema"]["schema"]["required"] == ["ok"]
    assert response.content == '{"ok":true}'
    assert response.model == "grok-4.7"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.stop_reason == "end_turn"

@pytest.mark.asyncio
async def test_xai_provider_allows_bounded_request_timeout(monkeypatch):
    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", FakeAsyncClient)
    provider = XAIProvider(api_key="xai-test-secret", timeout_seconds=300)

    await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role="user", content="hello")],
            metadata={"provider_timeout_seconds": 42},
        )
    )

    assert FakeAsyncClient.last.timeout.read == 42



def test_composition_selects_xai_and_requires_key():
    settings = Settings(
        _env_file=None,
        llm_provider="xai",
        llm_model="grok-4.7",
        xai_api_key="xai-test-secret",
        xai_reasoning_effort="low",
        xai_timeout_seconds=321,
        llm_fallback_models="",
    )
    provider = build_llm_provider(settings)
    assert isinstance(provider, XAIProvider)
    assert provider._timeout.read == 321

    missing = Settings(
        _env_file=None,
        llm_provider="xai",
        llm_model="grok-4.7",
        xai_api_key=None,
    )
    with pytest.raises(ValueError, match="XAI_API_KEY"):
        build_llm_provider(missing)


def test_xai_reasoning_effort_fails_closed():
    with pytest.raises(ValueError, match="reasoning effort"):
        XAIProvider(api_key="secret", reasoning_effort="turbo")  # type: ignore[arg-type]



class SequenceAsyncClient:
    responses = []
    calls = 0

    def __init__(self, *, base_url, timeout):
        self.base_url = base_url
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, path, *, headers, json):
        type(self).calls += 1
        item = type(self).responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_response(status_code: int, payload: dict):
    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    return httpx.Response(status_code, request=request, json=payload)


@pytest.mark.asyncio
async def test_xai_provider_retries_transient_gateway_failures(monkeypatch):
    SequenceAsyncClient.calls = 0
    SequenceAsyncClient.responses = [
        _http_response(502, {"error": {"message": "upstream failed"}}),
        _http_response(503, {"error": {"message": "temporarily unavailable"}}),
        _http_response(
            200,
            {
                "model": "grok-4.7",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ),
    ]
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", SequenceAsyncClient)
    monkeypatch.setattr("app.llm.xai.asyncio.sleep", fake_sleep)

    provider = XAIProvider(api_key="secret")
    response = await provider.generate(
        LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    )

    assert response.content == '{"ok":true}'
    assert SequenceAsyncClient.calls == 3
    assert sleeps == [1.0, 3.0]


@pytest.mark.asyncio
async def test_xai_provider_does_not_retry_model_timeout(monkeypatch):
    SequenceAsyncClient.calls = 0
    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    SequenceAsyncClient.responses = [httpx.ReadTimeout("model too slow", request=request)]
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", SequenceAsyncClient)
    monkeypatch.setattr("app.llm.xai.asyncio.sleep", fake_sleep)

    provider = XAIProvider(api_key="secret")
    with pytest.raises(httpx.ReadTimeout):
        await provider.generate(
            LLMRequest(
                messages=[LLMMessage(role="user", content="hello")],
                metadata={"provider_timeout_seconds": 30},
            )
        )

    assert SequenceAsyncClient.calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_xai_provider_does_not_retry_nontransient_http_error(monkeypatch):
    SequenceAsyncClient.calls = 0
    SequenceAsyncClient.responses = [
        _http_response(400, {"error": {"message": "bad request"}}),
    ]
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", SequenceAsyncClient)
    monkeypatch.setattr("app.llm.xai.asyncio.sleep", fake_sleep)

    provider = XAIProvider(api_key="secret")
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")])
        )

    assert SequenceAsyncClient.calls == 1
    assert sleeps == []



@pytest.mark.asyncio
async def test_non_grok_model_omits_grok_specific_fields(monkeypatch):
    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", FakeAsyncClient)
    provider = XAIProvider(
        api_key="xai-test-secret",
        model="gpt-6-sol",
        reasoning_effort="medium",
    )

    await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role="user", content="hello")],
            model="gpt-6-sol",
            metadata={
                "reasoning_effort": "low",
                "conversation_id": "should-not-be-grok-header",
            },
        )
    )

    sent = FakeAsyncClient.last.request
    assert sent["json"]["model"] == "gpt-6-sol"
    assert "reasoning_effort" not in sent["json"]
    assert "x-grok-conv-id" not in sent["headers"]



def test_composition_builds_model_fallback_chain():
    settings = Settings(
        _env_file=None,
        llm_provider="xai",
        llm_model="gpt-6-sol",
        llm_fallback_models="claude-opus-5-5,grok-4.7,gpt-6-sol",
        xai_api_key="xai-test-secret",
    )

    provider = build_llm_provider(settings)

    assert isinstance(provider, FallbackProvider)
    assert [model for model, _ in provider._providers] == [
        "gpt-6-sol",
        "claude-opus-5-5",
        "grok-4.7",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 12},
                                  {"completion_tokens": 20}])
async def test_xai_missing_or_partial_usage_is_unknown(monkeypatch, usage):
    monkeypatch.setattr("app.llm.xai.httpx.AsyncClient", FakeAsyncClient)
    original_payload = FakeAsyncClient.response_payload
    candidate = {
        "model": "grok-4.7",
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
    }
    if usage is not None:
        candidate["usage"] = usage
    FakeAsyncClient.response_payload = candidate
    try:
        response = await XAIProvider(api_key="test-key").generate(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")]))
        assert response.usage is None
    finally:
        FakeAsyncClient.response_payload = original_payload
