"""xAI / Grok provider using the OpenAI-compatible Chat Completions API."""
import asyncio
from typing import Literal

import httpx

from app.llm.schemas import LLMRequest, LLMResponse, LLMUsage

ReasoningEffort = Literal["low", "medium", "high", "xhigh"]

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (1.0, 3.0, 7.0)


class XAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "grok-4.7",
        base_url: str = "https://api.x.ai/v1",
        reasoning_effort: ReasoningEffort = "medium",
        timeout_seconds: float = 300.0,
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

        selected_model = request.model or self._model
        payload: dict[str, object] = {
            "model": selected_model,
            "messages": messages,
            "max_completion_tokens": request.max_tokens,
        }
        if selected_model.lower().startswith("grok-"):
            payload["reasoning_effort"] = reasoning_effort
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        conversation_id = request.metadata.get("conversation_id")
        if (
            selected_model.lower().startswith("grok-")
            and isinstance(conversation_id, str)
            and conversation_id
        ):
            headers["x-grok-conv-id"] = conversation_id[:200]

        timeout = self._timeout
        provider_timeout = request.metadata.get("provider_timeout_seconds")
        if isinstance(provider_timeout, (int, float)):
            seconds = float(provider_timeout)
            if 5.0 <= seconds <= 600.0:
                timeout = httpx.Timeout(seconds)

        async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout) as client:
            response = None
            for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
                try:
                    response = await client.post("chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if (
                        exc.response.status_code not in _RETRYABLE_STATUS_CODES
                        or attempt >= len(_RETRY_DELAYS_SECONDS)
                    ):
                        raise
                except httpx.TimeoutException:
                    # A slow model should fail over to the next configured ClaudeHub model
                    # instead of spending the whole stage budget retrying the same model.
                    raise
                except httpx.TransportError:
                    if attempt >= len(_RETRY_DELAYS_SECONDS):
                        raise
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])

            if response is None:
                raise RuntimeError("xAI request did not produce a response")
            data = response.json()

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("xAI response did not contain assistant content") from exc
        if not isinstance(content, str) or not content:
            raise ValueError("xAI response content is empty")

        reported_usage = data.get("usage")
        # Absent or partial usage is UNKNOWN, not an observed 0/0 billable call.
        # Neither later critic aggregation nor reporting may treat it as complete.
        usage = None
        if isinstance(reported_usage, dict):
            input_tokens = reported_usage.get("prompt_tokens", reported_usage.get("input_tokens"))
            output_tokens = reported_usage.get("completion_tokens", reported_usage.get("output_tokens"))
            if input_tokens is not None and output_tokens is not None:
                usage = LLMUsage(input_tokens=max(0, int(input_tokens)),
                                 output_tokens=max(0, int(output_tokens)))

        finish_reason = choice.get("finish_reason")
        stop_reason = "end_turn" if finish_reason == "stop" else finish_reason

        return LLMResponse(
            content=content,
            model=str(data.get("model") or request.model or self._model),
            usage=usage,
            stop_reason=stop_reason,
        )
