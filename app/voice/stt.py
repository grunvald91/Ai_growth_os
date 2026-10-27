"""Speech recognition behind an interface, like LLMProvider. Groq (Whisper) is the first adapter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (1.0, 3.0)
# Hard limit of the Groq transcription endpoint (free tier), not a product rule.
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


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, audio: bytes, *, filename: str, content_type: str,
                         language: str = "ru", prompt: str | None = None) -> Transcript: ...


class GroqWhisperProvider:
    def __init__(self, *, api_key: str, model: str = "whisper-large-v3-turbo",
                 base_url: str = "https://api.groq.com/openai/v1", timeout_seconds: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("A Groq API key is required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def transcribe(self, audio: bytes, *, filename: str, content_type: str,
                         language: str = "ru", prompt: str | None = None) -> Transcript:
        if len(audio) > PROVIDER_MAX_BYTES:
            raise STTError("Запись больше 25 МБ: распознавание не принимает такие файлы")
        data = {"model": self._model, "language": language, "response_format": "verbose_json",
                "temperature": "0"}
        if prompt:
            data["prompt"] = prompt
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                     transport=self._transport, trust_env=False) as client:
            response = None
            for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
                try:
                    response = await client.post(
                        "audio/transcriptions", headers=headers, data=data,
                        files={"file": (filename, audio, content_type)})
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if (exc.response.status_code not in _RETRYABLE_STATUS_CODES
                            or attempt >= len(_RETRY_DELAYS_SECONDS)):
                        # Only the status code: the body can echo the prompt.
                        raise STTError(f"Распознавание вернуло ошибку {exc.response.status_code}") from None
                except httpx.TimeoutException:
                    raise STTError("Распознавание не ответило вовремя") from None
                except httpx.TransportError:
                    if attempt >= len(_RETRY_DELAYS_SECONDS):
                        raise STTError("Нет связи с сервисом распознавания") from None
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])
            if response is None:
                raise STTError("Распознавание не дало ответа")
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
        return Transcript(text=text.strip(), duration_seconds=_number(payload.get("duration")),
                          segments=tuple(segments), model=self._model)


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
