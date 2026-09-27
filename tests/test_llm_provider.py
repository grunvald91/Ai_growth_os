import sys
from types import SimpleNamespace

import pytest

from app.llm.anthropic import AnthropicProvider
from app.llm.provider import LLMProvider
from app.llm.schemas import LLMMessage, LLMRequest


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_neutral_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Messages:
        async def create(self, **parameters):
            captured.update(parameters)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Answer")],
                model="configured-model",
                usage=SimpleNamespace(input_tokens=4, output_tokens=2),
                stop_reason="end_turn",
            )

    class Client:
        def __init__(self, **parameters):
            captured["client"] = parameters
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=Client))
    provider = AnthropicProvider(api_key="test-placeholder", model="configured-model", base_url="https://example.invalid")

    response = await provider.generate(
        LLMRequest(messages=[LLMMessage(role="user", content="Hello")], system_prompt="Be concise")
    )

    assert isinstance(provider, LLMProvider)
    assert captured["client"] == {"api_key": "test-placeholder", "base_url": "https://example.invalid"}
    assert captured["model"] == "configured-model"
    assert captured["system"] == "Be concise"
    assert response.content == "Answer"
    assert response.usage is not None and response.usage.output_tokens == 2
