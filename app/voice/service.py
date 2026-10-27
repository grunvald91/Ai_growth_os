"""Dictation pipeline: silence check -> recognition -> silence filter -> sound-alike words -> correction -> checks."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.llm.service import LLMService
from app.voice.cleanup import clean_text, replacements
from app.voice.dictionary import load_dictionary, recognizer_prompt, select_for_text
from app.voice.models import VoiceUsage
from app.voice.silence import precheck_audio, speech_text
from app.voice.stt import STTError, STTProvider
from app.voice.usage import estimate_cost


@dataclass
class DictationResult:
    event_id: UUID | None
    text: str
    raw_text: str
    empty: bool = False
    empty_reason: str | None = None
    cleaned: bool = False
    fallback_reason: str | None = None
    replacements: list[dict[str, str]] = field(default_factory=list)
    duration_seconds: float | None = None


class VoiceService:
    def __init__(self, *, db: Session, settings: Settings, stt: STTProvider | None, llm: LLMService | None) -> None:
        self._db, self._settings, self._stt, self._llm = db, settings, stt, llm

    async def dictate(self, audio: bytes, *, filename: str, content_type: str, account: str, project: str | None,
                      context: list[str], duration_ms: int | None = None, language: str = "ru") -> DictationResult:
        settings = self._settings
        context = [line.strip() for line in context if line and line.strip()]
        if duration_ms is not None and duration_ms > settings.voice_max_audio_seconds * 1000:
            return self._finish(account, project, "too_long", None, None, empty_reason="too_long",
                                detail=f"{duration_ms // 1000}s")
        check = precheck_audio(audio, duration_ms=duration_ms, min_audio_ms=settings.voice_min_audio_ms,
                               silence_rms=settings.voice_silence_rms)
        if not check.ok:
            # Nothing is sent anywhere: no recognition spend and no invented text.
            return self._finish(account, project, "empty", None, None, empty_reason=check.reason)
        if self._stt is None:
            raise STTError("Распознавание не настроено")

        terms = load_dictionary(Path(settings.products_root), project)
        started = time.monotonic()
        try:
            transcript = await self._stt.transcribe(
                audio, filename=filename, content_type=content_type, language=language,
                prompt=recognizer_prompt(terms, context))
        except STTError as exc:
            self._finish(account, project, "stt_error", None, None, detail=str(exc)[:60], empty_reason="stt_error")
            raise
        stt_ms = int((time.monotonic() - started) * 1000)
        duration = transcript.duration_seconds
        if duration is not None and duration > settings.voice_max_audio_seconds:
            return self._finish(account, project, "too_long", transcript, stt_ms, empty_reason="too_long",
                                detail=f"{int(duration)}s")
        raw = speech_text(transcript)
        if not raw:
            return self._finish(account, project, "empty", transcript, stt_ms, empty_reason="silence")

        selected = select_for_text(terms, raw)
        started = time.monotonic()
        cleanup = await clean_text(raw, llm=self._llm, model=settings.voice_cleanup_model, terms=selected,
                                   context=context, timeout_seconds=settings.voice_cleanup_timeout_seconds)
        llm_ms = int((time.monotonic() - started) * 1000)
        return self._finish(
            account, project, "ok" if cleanup.cleaned else "cleaned_fallback", transcript, stt_ms,
            raw=raw, cleanup=cleanup, llm_ms=llm_ms)

    async def preview(self, audio: bytes, *, filename: str, content_type: str, account: str, project: str | None,
                      duration_ms: int | None = None, language: str = "ru") -> tuple[str, str | None]:
        """Recognised words for the live view while a person is still speaking: no correction, nothing stored.

        Returns (text, empty_reason). The audio is the whole recording so far, so each answer replaces the last.
        The call is counted like any other recognition so the spend notice sees it."""
        settings = self._settings
        if duration_ms is not None and duration_ms > settings.voice_max_audio_seconds * 1000:
            return "", "too_long"
        check = precheck_audio(audio, duration_ms=duration_ms, min_audio_ms=settings.voice_min_audio_ms,
                               silence_rms=settings.voice_silence_rms)
        if not check.ok:
            return "", check.reason
        if self._stt is None:
            raise STTError("Распознавание не настроено")
        started = time.monotonic()
        try:
            transcript = await self._stt.transcribe(audio, filename=filename, content_type=content_type,
                                                    language=language, prompt=None)
        except STTError as exc:
            self._finish(account, project, "stt_error", None, None, detail=str(exc)[:60], empty_reason="stt_error")
            raise
        stt_ms = int((time.monotonic() - started) * 1000)
        text = speech_text(transcript)
        self._finish(account, project, "preview", transcript, stt_ms, empty_reason=None if text else "silence")
        return text, None if text else "silence"

    def _finish(self, account, project, outcome, transcript, stt_ms, *, raw: str = "", cleanup=None,
                llm_ms: int | None = None, empty_reason: str | None = None, detail: str | None = None) -> DictationResult:
        settings = self._settings
        audio_seconds = transcript.duration_seconds if transcript else None
        input_tokens = cleanup.input_tokens if cleanup else None
        output_tokens = cleanup.output_tokens if cleanup else None
        row = VoiceUsage(
            account=account[:200], project=(project or None) and project[:64], outcome=outcome,
            detail=detail or (cleanup.fallback_reason if cleanup else None),
            audio_seconds=audio_seconds, stt_model=transcript.model if transcript else None,
            llm_model=cleanup.model if cleanup else None, llm_input_tokens=input_tokens,
            llm_output_tokens=output_tokens, stt_ms=stt_ms, llm_ms=llm_ms,
            estimated_cost_usd=estimate_cost(settings, audio_seconds=audio_seconds, input_tokens=input_tokens,
                                             output_tokens=output_tokens,
                                             stt_cost_usd=transcript.cost_usd if transcript else None),
            incurred_at=datetime.now(timezone.utc))
        self._db.add(row)
        self._db.commit()
        if cleanup is None:
            return DictationResult(event_id=row.id, text="", raw_text="", empty=True, empty_reason=empty_reason,
                                   duration_seconds=audio_seconds)
        return DictationResult(
            event_id=row.id, text=cleanup.text, raw_text=raw, cleaned=cleanup.cleaned,
            fallback_reason=cleanup.fallback_reason,
            replacements=replacements(raw, cleanup.text) if cleanup.cleaned else [],
            duration_seconds=audio_seconds)
