from app.llm.provider import LLMProvider
from app.llm.schemas import LLMRequest, LLMResponse
from app.privacy import PrivacyGateway


class LLMService:
    def __init__(self, *, privacy_gateway: PrivacyGateway, provider: LLMProvider,
                 critic_enabled: bool = False) -> None:
        self._privacy_gateway = privacy_gateway
        self._provider = provider
        self._critic_enabled = critic_enabled

    async def generate(self, request: LLMRequest) -> LLMResponse:
        sanitized_request = self._privacy_gateway.sanitize(request)
        original = await self._provider.generate(sanitized_request)
        if not self._critic_enabled:
            return original
        from app.llm.critic import second_opinion
        return await second_opinion(
            request=sanitized_request,
            original=original,
            generate=lambda req: self._provider.generate(self._privacy_gateway.sanitize(req)),
        )
