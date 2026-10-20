import json

from app.llm.limits import output_ceiling
from app.llm.schemas import LLMRequest, LLMResponse, LLMUsage


class AnthropicProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.anthropic.com") -> None:
        if not api_key:
            raise ValueError("An API key is required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    async def generate(self, request: LLMRequest) -> LLMResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._api_key, base_url=self._base_url)
        parameters = {
            "model": request.model or self._model,
            "max_tokens": output_ceiling(request.max_tokens),
            "messages": [message.model_dump() for message in request.messages],
        }
        if request.system_prompt is not None:
            parameters["system"] = request.system_prompt
        if request.response_schema is not None:
            # This adapter uses Anthropic messages.create, which has no
            # response_schema argument. Provide the FULL schema explicitly,
            # including $defs and required decision fields, rather than
            # asking the model to guess an unseen JSON contract.
            schema_text = json.dumps(request.response_schema, ensure_ascii=False)
            parameters["system"] = (parameters.get("system", "") +
                "\\nReturn ONLY one JSON object matching the following full response schema: \\n" +
                schema_text)
        if request.temperature is not None:
            parameters["temperature"] = request.temperature

        response = await client.messages.create(**parameters)
        content = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            content=content,
            model=response.model,
            usage=LLMUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            stop_reason=response.stop_reason,
        )
