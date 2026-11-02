"""Read-only schedule for the marketing studio's dispatcher.

One internal method, called by the studio server with the private key (same key as the sign-in check).
It only reads: bookings for a period and, on request, free start times. Free times come from the existing
public slot calculation (`get_slots`), so there is no second availability engine. Nothing here changes a booking.

Client name, phone and e-mail are included on purpose (owner decision of 22.10.2026) so the dispatcher can answer
"who is next". Notes, payment details and Telegram ids are not sent.
"""
import hmac
import json
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EmployeeDayOff
from ..database import Booking, Business, Employee, Service, User, get_db
from .studio_sso import _secret, studio_role

router = APIRouter()

SCHEDULE_TZ = "Europe/Moscow"  # the same zone the public slot calculation uses for "now"


class BookingRow(BaseModel):
    id: int
    date: str
    time: str
    duration_minutes: Optional[int] = None
    status: str
    service: str
    resource: Optional[str] = None
    client_name: str
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    price: Optional[int] = None
    # Who booked and when the booking was made: lets the studio tell new from returning clients and see how
    # many days ahead people book. Additive: older readers ignore them.
    client_id: Optional[int] = None
    created_at: Optional[str] = None
    # False for add-ons booked together with a main service: they do not take the master's or hall's time.
    occupies_resource: bool = True


class ResourceOut(BaseModel):
    id: int
    name: str
    # Calendar dates on which this resource can work, accounting for duty and days off.
    working_dates: list[str] | None = None


class FreeSlots(BaseModel):
    date: str
    resource_id: Optional[int] = None
    resource: Optional[str] = None
    times: list[str]


class ScheduleOut(BaseModel):
    today: str
    working_hours: Optional[str] = None
    break_hours: Optional[str] = None
    step_minutes: Optional[int] = None
    resources: list[ResourceOut]
    bookings: list[BookingRow]
    free_slots: list[FreeSlots] = []


def _hours(start, end) -> Optional[str]:
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}" if start and end else None


def _working_dates(employee: Employee, dates: list[date], off_dates: set[date]) -> list[str]:
    """Mirror resource_works duty and days-off checks without per-day database queries."""
    allowed: set[str] | None = None
    if employee.duty_schedule:
        try:
            raw = json.loads(employee.duty_schedule)
            allowed = {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()
        except (ValueError, TypeError):
            allowed = set()  # invalid schedule is never treated as open
    return [
        day.isoformat() for day in dates
        if day not in off_dates
        and (allowed is None or day.isoformat() in allowed)
        and (allowed is not None or employee.on_duty_date is None or employee.on_duty_date == day)
    ]


@router.get("/internal/studio/schedule", response_model=ScheduleOut)
async def studio_schedule(
    business_id: int,
    date_from: date,
    date_to: date,
    user_id: Optional[int] = None,
    with_slots: bool = Query(False),
    slot_minutes: Optional[int] = Query(None, ge=1, le=24 * 60),
    x_studio_key: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Bookings of the business for the period (every status, so cancellations can be counted) and, when asked,
    free start times for each day and resource."""
    if not hmac.compare_digest(x_studio_key.encode(), _secret("STUDIO_INTERNAL_KEY")):
        raise HTTPException(status_code=403, detail="Forbidden")
    # With a user id the person's access is re-checked right now (signed in from ServGo). Without one the studio
    # answers for a company that its operator linked to this business by hand, and has already checked the sign-in.
    if user_id is not None and await studio_role(db, user_id, business_id) is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to is before date_from")
    business = await db.get(Business, business_id)
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")

    resources = (await db.scalars(select(Employee).where(
        Employee.business_id == business_id, Employee.is_active.is_(True),
        Employee.role.notin_(["administrator", "admin"])
    ).order_by(Employee.id))).all()

    rows = (await db.execute(
        select(Booking, Service, Employee, User)
        .join(Service, Service.id == Booking.service_id)
        .join(User, User.id == Booking.client_id)
        .outerjoin(Employee, Employee.id == Booking.employee_id)
        .where(Booking.business_id == business_id, Booking.booking_date >= date_from,
               Booking.booking_date <= date_to)
        .order_by(Booking.booking_date, Booking.booking_time, Booking.id)
    )).all()
    # One batched days-off lookup for the entire requested interval, rather than N*M queries.
    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    off_by_employee: dict[int, set[date]] = {}
    if resources:
        off_rows = (await db.execute(
            select(EmployeeDayOff.employee_id, EmployeeDayOff.date).where(
                EmployeeDayOff.employee_id.in_([resource.id for resource in resources]),
                EmployeeDayOff.date >= date_from, EmployeeDayOff.date <= date_to,
            )
        )).all()
        for employee_id, off_day in off_rows:
            off_by_employee.setdefault(employee_id, set()).add(off_day)

    bookings = [BookingRow(
        id=booking.id, date=booking.booking_date.isoformat(), time=booking.booking_time.strftime("%H:%M"),
        duration_minutes=booking.duration_minutes or booking.resource_duration_minutes,
        status=booking.status, service=service.name, resource=employee.name if employee else None,
        client_name=client.full_name, client_phone=client.phone, client_email=client.email,
        price=booking.estimated_price, occupies_resource=bool(booking.occupies_resource),
        client_id=booking.client_id,
        created_at=booking.created_at.isoformat() if booking.created_at else None,
    ) for booking, service, employee, client in rows]

    free_slots: list[FreeSlots] = []
    if with_slots:
        # Imported here: the client routes module is large and imports half of the API.
        from .client import get_slots
        day = date_from
        while day <= date_to:
            for resource in (resources or [None]):
                # A hall with a minimum rental time has no free start shorter than that minimum, so the window asked
                # for is at least that long; otherwise the platform's own step is used.
                minutes = slot_minutes
                if minutes is None and resource is not None and resource.min_booking_minutes:
                    step = resource.booking_interval_minutes or business.booking_interval_minutes or 30
                    minutes = max(step, resource.min_booking_minutes)
                slots = await get_slots(
                    business_id=business_id, date=day, service_id=None,
                    employee_id=resource.id if resource else None, duration_minutes=minutes,
                    include_unavailable=False, exclude_booking_id=None, db=db)
                free_slots.append(FreeSlots(
                    date=day.isoformat(), resource_id=resource.id if resource else None,
                    resource=resource.name if resource else None, times=[slot.time for slot in slots]))
            day += timedelta(days=1)

    return ScheduleOut(
        today=datetime.now(ZoneInfo(SCHEDULE_TZ)).date().isoformat(),
        working_hours=_hours(business.working_hours_start, business.working_hours_end),
        break_hours=_hours(business.break_start, business.break_end),
        step_minutes=business.booking_interval_minutes,
        resources=[ResourceOut(id=item.id, name=item.name,
                               working_dates=_working_dates(item, days, off_by_employee.get(item.id, set())))
                   for item in resources],
        bookings=bookings, free_slots=free_slots)
