"""Facts become Metric rows, so the marketing team can cite them as evidence.

One set per company, depth and day: asking again the same day refreshes the numbers in place instead of piling up
copies. A Decision that cited a metric keeps pointing at the same row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.insights.compute import Insights
from app.models.domain import Metric

SOURCE_PREFIX = "servgo_schedule"


def source_name(depth_days: int) -> str:
    return f"{SOURCE_PREFIX}:{depth_days}d"


def store(db: Session, product_id: UUID, insights: Insights, now: datetime | None = None) -> list[Metric]:
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    source = source_name(insights.depth_days)
    existing = {m.name: m for m in db.scalars(select(Metric).where(
        Metric.product_id == product_id, Metric.source == source, Metric.observed_at >= day_start))}
    rows: list[Metric] = []
    for fact in insights.facts:
        value = Decimal(str(round(fact.value, 4)))
        dimensions = {"label": fact.label, "depth_days": insights.depth_days,
                      "period_from": insights.first.isoformat(), "period_to": insights.last.isoformat()}
        row = existing.get(fact.name[:100])
        if row is None:
            row = Metric(product_id=product_id, name=fact.name[:100], value=value, unit=fact.unit, source=source,
                         observed_at=now, dimensions=dimensions)
            db.add(row)
        else:
            row.value, row.unit, row.observed_at, row.dimensions = value, fact.unit, now, dimensions
        rows.append(row)
    db.commit()
    return rows
