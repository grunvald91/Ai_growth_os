import json

import pytest

from app.core.composition import build_llm_provider
from app.core.config import Settings
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
    assert response.content == '{"ok":true}'
    assert response.model == "grok-4.7"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.stop_reason == "end_turn"


def test_composition_selects_xai_and_requires_key():
    settings = Settings(
        _env_file=None,
        llm_provider="xai",
        llm_model="grok-4.7",
        xai_api_key="xai-test-secret",
        xai_reasoning_effort="low",
    )
    assert isinstance(build_llm_provider(settings), XAIProvider)

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
