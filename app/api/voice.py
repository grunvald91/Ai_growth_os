"""Dictation endpoints: audio in, corrected text out.

Two doors to the same pipeline, both closed without credentials:
- /studio/voice/dictate: for pages inside the Studio (its own sign-in and origin checks);
- /studio/voice/service/dictate: for bots and other pages, with the shared key in X-Voice-Key. Without
  VOICE_API_KEY on the server this door stays shut.
Recognition (OpenRouter) and model keys live only on the server. No audio and no text are stored.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.core.composition import SAFE_LLM_METADATA_FIELDS
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.llm.service import LLMService
from app.llm.xai import XAIProvider
from app.privacy import PrivacyGateway
from app.voice.dictionary import SLUG
from app.voice.service import DictationResult, VoiceService
from app.voice.stt import OpenAICompatibleSTT, STTError, STTProvider
from app.voice.usage import notify_owner, spend_alert

studio_router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
service_router = APIRouter()
_ACCOUNT = re.compile(r"^[\w.@:+-]{1,200}$")


def get_stt(settings: Settings = Depends(get_settings)) -> STTProvider | None:
    """Recognition adapter; tests replace this dependency. None means no OpenRouter key is configured.

    From the production server OpenRouter answers 403 directly, so the already configured trusted proxy
    (WHITEBOARD_PROXY_URL, the same connection the whiteboard video uses) carries these calls too."""
    if not settings.openrouter_api_key:
        return None
    return OpenAICompatibleSTT(api_key=settings.openrouter_api_key, model=settings.voice_stt_model,
                               base_url=settings.voice_stt_base_url, proxy_url=settings.whiteboard_proxy_url)


def get_voice_llm(settings: Settings = Depends(get_settings)) -> LLMService | None:
    """The correction model behind the same gateway as every other model call. None means no gateway key."""
    if settings.llm_provider != "xai" or not settings.xai_api_key:
        return None
    provider = XAIProvider(api_key=settings.xai_api_key, model=settings.voice_cleanup_model,
                           base_url=settings.xai_base_url, reasoning_effort="low",
                           timeout_seconds=settings.voice_cleanup_timeout_seconds)
    return LLMService(privacy_gateway=PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS),
                      provider=provider, critic_enabled=False)


def require_voice_key(x_voice_key: str = Header(default=""), settings: Settings = Depends(get_settings)) -> None:
    key = settings.voice_api_key or ""
    # Same answer for "no key configured" and "wrong key": the door is shut and says nothing about why.
    if len(key) < 32 or not secrets.compare_digest(x_voice_key.encode(), key.encode()):
        raise HTTPException(401, "Нет доступа")


def _parse_context(raw: str) -> list[str]:
    """`context` is a JSON list of the last dialogue lines; anything else is ignored rather than guessed."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _body(result: DictationResult) -> dict[str, Any]:
    return {
        "event_id": str(result.event_id) if result.event_id else None,
        "text": result.text, "raw_text": result.raw_text, "empty": result.empty,
        "empty_reason": result.empty_reason, "cleaned": result.cleaned,
        "fallback_reason": result.fallback_reason, "replacements": result.replacements,
        "duration_seconds": result.duration_seconds,
    }


async def _dictate(*, audio: UploadFile, account: str, project: str | None, context: str, duration_ms: int | None,
                   language: str, settings: Settings, db: Session, stt: STTProvider | None,
                   llm: LLMService | None, background: BackgroundTasks) -> dict[str, Any]:
    if stt is None:
        raise HTTPException(503, "Распознавание речи не настроено на сервере")
    if project is not None and not SLUG.match(project):
        raise HTTPException(422, "Неверное имя проекта")
    data = await audio.read()
    try:
        result = await VoiceService(db=db, settings=settings, stt=stt, llm=llm).dictate(
            data, filename=audio.filename or "audio", content_type=audio.content_type or "application/octet-stream",
            account=account, project=project, context=_parse_context(context)[-3:], duration_ms=duration_ms,
            language=language)
    except STTError as exc:
        raise HTTPException(502, str(exc)) from None
    message = spend_alert(db, settings)
    if message:
        background.add_task(notify_owner, settings, message)
    return _body(result)


@studio_router.post("/studio/voice/preview")
async def preview_in_studio(
    request: Request, background: BackgroundTasks, audio: UploadFile = File(...), project: str | None = Form(None),
    duration_ms: int | None = Form(None), language: str = Form("ru"),
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
    stt: STTProvider | None = Depends(get_stt),
):
    """Live view while recording: the words recognised so far, not corrected, not stored. The final text still goes
    through /studio/voice/dictate when the person stops."""
    if stt is None:
        raise HTTPException(503, "Распознавание речи не настроено на сервере")
    if project is not None and not SLUG.match(project):
        raise HTTPException(422, "Неверное имя проекта")
    account = getattr(request.state, "studio_login", None) or "local-owner"
    data = await audio.read()
    try:
        text, reason = await VoiceService(db=db, settings=settings, stt=stt, llm=None).preview(
            data, filename=audio.filename or "audio", content_type=audio.content_type or "application/octet-stream",
            account=account, project=project, duration_ms=duration_ms, language=language)
    except STTError as exc:
        raise HTTPException(502, str(exc)) from None
    message = spend_alert(db, settings)
    if message:
        background.add_task(notify_owner, settings, message)
    return {"text": text, "empty": not text, "empty_reason": reason}


@studio_router.post("/studio/voice/dictate")
async def dictate_in_studio(
    request: Request, background: BackgroundTasks, audio: UploadFile = File(...), project: str | None = Form(None),
    context: str = Form(""), duration_ms: int | None = Form(None), language: str = Form("ru"),
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
    stt: STTProvider | None = Depends(get_stt), llm: LLMService | None = Depends(get_voice_llm),
):
    account = getattr(request.state, "studio_login", None) or "local-owner"
    return await _dictate(audio=audio, account=account, project=project, context=context, duration_ms=duration_ms,
                          language=language, settings=settings, db=db, stt=stt, llm=llm, background=background)


@service_router.post("/studio/voice/service/dictate", dependencies=[Depends(require_voice_key)])
async def dictate_for_service(
    background: BackgroundTasks, audio: UploadFile = File(...), user: str = Form(...), project: str | None = Form(None),
    context: str = Form(""), duration_ms: int | None = Form(None), language: str = Form("ru"),
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
    stt: STTProvider | None = Depends(get_stt), llm: LLMService | None = Depends(get_voice_llm),
):
    if not _ACCOUNT.match(user):
        raise HTTPException(422, "Неверный идентификатор пользователя")
    return await _dictate(audio=audio, account=user, project=project, context=context, duration_ms=duration_ms,
                          language=language, settings=settings, db=db, stt=stt, llm=llm, background=background)
