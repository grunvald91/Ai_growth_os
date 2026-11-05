"""Central booking concurrency and overlap protection.

Every write path that reserves a concrete employee/resource should use these
helpers before INSERT/UPDATE. PostgreSQL advisory transaction locks serialize
concurrent requests even when the target slot is currently empty; interval
checks then reject overlaps. The database exclusion constraint is the final
backstop.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional, Sequence

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Booking, Service
from booking_intervals import intervals_overlap


ACTIVE_BOOKING_STATUSES = (
    "pending",
    "confirmed",
    "pending_prepayment",
    "prepayment_sent",
    "prepayment_confirmed",
)


class BookingConflictError(ValueError):
    """Requested resource interval overlaps an active booking."""


def _expanded_lock_dates(dates: Iterable[date]) -> list[date]:
    """Lock target days plus neighbours for cross-midnight safety."""
    result: set[date] = set()
    for day in dates:
        result.add(day - timedelta(days=1))
        result.add(day)
        result.add(day + timedelta(days=1))
    return sorted(result)


async def lock_resource_days(
    session: AsyncSession,
    employee_id: Optional[int],
    dates: Iterable[date],
) -> None:
    """Serialize booking writes for a resource/date window.

    A row lock on existing bookings is insufficient when the slot is empty:
    two transactions can both see zero rows. PostgreSQL transaction-scoped
    advisory locks give us a stable lock key even before any booking row exists.

    Locks are acquired in sorted date order to avoid deadlocks for recurring
    bookings that touch several days.
    """
    if not employee_id:
        return

    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        # Unit tests may run on SQLite. The database exclusion constraint is
        # PostgreSQL-specific too; tests still exercise interval logic.
        return

    for lock_date in _expanded_lock_dates(dates):
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "CAST(:resource_id AS INTEGER), CAST(:day_key AS INTEGER))"
            ),
            {
                "resource_id": int(employee_id),
                "day_key": int(lock_date.toordinal()),
            },
        )


def booking_resource_duration(booking: Booking) -> int:
    """Return the immutable resource duration snapshot for a booking."""
    snapshot = getattr(booking, "resource_duration_minutes", None)
    if snapshot is not None:
        return max(0, int(snapshot))

    rental_duration = getattr(booking, "duration_minutes", None)
    if rental_duration is not None:
        return max(0, int(rental_duration))

    service = getattr(booking, "service", None)
    if service is not None and getattr(service, "duration_minutes", None) is not None:
        return max(0, int(service.duration_minutes))

    return 30


def service_resource_duration(service: Optional[Service]) -> int:
    if service is None:
        return 30
    return max(0, int(service.duration_minutes or 0))


def service_occupies_resource(service: Optional[Service]) -> bool:
    if service is None:
        return True
    return not bool(getattr(service, "is_additional", False)) and service_resource_duration(service) > 0


async def resource_overlap_exists(
    session: AsyncSession,
    *,
    employee_id: Optional[int],
    booking_date: date,
    booking_time: time,
    duration_minutes: int,
    exclude_booking_ids: Sequence[int] | None = None,
    acquire_lock: bool = True,
) -> bool:
    """Check whether a resource interval overlaps any active booking."""
    if not employee_id or duration_minutes <= 0:
        return False

    if acquire_lock:
        await lock_resource_days(session, employee_id, [booking_date])

    check_dates = [
        booking_date - timedelta(days=1),
        booking_date,
        booking_date + timedelta(days=1),
    ]
    filters = [
        Booking.employee_id == employee_id,
        Booking.booking_date.in_(check_dates),
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.occupies_resource.is_(True),
    ]
    if exclude_booking_ids:
        filters.append(Booking.id.notin_(list(exclude_booking_ids)))

    result = await session.execute(
        select(Booking)
        .where(and_(*filters))
        .options(selectinload(Booking.service))
    )
    existing_bookings = result.scalars().all()

    requested_start = datetime.combine(booking_date, booking_time)
    requested_end = requested_start + timedelta(minutes=duration_minutes)

    for existing in existing_bookings:
        existing_duration = booking_resource_duration(existing)
        if existing_duration <= 0:
            continue
        existing_start = datetime.combine(existing.booking_date, existing.booking_time)
        existing_end = existing_start + timedelta(minutes=existing_duration)
        if intervals_overlap(requested_start, requested_end, existing_start, existing_end):
            return True

    return False


async def ensure_resource_available(
    session: AsyncSession,
    *,
    employee_id: Optional[int],
    booking_date: date,
    booking_time: time,
    duration_minutes: int,
    exclude_booking_ids: Sequence[int] | None = None,
    acquire_lock: bool = True,
) -> None:
    if await resource_overlap_exists(
        session,
        employee_id=employee_id,
        booking_date=booking_date,
        booking_time=booking_time,
        duration_minutes=duration_minutes,
        exclude_booking_ids=exclude_booking_ids,
        acquire_lock=acquire_lock,
    ):
        raise BookingConflictError(
            f"Resource is already booked for {booking_date.isoformat()} "
            f"{booking_time.strftime('%H:%M')}"
        )


async def busy_resource_intervals(
    session: AsyncSession,
    *,
    employee_id: int,
    dates: Iterable[date],
    exclude_booking_ids: Sequence[int] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return active occupied intervals for slot rendering/read-only checks."""
    days = sorted(set(dates))
    if not days:
        return []
    expanded = _expanded_lock_dates(days)
    filters = [
        Booking.employee_id == employee_id,
        Booking.booking_date.in_(expanded),
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.occupies_resource.is_(True),
    ]
    if exclude_booking_ids:
        filters.append(Booking.id.notin_(list(exclude_booking_ids)))
    result = await session.execute(
        select(Booking)
        .where(*filters)
        .options(selectinload(Booking.service))
    )
    intervals: list[tuple[datetime, datetime]] = []
    for booking in result.scalars().all():
        duration = booking_resource_duration(booking)
        if duration <= 0:
            continue
        start = datetime.combine(booking.booking_date, booking.booking_time)
        intervals.append((start, start + timedelta(minutes=duration)))
    return intervals


async def contiguous_visit_bookings(
    session: AsyncSession,
    anchor: Booking,
    statuses: Sequence[str] | None = None,
) -> list[Booking]:
    """Return the contiguous service rows that form the anchor's visit.

    Modern multi-service visits store services sequentially, so equality on
    booking_time is not enough. Legacy rows with the same start are also
    grouped.
    """
    filters = [
        Booking.client_id == anchor.client_id,
        Booking.business_id == anchor.business_id,
        Booking.employee_id == anchor.employee_id,
        Booking.booking_date == anchor.booking_date,
    ]
    if statuses:
        filters.append(Booking.status.in_(list(statuses)))

    result = await session.execute(
        select(Booking)
        .where(and_(*filters))
        .options(
            selectinload(Booking.client),
            selectinload(Booking.business),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .order_by(Booking.booking_time, Booking.id)
    )
    items = list(result.scalars().all())
    if not items:
        return [anchor]

    anchor_index = next((i for i, item in enumerate(items) if item.id == anchor.id), None)
    if anchor_index is None:
        return [anchor]

    chain = [items[anchor_index]]

    i = anchor_index - 1
    while i >= 0:
        prev = items[i]
        first = chain[0]
        prev_start = datetime.combine(prev.booking_date, prev.booking_time)
        prev_end = prev_start + timedelta(minutes=booking_resource_duration(prev))
        first_start = datetime.combine(first.booking_date, first.booking_time)
        if prev.booking_time == first.booking_time or prev_end == first_start:
            chain.insert(0, prev)
            i -= 1
        else:
            break

    i = anchor_index + 1
    while i < len(items):
        nxt = items[i]
        last = chain[-1]
        last_start = datetime.combine(last.booking_date, last.booking_time)
        last_end = last_start + timedelta(minutes=booking_resource_duration(last))
        nxt_start = datetime.combine(nxt.booking_date, nxt.booking_time)
        if nxt.booking_time == chain[0].booking_time or nxt_start == last_end:
            chain.append(nxt)
            i += 1
        else:
            break

    return chain
