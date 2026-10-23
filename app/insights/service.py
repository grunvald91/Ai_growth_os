"""Reading the booking data for the chosen depth and turning it into facts. Read-only toward the booking platform."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.insights.compute import Insights, compute
from app.insights.settings import AHEAD_DAYS, depth_for
from app.models.domain import Product
from app.schedule.source import ScheduleSource, source_for
from app.schedule.views import SCHEDULE_TZ

NO_LINK = "no_link"
UNAVAILABLE = "unavailable"


def resolve(db: Session, settings, product: Product, account: str,
            depth_days: int | None = None) -> tuple[ScheduleSource, int] | None:
    """The booking source of the company and the depth to look at, or None when no booking business is linked.
    Touches the database, so it runs in the caller's own thread."""
    source = source_for(db, settings, product, account)
    if source is None:
        return None
    return source, (depth_days or depth_for(db, product.id))


def read(source: ScheduleSource, depth: int) -> Insights | None:
    """Facts for the depth, or None when the booking platform does not answer. Never a guess.

    Blocking: asks the booking platform over HTTP, so async callers run it in a thread."""
    today = datetime.now(ZoneInfo(SCHEDULE_TZ)).date()
    schedule = source.fetch(today - timedelta(days=depth), today + timedelta(days=AHEAD_DAYS - 1))
    if schedule is None:
        return None
    return compute(schedule, schedule.today or today, depth, AHEAD_DAYS)


def collect(db: Session, settings, product: Product, account: str,
            depth_days: int | None = None) -> tuple[Insights | None, str | None]:
    resolved = resolve(db, settings, product, account, depth_days)
    if resolved is None:
        return None, NO_LINK
    insights = read(*resolved)
    return (insights, None) if insights is not None else (None, UNAVAILABLE)
