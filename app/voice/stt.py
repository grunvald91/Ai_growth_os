"""Speech recognition behind an interface, like LLMProvider. The first adapter speaks the OpenAI-compatible
/audio/transcriptions protocol and is used with OpenRouter (Whisper and other models)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (1.0, 3.0)
# Hard limit of the provider's multipart upload, not a product rule.
PROVIDER_MAX_BYTES = 25 * 1024 * 1024


class STTError(RuntimeError):
    """Recognition failed. The message never contains audio, text or keys."""


@dataclass(frozen=True)
class Segment:
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None


@dataclass(frozen=True)
class Transcript:
    text: str
    duration_seconds: float | None = None
    segments: tuple[Segment, ...] = field(default_factory=tuple)
    model: str = ""
    # Price the provider itself reported for this call, when it does.
    cost_usd: float | None = None


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, audio: bytes, *, filename: str, content_type: str,
                         language: str = "ru", prompt: str | None = None) -> Transcript: ...


class OpenAICompatibleSTT:
    """POST {base_url}/audio/transcriptions with a multipart file. Works with OpenRouter and compatible gateways."""

    def __init__(self, *, api_key: str, model: str = "openai/whisper-large-v3",
                 base_url: str = "https://openrouter.ai/api/v1", timeout_seconds: float = 60.0,
                 proxy_url: str | None = None, send_prompt: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("A speech recognition API key is required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = httpx.Timeout(timeout_seconds, connect=15.0)
        self._proxy_url = proxy_url
        # OpenRouter accepts the field in multipart requests but ignores it, so it is off by default.
        self._send_prompt = send_prompt
        self._transport = transport

    async def transcribe(self, audio: bytes, *, filename: str, content_type: str,
                         language: str = "ru", prompt: str | None = None) -> Transcript:
        if len(audio) > PROVIDER_MAX_BYTES:
            raise STTError("Запись больше 25 МБ: распознавание не принимает такие файлы")
        content_type = content_type.split(";", 1)[0].strip() or "application/octet-stream"
        data = {"model": self._model, "language": language, "temperature": "0"}
        if prompt and self._send_prompt:
            data["prompt"] = prompt
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport,
                                     proxy=self._proxy_url if self._transport is None else None,
                                     trust_env=False, follow_redirects=False) as client:
            response = None
            # Segments with confidence help to drop silence; a model that cannot return them gets a plain request.
            for response_format in ("verbose_json", "json"):
                response = await self._post_with_retries(client, headers, {**data, "response_format": response_format},
                                                         audio, filename, content_type)
                if response.status_code != 400 or response_format == "json":
                    break
            if response is None or response.is_error:
                raise STTError(_status_message(response.status_code if response is not None else 0))
        try:
            payload = response.json()
            text = payload.get("text", "")
        except (ValueError, AttributeError):
            raise STTError("Распознавание вернуло неожиданный ответ") from None
        if not isinstance(text, str):
            raise STTError("Распознавание вернуло неожиданный ответ")
        segments = []
        for item in payload.get("segments") or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                segments.append(Segment(
                    text=item["text"],
                    no_speech_prob=_number(item.get("no_speech_prob")),
                    avg_logprob=_number(item.get("avg_logprob"))))
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        duration = _number(payload.get("duration"))
        if duration is None:
            duration = _number(usage.get("seconds"))
        return Transcript(text=text.strip(), duration_seconds=duration, segments=tuple(segments),
                          model=self._model, cost_usd=_number(usage.get("cost")))

    async def _post_with_retries(self, client, headers, data, audio, filename, content_type) -> httpx.Response:
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            try:
                response = await client.post("audio/transcriptions", headers=headers, data=data,
                                             files={"file": (filename, audio, content_type)})
            except httpx.TimeoutException:
                raise STTError("Распознавание не ответило вовремя") from None
            except httpx.TransportError:
                if attempt >= len(_RETRY_DELAYS_SECONDS):
                    raise STTError("Нет связи с сервисом распознавания") from None
            else:
                if response.status_code not in _RETRYABLE_STATUS_CODES or attempt >= len(_RETRY_DELAYS_SECONDS):
                    return response
            await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])
        raise STTError("Распознавание не дало ответа")  # unreachable: the last attempt returns or raises


def _status_message(status: int) -> str:
    # Only the code: the response body can echo the audio's text or the key.
    if status == 403:
        return "Распознавание отклонило доступ сервера (ошибка 403): проверьте прокси и ключ"
    if status == 401:
        return "Распознавание не приняло ключ (ошибка 401)"
    if status == 402:
        return "На счёте распознавания не хватает средств (ошибка 402)"
    return f"Распознавание вернуло ошибку {status}"


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
