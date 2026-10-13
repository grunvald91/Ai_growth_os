from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int = Field(default=1024, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] | None = None


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    content: str
    model: str
    usage: LLMUsage | None = None
    stop_reason: str | None = None
    quality_review: dict[str, Any] | None = None
