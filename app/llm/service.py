from typing import Callable

from app.llm.provider import LLMProvider
from app.llm.schemas import LLMRequest, LLMResponse
from app.privacy import PrivacyGateway


class LLMService:
    def __init__(self, *, privacy_gateway: PrivacyGateway, provider: LLMProvider,
                 critic_enabled: bool = False, critic_limits: dict | None = None) -> None:
        self._privacy_gateway = privacy_gateway
        self._provider = provider
        self._critic_enabled = critic_enabled
        if critic_limits is None and critic_enabled:
            from app.core.config import get_settings
            settings = get_settings()
            critic_limits = {
                "max_context_chars": settings.agent_critic_max_context_chars,
                "review_timeout": settings.agent_critic_review_timeout_seconds,
                "revision_timeout": settings.agent_critic_revision_timeout_seconds,
            }
        self._critic_limits = critic_limits or {}

    @property
    def extra_timeout_seconds(self) -> int:
        if not self._critic_enabled:
            return 0
        from app.llm.critic import REVIEW_TIMEOUT, REVISION_TIMEOUT
        review = self._critic_limits.get("review_timeout", REVIEW_TIMEOUT)
        revision = self._critic_limits.get("revision_timeout", REVISION_TIMEOUT)
        return int(review + revision + 5)

    async def generate(self, request: LLMRequest, *,
                       validate_revision: Callable[[dict], None] | None = None,
                       on_observed: Callable[[str, LLMResponse], None] | None = None,
                       on_review_progress: Callable[[LLMResponse], None] | None = None) -> LLMResponse:
        sanitized_request = self._privacy_gateway.sanitize(request)

        def observe(stage: str, reply: LLMResponse) -> LLMResponse:
            if on_observed is not None:
                # Caller persists the completed model call immediately.
                # Deliberately do not swallow persistence failures.
                on_observed(stage, reply)
            return reply

        original = observe("author", await self._provider.generate(sanitized_request))
        if not self._critic_enabled:
            return original

        async def review_generate(next_request: LLMRequest) -> LLMResponse:
            stage = ("critic" if next_request.metadata.get("task_type") == "quality_review"
                     else "revision")
            response = await self._provider.generate(self._privacy_gateway.sanitize(next_request))
            return observe(stage, response)

        from app.llm.critic import second_opinion
        return await second_opinion(
            request=sanitized_request,
            original=original,
            generate=review_generate,
            validate_revision=validate_revision,
            on_review_progress=on_review_progress,
            **self._critic_limits,
        )
