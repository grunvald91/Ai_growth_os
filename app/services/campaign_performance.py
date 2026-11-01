"""Evidence-first performance comparison for Growth V4 campaigns.

No ROI, client count, success verdict or causal attribution is inferred from
free-text hypotheses. Manual observations are labeled as user-supplied.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Experiment, Metric

STAGES = {"baseline": "До кампании", "result": "После запуска"}
_AMOUNT = re.compile(r"[+-]?(?:\d+(?:\.\d{1,4})?|\.\d{1,4})\Z")


class CampaignMeasurementError(ValueError):
    pass


@dataclass(frozen=True)
class PerformanceComparison:
    baseline: Metric | None = None
    result: Metric | None = None
    absolute_change: Decimal | None = None
    percent_change: Decimal | None = None
    explanation: str = ""

    @property
    def comparable(self) -> bool:
        return self.absolute_change is not None


def parse_amount(raw: str) -> Decimal:
    value = (raw or "").strip().replace(",", ".")
    if not _AMOUNT.fullmatch(value):
        raise CampaignMeasurementError("Введите числовое значение, до четырёх знаков после запятой")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise CampaignMeasurementError("Некорректное значение показателя") from None
    if not amount.is_finite() or abs(amount) >= Decimal("100000000000000"):
        raise CampaignMeasurementError("Значение показателя вне допустимого диапазона")
    return amount


def add_measurement(
    db: Session, *, product_id: UUID, experiment_id: UUID,
    stage: str, value: str, unit: str, source: str, observed_on: str,
    actor: str,
) -> Metric:
    campaign = db.scalar(select(Experiment).where(
        Experiment.product_id == product_id, Experiment.id == experiment_id,
    ))
    if campaign is None:
        raise LookupError("Кампания не найдена у выбранной организации")
    metric_name = (campaign.target_metric or "").strip()
    if not metric_name:
        raise CampaignMeasurementError("Укажите основной показатель кампании перед измерением")
    if stage not in STAGES:
        raise CampaignMeasurementError("Выберите период: до кампании или после запуска")
    amount = parse_amount(value)
    clean_unit = (unit or "").strip()
    clean_source = (source or "").strip()
    if len(clean_unit) > 50:
        raise CampaignMeasurementError("Слишком длинная единица измерения")
    if not 2 <= len(clean_source) <= 200:
        raise CampaignMeasurementError("Укажите источник данных (от 2 до 200 символов)")
    try:
        when = date.fromisoformat(observed_on)
    except (ValueError, TypeError):
        raise CampaignMeasurementError("Укажите дату измерения") from None
    if when > datetime.now(timezone.utc).date():
        raise CampaignMeasurementError("Дата измерения не может быть в будущем")
    # The selected observation date describes when the number applied. The
    # separate recorded_at captures the actual user entry timestamp.
    measured = Metric(
        product_id=product_id,
        goal_id=campaign.goal_id,
        experiment_id=experiment_id,
        name=metric_name,
        value=amount,
        unit=clean_unit or None,
        source=clean_source,
        observed_at=datetime.combine(when, time(12), tzinfo=timezone.utc),
        dimensions={
            "stage": stage, "entry_method": "owner_manual",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor[:200],
        },
    )
    db.add(measured)
    db.commit()
    return measured


def comparison_for(campaign: Experiment, observations: list[Metric]) -> PerformanceComparison:
    target = (campaign.target_metric or "").strip()
    if not target:
        return PerformanceComparison(explanation="В плане ещё не указан измеримый показатель.")
    matched = [m for m in observations if m.name == target and
               isinstance(m.dimensions, dict)]
    # Two entries for the same measurement date can have identical SQL
    # created_at values (SQLite/PostgreSQL NOW() may have second precision).
    # Use the user-entry timestamp saved with manual measurements to pick the
    # latest correction deterministically instead of ordering by random UUID.
    matched.sort(key=lambda m: (
        m.observed_at.date().isoformat(),
        str(m.dimensions.get("recorded_at") or ""),
        m.created_at.isoformat() if m.created_at else "",
        str(m.id),
    ), reverse=True)
    baseline = next((m for m in matched if m.dimensions.get("stage") == "baseline"), None)
    result = next((m for m in matched if m.dimensions.get("stage") == "result"), None)
    if baseline is None or result is None:
        return PerformanceComparison(
            baseline=baseline, result=result,
            explanation="Для сравнения нужны значения показателя до кампании и после запуска.",
        )
    if (baseline.unit or "").strip() != (result.unit or "").strip():
        return PerformanceComparison(
            baseline=baseline, result=result,
            explanation="Единицы измерения не совпадают — сравнение невозможно.",
        )
    if result.observed_at.date() < baseline.observed_at.date():
        return PerformanceComparison(
            baseline=baseline, result=result,
            explanation="Дата результата раньше даты исходного значения.",
        )
    change = Decimal(result.value) - Decimal(baseline.value)
    pct = None
    if Decimal(baseline.value) != 0:
        pct = ((change / abs(Decimal(baseline.value))) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
    return PerformanceComparison(baseline, result, change, pct)


def metric_text(value: Decimal) -> str:
    """Decimal without scientific notation or meaningless trailing zeroes."""
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
