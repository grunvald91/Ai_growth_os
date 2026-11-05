"""Real resource-based rental requests for venue_rental businesses."""

from datetime_utils import utc_now
import logging
import secrets
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .database import get_db, Business, Service, Employee, Booking, User, BusinessOwner
from .deps import get_current_admin
from .rental_logic import available_starts, price_quote
from .sales.schedule import resource_works
from database.prepayment import payment_deadline, request_prepayment
from database.booking_safety import (
    ACTIVE_BOOKING_STATUSES,
    BookingConflictError,
    ensure_resource_available,
    lock_resource_days,
    busy_resource_intervals,
)

router = APIRouter()
log = logging.getLogger(__name__)
ACTIVE = ACTIVE_BOOKING_STATUSES
LOCAL_TZ = ZoneInfo('Europe/Moscow')


class RentalRequest(BaseModel):
    date: date
    time: str
    employee_id: int
    duration_hours: int = Field(ge=1, le=12)
    client_name: str = Field(min_length=2, max_length=120)
    client_phone: str = Field(min_length=10, max_length=30, pattern=r'^\+?[0-9\s()\-]+$')
    additional_service_ids: list[int] = Field(default_factory=list)
    notes: str = Field(default='', max_length=1000)


class RentalQuoteUpdate(BaseModel):
    estimated_price: int = Field(ge=0)
    prepayment_amount: int = Field(default=0, ge=0)


def _public_status(booking: Booking) -> dict:
    return {'id': booking.id, 'status': booking.status, 'date': booking.booking_date.isoformat(),
            'time': booking.booking_time.strftime('%H:%M'), 'duration_minutes': booking.duration_minutes,
            'estimated_price': booking.estimated_price,
            'price_requires_confirmation': booking.price_requires_confirmation,
            'prepayment_amount': booking.prepayment_amount,
            'prepayment_expires_at': payment_deadline(booking)}


async def _booked_intervals(db: AsyncSession, business_id: int, employee_id: int, day: date):
    # Include neighbouring days: another booking can cross midnight into this day.
    intervals = await busy_resource_intervals(db, employee_id=employee_id, dates=[day])
    midnight = datetime.combine(day, time())
    return [(int((start - midnight).total_seconds() // 60), int((end - start).total_seconds() // 60))
            for start, end in intervals]


async def _rental_context(db: AsyncSession, business_id: int, employee_id: int, lock: bool = False):
    business = await db.get(Business, business_id)
    if not business or not business.is_active or business.business_type != 'venue_rental':
        raise HTTPException(404, 'Rental business not found')
    q = select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id,
                               Employee.is_active.is_(True))
    if lock:
        q = q.with_for_update()
    employee = (await db.execute(q)).scalar_one_or_none()
    if not employee or not employee.solo_price_per_hour or employee.solo_price_per_hour <= 0:
        raise HTTPException(404, 'Vessel not found')
    if not business.working_hours_start or not business.working_hours_end:
        raise HTTPException(409, 'Rental schedule is not configured')
    return business, employee


@router.get('/businesses/{business_id}/rental/slots')
async def rental_slots(business_id: int, date: date, employee_id: int,
                       duration_hours: int = Query(ge=1, le=12), db: AsyncSession = Depends(get_db)):
    business, employee = await _rental_context(db, business_id, employee_id)
    busy = await _booked_intervals(db, business_id, employee.id, date)
    try:
        slots = available_starts(date, business.working_hours_start, business.working_hours_end,
                                 duration_hours * 60, busy, now=datetime.now(LOCAL_TZ).replace(tzinfo=None),
                                 interval=employee.booking_interval_minutes or business.booking_interval_minutes or 60,
                                 break_start=business.break_start, break_end=business.break_end)
        if not await resource_works(db, employee, date, duration_hours * 60):
            slots = []
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    total, needs_quote, _ = price_quote(employee.solo_price_per_hour, duration_hours, [])
    return {'slots': slots, 'hourly_rate': employee.solo_price_per_hour,
            'base_price': total, 'price_requires_confirmation': needs_quote}


@router.post('/businesses/{business_id}/rental/bookings', status_code=201)
async def create_rental_booking(business_id: int, body: RentalRequest,
                                db: AsyncSession = Depends(get_db)):
    return await create_rental_request(business_id, body, db)


async def create_rental_request(business_id: int, body: RentalRequest, db: AsyncSession,
                                *, user: User | None = None, commit: bool = True):
    business, employee = await _rental_context(db, business_id, body.employee_id)
    try:
        requested_time = time.fromisoformat(body.time)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid time') from exc
    if requested_time.second or requested_time.microsecond:
        raise HTTPException(400, 'Choose a configured slot')

    duration_minutes = body.duration_hours * 60
    if not await resource_works(db, employee, body.date, duration_minutes):
        raise HTTPException(409, 'Resource schedule or minimum duration does not allow this booking')

    # Use the same stable transaction lock as ordinary bookings. This lock
    # exists even while the requested slot has no booking row yet.
    await lock_resource_days(db, employee.id, [body.date])
    try:
        await ensure_resource_available(
            db,
            employee_id=employee.id,
            booking_date=body.date,
            booking_time=requested_time,
            duration_minutes=duration_minutes,
            acquire_lock=False,
        )
    except BookingConflictError as exc:
        await db.rollback()
        raise HTTPException(409, 'The vessel is unavailable for the full interval') from exc

    # Schedule/past-time validation stays in the rental-specific pure helper,
    # but availability is re-read only after the resource lock is held.
    busy = await _booked_intervals(db, business_id, employee.id, body.date)
    try:
        slots = available_starts(
            body.date,
            business.working_hours_start,
            business.working_hours_end,
            duration_minutes,
            busy,
            now=datetime.now(LOCAL_TZ).replace(tzinfo=None),
            interval=employee.booking_interval_minutes or business.booking_interval_minutes or 60,
            break_start=business.break_start, break_end=business.break_end,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
    if body.time not in slots:
        await db.rollback()
        raise HTTPException(409, 'The vessel is unavailable for the full interval')

    if len(body.additional_service_ids) != len(set(body.additional_service_ids)) or len(body.additional_service_ids) > 8:
        raise HTTPException(400, 'Invalid additional services')
    addon_names = []
    fixed = []
    quoted_addon = False
    if body.additional_service_ids:
        found = (await db.execute(select(Service).where(
            Service.id.in_(body.additional_service_ids), Service.business_id == business_id,
            Service.is_additional.is_(True), Service.is_active.is_(True)))).scalars().all()
        if len(found) != len(body.additional_service_ids):
            raise HTTPException(400, 'Additional service not found')
        for addon in found:
            addon_names.append(addon.name)
            if addon.price is None:
                quoted_addon = True
            else:
                fixed.append(addon.price)

    main = (await db.execute(select(Service).where(
        Service.business_id == business_id, Service.is_additional.is_(False),
        Service.is_active.is_(True)).order_by(Service.id))).scalars().first()
    if main is None:
        raise HTTPException(409, 'Rental service not configured')

    total, needs_quote, addon_fixed = price_quote(employee.solo_price_per_hour, body.duration_hours, fixed)
    needs_quote = needs_quote or quoted_addon
    if quoted_addon:
        total = None

    client_phone = body.client_phone.strip()
    client_name = body.client_name.strip()
    digits = ''.join(c for c in client_phone if c.isdigit())
    if len(client_name) < 2 or not (10 <= len(digits) <= 15):
        raise HTTPException(400, 'Valid name and phone are required')

    client = user
    if client is None:
        client = User(
            telegram_id=-secrets.randbelow(2**60)-1,
            full_name=client_name,
            phone=('+' if client_phone.startswith('+') else '') + digits,
        )
        db.add(client)
    await db.flush()

    notes = [
        f'Аренда {body.duration_hours} ч. Судно: {employee.name}.',
        'Доп. услуги: ' + ', '.join(addon_names) if addon_names else '',
        f'Фиксированные доп. услуги: {addon_fixed} ₽.' if addon_fixed else '',
        'Требуется подтверждение цены менеджером.' if needs_quote else f'Предварительная сумма: {total} ₽.',
        'Предоплата: до 20%, точную сумму подтверждает владелец.',
        f'Комментарий клиента: {body.notes.strip()}' if body.notes.strip() else '',
    ]
    booking = Booking(
        business_id=business_id,
        client_id=client.id,
        service_id=main.id,
        employee_id=employee.id,
        booking_date=body.date,
        booking_time=requested_time,
        duration_minutes=duration_minutes,
        resource_duration_minutes=duration_minutes,
        occupies_resource=True,
        estimated_price=total,
        price_requires_confirmation=needs_quote,
        prepayment_amount=None,
        status='pending',
        notes='\n'.join(x for x in notes if x),
        notify_token=secrets.token_urlsafe(32),
    )
    db.add(booking)

    try:
        if commit:
            await db.commit()
        else:
            await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        message = str(exc).lower()
        if "bookings_no_active_resource_overlap" in message or "exclusion" in message:
            raise HTTPException(409, 'The vessel is unavailable for the full interval') from exc
        raise

    await db.refresh(booking)
    if commit:
        await notify_rental_request(booking, db)
    return {**_public_status(booking), 'tracking_token': booking.notify_token}


async def notify_rental_request(booking, db):
    """Shared post-commit event for web and AI rental requests."""
    try:
        from bot.services.notification_events import dispatch_booking_event
        await dispatch_booking_event(
            [booking],
            db,
            "rental_request_created",
            include_client=False,
            staff_audience="all",
        )
    except Exception as notification_error:
        log.error(
            "Rental request notification failed booking=%s: %s",
            booking.id,
            notification_error,
            exc_info=True,
        )


@router.get('/rental/bookings/{tracking_token}')
async def rental_booking_status(tracking_token: str, db: AsyncSession = Depends(get_db)):
    if len(tracking_token) < 32 or len(tracking_token) > 64:
        raise HTTPException(404, 'Booking not found')
    booking = (await db.execute(select(Booking).where(
        Booking.notify_token == tracking_token, Booking.duration_minutes.is_not(None)))).scalar_one_or_none()
    if not booking:
        raise HTTPException(404, 'Booking not found')
    return _public_status(booking)


@router.patch('/admin/rental/bookings/{booking_id}/quote')
async def update_rental_quote(booking_id: int, body: RentalQuoteUpdate,
                              user: User = Depends(get_current_admin),
                              db: AsyncSession = Depends(get_db)):
    booking = (await db.execute(select(Booking).where(Booking.id == booking_id)
        .options(selectinload(Booking.client), selectinload(Booking.service), selectinload(Booking.employee))
        .with_for_update())).scalar_one_or_none()
    if not booking or booking.duration_minutes is None:
        raise HTTPException(404, 'Rental booking not found')
    ownership = (await db.execute(select(BusinessOwner.id).where(
        BusinessOwner.business_id == booking.business_id, BusinessOwner.user_id == user.id))).scalar_one_or_none()
    business = await db.get(Business, booking.business_id)
    if business.owner_id != user.id and ownership is None:
        raise HTTPException(403, 'Not your business')
    if booking.status in ('cancelled', 'completed'):
        raise HTTPException(409, 'Booking cannot be quoted')
    if booking.status not in ('pending', 'pending_prepayment'):
        if (booking.estimated_price != body.estimated_price or booking.prepayment_amount != body.prepayment_amount):
            raise HTTPException(409, 'Cannot change a quote after payment submission or confirmation')
        from .routes.admin import _admin_booking_out
        return _admin_booking_out(booking)
    deadline = payment_deadline(booking)
    if deadline is not None and deadline <= utc_now():
        booking.status = 'cancelled'
        await db.commit()
        raise HTTPException(409, 'Payment window expired; create a new booking')
    if body.prepayment_amount > body.estimated_price * 0.2:
        raise HTTPException(400, 'Prepayment cannot exceed 20%')
    booking.estimated_price = body.estimated_price
    request_prepayment(booking, body.prepayment_amount)
    booking.price_requires_confirmation = False
    await db.commit()
    from .routes.admin import _admin_booking_out
    return _admin_booking_out(booking)
