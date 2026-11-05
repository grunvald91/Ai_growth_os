"""ClaudeHub transport. Internal messages use OpenAI's tool-call wire format."""
from __future__ import annotations

import json
import os
import uuid
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Safe public error; never contains upstream response bodies or credentials."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    api_format: str = "openai"

    @classmethod
    def from_env(cls):
        # The booking assistant uses a small, dedicated model by default.
        # ClaudeHub's general model remains unchanged for other services.
        model = os.getenv("CLAUDEHUB_CHAT_MODEL") or "gpt-6-luna"
        api_format = os.getenv("CLAUDEHUB_CHAT_API_FORMAT") or (
            "openai" if model == "gpt-6-luna"
            else os.getenv("CLAUDEHUB_API_FORMAT", "openai")
        )
        config = cls(
            os.getenv("CLAUDEHUB_BASE_URL", "").rstrip("/"),
            os.getenv("CLAUDEHUB_API_KEY", ""),
            model,
            api_format,
        )
        if not all((config.base_url, config.api_key, config.model)):
            raise ProviderError("Чат-помощник пока не подключён. Используйте обычную форму записи.")
        if config.api_format not in {"openai", "anthropic"}:
            raise ProviderError("Неверная настройка формата API чат-помощника.")
        if config.model == "gpt-6-luna" and config.api_format != "openai":
            raise ProviderError("GPT-6 Luna требует OpenAI-совместимый формат API.")
        return config


def anthropic_payload(messages: list[dict], tools: list[dict], config: LLMConfig) -> dict:
    converted = []
    system = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system.append(message["content"])
            continue
        blocks = []
        if role == "tool":
            role = "user"
            blocks.append({"type": "tool_result", "tool_use_id": message["tool_call_id"],
                           "content": message["content"]})
        else:
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": call["function"]["name"],
                               "input": json.loads(call["function"]["arguments"])})
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    payload = {"model": config.model, "system": "\n\n".join(system),
               "messages": converted, "max_tokens": 850, "temperature": 0.3}
    if tools:
        payload["tools"] = [{"name": t["function"]["name"],
                              "description": t["function"]["description"],
                              "input_schema": t["function"]["parameters"]} for t in tools]
    return payload


class ClaudeHubClient:
    def __init__(self, config: LLMConfig | None = None, transport=None):
        self.config = config or LLMConfig.from_env()
        self.transport = transport
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        config = self.config
        if config.api_format == "anthropic":
            url = config.base_url + "/messages"
            headers = {"x-api-key": config.api_key, "anthropic-version": "2023-06-01"}
            payload = anthropic_payload(messages, tools, config)
        else:
            url = config.base_url + "/chat/completions"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            if config.model == "gpt-6-luna":
                # GPT-6 Luna's Chat Completions function calling requires
                # reasoning_effort=none. Use its current completion-token
                # parameter and omit legacy sampling settings.
                payload = {"model": config.model, "messages": messages,
                           "reasoning_effort": "none", "max_completion_tokens": 850}
            else:
                payload = {"model": config.model, "messages": messages,
                           "temperature": 0.3, "max_tokens": 1200}
            if tools:
                payload.update(tools=tools, tool_choice="auto")
        started = time.perf_counter()
        try:
            # Reuse the TLS connection across consecutive model/tool rounds.
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=40, transport=self.transport)
            response = await self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info("ServGo sales LLM request completed in %.2fs", time.perf_counter() - started)
            if config.api_format == "anthropic":
                content = []
                calls = []
                for block in data["content"]:
                    if block["type"] == "text":
                        content.append(block["text"])
                    elif block["type"] == "tool_use":
                        calls.append({"id": block["id"], "type": "function", "function": {
                            "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
                message = {"content": "\n".join(content), "tool_calls": calls}
            else:
                message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 8:
                raise ValueError("Invalid tool calls")
            normalized = []
            for call in calls:
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
                if not isinstance(name, str) or not isinstance(arguments, str):
                    raise ValueError("Invalid tool call")
                normalized.append({"id": call.get("id") or str(uuid.uuid4()), "type": "function",
                                   "function": {"name": name, "arguments": arguments}})
            content = message.get("content") or ""
            if not isinstance(content, str):
                raise ValueError("Invalid content")
            result = {"role": "assistant", "content": content}
            if normalized:
                result["tool_calls"] = normalized
            return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Чат-помощник временно недоступен. Попробуйте позже или откройте форму записи.") from exc
