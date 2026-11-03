"""Durable per-company editorial calendar, using the existing Task.due_at column.

The calendar assigns up to three publishing slots per Moscow date. It neither
drops topics nor relies on repeated GitHub Actions. A PostgreSQL product lock
serializes reservations and actual publication; SQLite remains for tests.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.domain import Artifact, Product, Publication, Task, TaskStatus

SEO_TASK_PREFIX = "SEO-ARTICLE:"
OWNER_TZ = ZoneInfo("Europe/Moscow")
PUBLICATION_HOUR = 9
PUBLICATIONS_PER_DAY = 3


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day(value: datetime, *, tz: ZoneInfo = OWNER_TZ) -> date:
    return utc(value).astimezone(tz).date()


def day_bounds(value: datetime, *, tz: ZoneInfo = OWNER_TZ) -> tuple[datetime, datetime]:
    local_date = day(value, tz=tz)
    start = datetime.combine(local_date, time.min, tzinfo=tz)
    stop = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), stop.astimezone(timezone.utc)


def db_time(db, value: datetime) -> datetime:
    value = utc(value)
    return value.replace(tzinfo=None) if db.bind.dialect.name == "sqlite" else value


def published_today(db, product_id, *, now: datetime | None = None,
                    tz: ZoneInfo = OWNER_TZ) -> int:
    """Count every publication created that day, including subsequently hidden ones."""
    now = utc(now or datetime.now(timezone.utc))
    start, end = day_bounds(now, tz=tz)
    return len(list(db.scalars(select(Publication.id).where(
        Publication.product_id == product_id, Publication.channel == "own_site",
        Publication.published_at >= db_time(db, start),
        Publication.published_at < db_time(db, end),
    ))))


def available_slots(db, product_id, count: int, *, limit: int = PUBLICATIONS_PER_DAY,
                    now: datetime | None = None, tz: ZoneInfo = OWNER_TZ) -> list[datetime]:
    """Reserve full calendar capacity across plans and already-published pages.

    Must be invoked while holding a row-level lock on this company's Product.
    Slots are computed from durable Task.due_at and actual Publication rows, not
    transient worker memory. Previously completed/published tasks retain slots.
    """
    if count < 0 or limit < 1:
        raise ValueError("Invalid SEO scheduling capacity")
    now = utc(now or datetime.now(timezone.utc))
    today = day(now, tz=tz)
    occupied: dict[date, set[str]] = defaultdict(set)
    tasks = list(db.scalars(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "content_factory",
        Task.title.like(f"{SEO_TASK_PREFIX}%"),
        Task.due_at.is_not(None),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING,
                         TaskStatus.WAITING_APPROVAL, TaskStatus.COMPLETED]),
    )))
    scheduled_by_task = {}
    for task in tasks:
        scheduled = day(task.due_at, tz=tz)
        scheduled_by_task[task.id] = scheduled
        if scheduled >= today:
            occupied[scheduled].add(f"task:{task.id}")
    day_start, _ = day_bounds(now, tz=tz)
    publications = db.execute(select(Publication.id, Publication.published_at, Artifact.task_id)
        .join(Artifact, Publication.artifact_id == Artifact.id)
        .where(Publication.product_id == product_id, Publication.channel == "own_site",
               Publication.published_at >= db_time(db, day_start))).all()
    for publication_id, published_at, task_id in publications:
        published_date = day(published_at, tz=tz)
        if published_date >= today:
            # A scheduled article published on its due day consumes one slot,
            # not two. A late publication consumes the actual day's slot too.
            token = (f"task:{task_id}" if scheduled_by_task.get(task_id) == published_date
                     else f"publication:{publication_id}")
            occupied[published_date].add(token)

    due = []
    date_cursor = today
    while len(due) < count:
        available = max(0, limit - len(occupied[date_cursor]))
        for _ in range(min(available, count - len(due))):
            morning = datetime.combine(date_cursor, time(hour=PUBLICATION_HOUR), tzinfo=tz)
            planned = max(now, morning.astimezone(timezone.utc)) if date_cursor == today else morning.astimezone(timezone.utc)
            due.append(planned)
            occupied[date_cursor].add(f"new:{len(due)}")
        date_cursor += timedelta(days=1)
    return due
