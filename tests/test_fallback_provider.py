import httpx
import pytest

from app.llm.fallback import FallbackProvider
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse


class RecordingProvider:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.models = []

    async def generate(self, request):
        self.models.append(request.model)
        if self.error is not None:
            raise self.error
        return self.result


def _http_error(status: int):
    request = httpx.Request("POST", "https://provider.invalid/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": "failed"}})
    return httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.asyncio
async def test_fallback_switches_model_after_transient_http_failure():
    primary = RecordingProvider(error=_http_error(502))
    fallback = RecordingProvider(
        result=LLMResponse(content='{"ok":true}', model="gemini-flash-3.8")
    )
    provider = FallbackProvider(
        providers=[
            ("deepseek-v4.1-flash", primary),
            ("gemini-flash-3.8", fallback),
        ]
    )

    result = await provider.generate(
        LLMRequest(
            messages=[LLMMessage(role="user", content="hello")],
            model="deepseek-v4.1-flash",
        )
    )

    assert result.model == "gemini-flash-3.8"
    assert primary.models == ["deepseek-v4.1-flash"]
    assert fallback.models == ["gemini-flash-3.8"]


@pytest.mark.asyncio
async def test_fallback_does_not_mask_nontransient_400():
    primary = RecordingProvider(error=_http_error(400))
    fallback = RecordingProvider(
        result=LLMResponse(content='{"ok":true}', model="gemini-flash-3.8")
    )
    provider = FallbackProvider(
        providers=[
            ("deepseek-v4.1-flash", primary),
            ("gemini-flash-3.8", fallback),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")])
        )

    assert primary.models == ["deepseek-v4.1-flash"]
    assert fallback.models == []
