"""xAI / Grok provider using the OpenAI-compatible Chat Completions API."""
from typing import Literal

import httpx

from app.llm.schemas import LLMRequest, LLMResponse, LLMUsage

ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


class XAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "grok-4.7",
        base_url: str = "https://api.x.ai/v1",
        reasoning_effort: ReasoningEffort = "medium",
        timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("An xAI API key is required")
        if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("Unsupported xAI reasoning effort")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/") + "/"
        self._reasoning_effort = reasoning_effort
        self._timeout = httpx.Timeout(timeout_seconds)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system_prompt is not None:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(message.model_dump() for message in request.messages)

        reasoning_effort = request.metadata.get("reasoning_effort", self._reasoning_effort)
        if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("Unsupported xAI reasoning effort")

        payload: dict[str, object] = {
            "model": request.model or self._model,
            "messages": messages,
            "max_completion_tokens": request.max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        conversation_id = request.metadata.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id:
            headers["x-grok-conv-id"] = conversation_id[:200]

        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.post("chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("xAI response did not contain assistant content") from exc
        if not isinstance(content, str) or not content:
            raise ValueError("xAI response content is empty")

        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))

        finish_reason = choice.get("finish_reason")
        stop_reason = "end_turn" if finish_reason == "stop" else finish_reason

        return LLMResponse(
            content=content,
            model=str(data.get("model") or request.model or self._model),
            usage=LLMUsage(
                input_tokens=max(0, int(input_tokens or 0)),
                output_tokens=max(0, int(output_tokens or 0)),
            ),
            stop_reason=stop_reason,
        )
