from typing import Protocol, runtime_checkable

from app.llm.schemas import LLMRequest, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
