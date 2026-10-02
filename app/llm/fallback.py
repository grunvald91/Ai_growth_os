import httpx

from app.llm.provider import LLMProvider
from app.llm.schemas import LLMRequest, LLMResponse


_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class FallbackProvider:
    """Try configured models in order, failing over only on transient provider errors."""

    def __init__(
        self,
        *,
        providers: list[tuple[str, LLMProvider]],
    ) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required")
        self._providers = providers

    async def generate(self, request: LLMRequest) -> LLMResponse:
        last_error: Exception | None = None
        for index, (model, provider) in enumerate(self._providers):
            candidate = request.model_copy(update={"model": model})
            try:
                return await provider.generate(candidate)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc

            if index == len(self._providers) - 1:
                break

        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM fallback chain exhausted without a response")
