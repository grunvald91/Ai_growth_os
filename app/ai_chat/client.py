"""ClaudeHub client adapter for ServGo AI Chat (streaming text, non-streaming text, and image generation)."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from app.ai_chat.files import validate_generated_image
from app.llm.limits import output_ceiling
from app.llm.model_catalog import ConfirmedModelCapabilities


class AiChatProviderError(RuntimeError):
    """Base error for ClaudeHub AI Chat calls."""


class AiChatOutcomeUnknownError(AiChatProviderError):
    """Raised when a paid request may have completed at the provider despite a timeout/disconnect."""


@dataclass(frozen=True)
class ChatStreamChunk:
    delta: str = ""
    done: bool = False
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class GeneratedImageResult:
    png_bytes: bytes
    width: int
    height: int
    sha256: str
    model: str


@runtime_checkable
class AiChatClient(Protocol):
    async def stream_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[dict],
        capabilities: ConfirmedModelCapabilities,
        conversation_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[ChatStreamChunk]: ...

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        capabilities: ConfirmedModelCapabilities,
        size: str | None = None,
    ) -> GeneratedImageResult: ...


def _extract_text_content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _parse_usage(reported) -> tuple[int | None, int | None]:
    if not isinstance(reported, dict):
        return None, None
    in_tok = reported.get("prompt_tokens", reported.get("input_tokens"))
    out_tok = reported.get("completion_tokens", reported.get("output_tokens"))
    if in_tok is None and out_tok is None:
        return None, None
    return (
        max(0, int(in_tok)) if in_tok is not None else None,
        max(0, int(out_tok)) if out_tok is not None else None,
    )


def _validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AiChatProviderError("ClaudeHub URL должен быть доверенным HTTPS-адресом")
    return base_url.rstrip("/") + "/"


class ClaudeHubAiChatClient:
    """OpenAI-compatible ClaudeHub adapter for AI Chat."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.claudehub.fun/v1",
        timeout_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        openai_api_key: str | None = None,
        claudehub_model_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        if (not api_key or not api_key.strip()) and not (openai_api_key and openai_api_key.strip()):
            raise AiChatProviderError("Ключ ClaudeHub (XAI_API_KEY) не настроен на сервере")
        self._api_key = (api_key or "").strip()
        self._base_url = _validate_base_url(base_url)
        self._openai_api_key = openai_api_key.strip() if openai_api_key and openai_api_key.strip() else None
        self._claudehub_model_ids = frozenset(claudehub_model_ids or ())
        self._timeout = httpx.Timeout(max(30.0, timeout_seconds), connect=15.0)
        self._transport = transport

    async def stream_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[dict],
        capabilities: ConfirmedModelCapabilities,
        conversation_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        if not self._api_key:
            raise AiChatProviderError("Ключ ClaudeHub (XAI_API_KEY) не настроен на сервере")
        api_messages: list[dict] = [{"role": "system", "content": system_prompt}, *messages]
        want_stream = bool(capabilities.supports_streaming)
        payload: dict[str, object] = {
            "model": model,
            "messages": api_messages,
            "max_completion_tokens": output_ceiling(capabilities.max_output_tokens),
            "stream": want_stream,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if model.lower().startswith("grok-") and conversation_id:
            headers["x-grok-conv-id"] = conversation_id[:200]

        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            if want_stream:
                try:
                    async with client.stream("POST", "chat/completions", headers=headers, json=payload) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            raise AiChatProviderError(
                                f"Ошибка провайдера ClaudeHub (HTTP {response.status_code})"
                            )
                        ctype = response.headers.get("content-type", "").lower()
                        if "text/event-stream" not in ctype and "application/json" in ctype:
                            raw_bytes = await response.aread()
                            data = json.loads(raw_bytes)
                            chunk = self._parse_non_stream_json(data, fallback_model=model)
                            yield chunk
                            return

                        in_tokens: int | None = None
                        out_tokens: int | None = None
                        resp_model: str | None = model
                        received_any = False

                        async for line in response.aiter_lines():
                            if cancel_event is not None and cancel_event.is_set():
                                return
                            if not line:
                                continue
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                yield ChatStreamChunk(
                                    delta="",
                                    done=True,
                                    model=resp_model,
                                    input_tokens=in_tokens,
                                    output_tokens=out_tokens,
                                )
                                return
                            try:
                                event_obj = json.loads(data_str)
                            except ValueError:
                                continue
                            if isinstance(event_obj.get("model"), str):
                                resp_model = event_obj["model"]
                            u_in, u_out = _parse_usage(event_obj.get("usage"))
                            if u_in is not None:
                                in_tokens = u_in
                            if u_out is not None:
                                out_tokens = u_out
                            choices = event_obj.get("choices") or []
                            if choices and isinstance(choices[0], dict):
                                delta_obj = choices[0].get("delta") or choices[0].get("message") or {}
                                text = _extract_text_content(delta_obj.get("content"))
                                if text:
                                    received_any = True
                                    yield ChatStreamChunk(delta=text, model=resp_model)
                        if received_any or in_tokens is not None or out_tokens is not None:
                            yield ChatStreamChunk(
                                delta="",
                                done=True,
                                model=resp_model,
                                input_tokens=in_tokens,
                                output_tokens=out_tokens,
                            )
                            return
                        raise AiChatProviderError("ClaudeHub вернул пустой поток ответа")
                except httpx.ReadTimeout as exc:
                    raise AiChatOutcomeUnknownError(
                        "Истекло время ожидания ответа от ClaudeHub; статус обработки у провайдера неизвестен."
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise AiChatProviderError("Превышено время ожидания ответа от ClaudeHub") from exc
                except httpx.HTTPError as exc:
                    raise AiChatProviderError("Сетевая ошибка при обращении к ClaudeHub") from exc
            else:
                try:
                    response = await client.post("chat/completions", headers=headers, json=payload)
                    if response.status_code >= 400:
                        raise AiChatProviderError(
                            f"Ошибка провайдера ClaudeHub (HTTP {response.status_code})"
                        )
                    data = response.json()
                except httpx.ReadTimeout as exc:
                    raise AiChatOutcomeUnknownError(
                        "Истекло время ожидания ответа от ClaudeHub; статус обработки у провайдера неизвестен."
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise AiChatProviderError("Превышено время ожидания ответа от ClaudeHub") from exc
                except (httpx.HTTPError, ValueError) as exc:
                    raise AiChatProviderError("Ошибка чтения ответа ClaudeHub") from exc
                yield self._parse_non_stream_json(data, fallback_model=model)

    @staticmethod
    def _parse_non_stream_json(data: dict, *, fallback_model: str) -> ChatStreamChunk:
        try:
            choice = data["choices"][0]
            content = _extract_text_content(choice["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AiChatProviderError("ClaudeHub вернул ответ в неожиданном формате") from exc
        if not content or not content.strip():
            raise AiChatProviderError("Модель вернула пустой ответ")
        in_tok, out_tok = _parse_usage(data.get("usage"))
        return ChatStreamChunk(
            delta=content,
            done=True,
            model=str(data.get("model") or fallback_model),
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        capabilities: ConfirmedModelCapabilities,
        size: str | None = None,
    ) -> GeneratedImageResult:
        if not capabilities.supports_image_generation:
            raise AiChatProviderError("Выбранная модель не поддерживает генерацию изображений")

        body: dict[str, object] = {"model": model, "prompt": prompt}
        if capabilities.image_sizes:
            chosen_size = size if size in capabilities.image_sizes else capabilities.image_sizes[0]
            body["size"] = chosen_size
        lower_model = model.lower()
        if lower_model.startswith("dall-e"):
            body["response_format"] = "b64_json"
        elif lower_model.startswith(("gpt-image-", "chatgpt-image-")):
            body["n"] = 1

        use_openai_direct = (
            self._openai_api_key is not None
            and lower_model.startswith(("gpt-image-", "chatgpt-image-", "dall-e"))
            and (not self._claudehub_model_ids or model not in self._claudehub_model_ids)
        )
        req_base_url = "https://api.openai.com/v1/" if use_openai_direct else self._base_url
        req_api_key = self._openai_api_key if use_openai_direct else self._api_key
        if not req_api_key:
            raise AiChatProviderError("Ключ ClaudeHub (XAI_API_KEY) не настроен на сервере")

        headers = {
            "Authorization": f"Bearer {req_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=req_base_url,
            timeout=httpx.Timeout(600.0, connect=15.0),
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            try:
                response = await client.post("images/generations", headers=headers, json=body)
                if response.status_code >= 400:
                    raise AiChatProviderError(
                        f"Ошибка генерации изображения в ClaudeHub (HTTP {response.status_code})"
                    )
                payload = response.json()
            except httpx.ReadTimeout as exc:
                raise AiChatOutcomeUnknownError(
                    "Истекло время ожидания генерации изображения; результат у провайдера неизвестен."
                ) from exc
            except httpx.TimeoutException as exc:
                raise AiChatProviderError("Превышено время ожидания генерации изображения") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise AiChatProviderError("Ошибка чтения ответа генерации изображения") from exc

        try:
            items = payload["data"]
            if not isinstance(items, list) or len(items) < 1 or not isinstance(items[0], dict):
                raise ValueError("Missing image item")
            item = items[0]
            encoded = item.get("b64_json")
            if encoded is None:
                uri = item.get("url") or (
                    item.get("image_url", {}).get("url")
                    if isinstance(item.get("image_url"), dict)
                    else item.get("image_url")
                )
                if not isinstance(uri, str) or not uri.startswith("data:image/"):
                    raise AiChatProviderError("Провайдер не вернул встроенное изображение (base64)")
                header, sep, encoded = uri.partition(",")
                if sep != "," or ";base64" not in header:
                    raise AiChatProviderError("Некорректный формат data URI изображения")
            if not isinstance(encoded, str) or len(encoded) > 28_000_000:
                raise AiChatProviderError("Некорректные base64-данные изображения")
            raw_bytes = base64.b64decode(encoded, validate=True)
        except AiChatProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, base64.binascii.Error) as exc:
            raise AiChatProviderError("Провайдер вернул некорректные данные изображения") from exc

        png_bytes, width, height, digest = validate_generated_image(raw_bytes)
        return GeneratedImageResult(
            png_bytes=png_bytes,
            width=width,
            height=height,
            sha256=digest,
            model=model,
        )
