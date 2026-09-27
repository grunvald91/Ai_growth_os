from app.llm.schemas import LLMRequest, LLMResponse, LLMUsage


class FakeLLMProvider:
    def __init__(self, response: LLMResponse | None = None) -> None:
        self.response = response or LLMResponse(content="Fake response", model="fake")
        self.requests: list[LLMRequest] = []

    @property
    def last_request(self) -> LLMRequest | None:
        return self.requests[-1] if self.requests else None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        return self.response.model_copy(deep=True)


__all__ = ["FakeLLMProvider", "LLMResponse", "LLMUsage"]
