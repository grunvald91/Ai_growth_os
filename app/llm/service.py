from app.llm.provider import LLMProvider
from app.llm.schemas import LLMRequest, LLMResponse
from app.privacy import PrivacyGateway


class LLMService:
    def __init__(self, *, privacy_gateway: PrivacyGateway, provider: LLMProvider) -> None:
        self._privacy_gateway = privacy_gateway
        self._provider = provider

    async def generate(self, request: LLMRequest) -> LLMResponse:
        sanitized_request = self._privacy_gateway.sanitize(request)
        return await self._provider.generate(sanitized_request)
