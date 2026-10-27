"""Voice module bookkeeping. No audio and no text are stored here: only counts, timings and outcomes."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class VoiceUsage(UUIDMixin, TimestampMixin, Base):
    """One dictation. The id doubles as the event id a client sends back with a manual correction later."""
    __tablename__ = "voice_usage"

    account: Mapped[str] = mapped_column(String(200), nullable=False)
    project: Mapped[str | None] = mapped_column(String(64))
    # ok | cleaned_fallback | empty | too_long | stt_error | preview (live view while recording)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(60))
    audio_seconds: Mapped[float | None] = mapped_column(Float)
    stt_model: Mapped[str | None] = mapped_column(String(100))
    llm_model: Mapped[str | None] = mapped_column(String(100))
    llm_input_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_output_tokens: Mapped[int | None] = mapped_column(Integer)
    stt_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal(0), nullable=False)
    incurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class VoiceCostAlert(UUIDMixin, TimestampMixin, Base):
    """At most one notice per day about unusually high spend."""
    __tablename__ = "voice_cost_alerts"

    day: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    day_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    usual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
