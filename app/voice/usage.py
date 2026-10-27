"""Spend bookkeeping for dictation and the notice to the owner when a day costs far more than usual."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.voice.models import VoiceCostAlert, VoiceUsage

USUAL_DAYS = 7


def _decimal(value: str, default: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        parsed = Decimal(default)
    return parsed if parsed.is_finite() and parsed >= 0 else Decimal(default)


def estimate_cost(settings: Settings, *, audio_seconds: float | None, input_tokens: int | None,
                  output_tokens: int | None, stt_cost_usd: float | None = None) -> Decimal:
    """Recognition cost as the provider reported it, else from the operator's rate; an unknown quantity counts as
    zero, never as a guess."""
    if stt_cost_usd is not None:
        stt = Decimal(str(stt_cost_usd))
    else:
        stt = Decimal(str(audio_seconds or 0)) / Decimal(3600) * _decimal(settings.voice_stt_usd_per_hour, "0.03")
    llm_in = Decimal(input_tokens or 0) * _decimal(settings.voice_llm_input_usd_per_million, "1.0")
    llm_out = Decimal(output_tokens or 0) * _decimal(settings.voice_llm_output_usd_per_million, "5.0")
    return stt + (llm_in + llm_out) / Decimal(1_000_000)


def _day_cost(db: Session, start: datetime, end: datetime) -> Decimal:
    total = db.scalar(select(func.coalesce(func.sum(VoiceUsage.estimated_cost_usd), 0))
                      .where(VoiceUsage.incurred_at >= start, VoiceUsage.incurred_at < end))
    return Decimal(str(total or 0))


def spend_alert(db: Session, settings: Settings, now: datetime | None = None) -> str | None:
    """Message for the owner if today's spend is unusually high and no notice was sent today, else None.

    "Usual" is the average of the previous days (up to a week) that had any dictation. Without such days the
    absolute floor alone decides, so a first busy day still gets noticed.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    if db.scalar(select(VoiceCostAlert.id).where(VoiceCostAlert.day == today)) is not None:
        return None
    today_cost = _day_cost(db, start, start + timedelta(days=1))
    floor = _decimal(settings.voice_cost_alert_min_usd, "0.5")
    if today_cost < floor:
        return None
    previous = [_day_cost(db, start - timedelta(days=offset), start - timedelta(days=offset - 1))
                for offset in range(1, USUAL_DAYS + 1)]
    active = [cost for cost in previous if cost > 0]
    usual = sum(active) / len(active) if active else None
    if usual is not None and today_cost < usual * Decimal(str(settings.voice_cost_alert_factor)):
        return None
    db.add(VoiceCostAlert(day=today, day_cost_usd=today_cost, usual_cost_usd=usual))
    db.commit()
    lines = ["🎙 Голосовой ввод: расход за сегодня заметно выше обычного.",
             f"Сегодня (оценка): ${today_cost:.2f}"]
    lines.append(f"Обычно в день: ${usual:.2f}" if usual is not None
                 else "Прошлых дней с диктовкой ещё нет, сравнивать не с чем.")
    lines.append("Если это ты, ничего делать не нужно. Если нет, проверь, не утёк ли ключ доступа к голосовому модулю.")
    return "\n".join(lines)


async def notify_owner(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or settings.telegram_owner_chat_id is None:
        return
    from app.telegram_control_bot import TelegramAPI
    try:
        await TelegramAPI(settings.telegram_bot_token, settings.telegram_proxy_url).call(
            "sendMessage", {"chat_id": settings.telegram_owner_chat_id, "text": text})
    except Exception:
        # A notice must never break a dictation.
        return
