"""The analysis depth: how many past days of bookings are looked at. Default one month, set per company."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.insights.models import InsightSettings

DEFAULT_DEPTH_DAYS = 30
MIN_DEPTH_DAYS = 7
MAX_DEPTH_DAYS = 365
AHEAD_DAYS = 14  # upcoming bookings are always read this far ahead


def depth_for(db: Session, product_id: UUID) -> int:
    value = db.scalar(select(InsightSettings.depth_days).where(InsightSettings.product_id == product_id))
    return value if value is not None else DEFAULT_DEPTH_DAYS


def set_depth(db: Session, product_id: UUID, days: int, account: str) -> int:
    if not MIN_DEPTH_DAYS <= days <= MAX_DEPTH_DAYS:
        raise ValueError("depth out of range")
    row = db.scalar(select(InsightSettings).where(InsightSettings.product_id == product_id))
    if row is None:
        row = InsightSettings(product_id=product_id, depth_days=days, updated_by=account[:200])
        db.add(row)
    else:
        row.depth_days, row.updated_by = days, account[:200]
    db.commit()
    return days
