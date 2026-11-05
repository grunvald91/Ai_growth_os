"""Правки существующих записей из расписания: смена услуги и «завершить сейчас».

Общий код для сайта (владелец/админ/мастер) и для Telegram-бота, чтобы правила не расходились.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.booking_safety import (
    BookingConflictError,
    booking_resource_duration,
    contiguous_visit_bookings,
    ensure_resource_available,
    lock_resource_days,
    service_occupies_resource,
    service_resource_duration,
)
from database.models import Booking, Service
from datetime_utils import utc_now

SCHEDULE_TZ = "Europe/Moscow"  # тот же пояс, что у расчёта свободного времени

# Менять услугу можно и у записей с предоплатой: внесённая сумма остаётся как есть,
# остаток при визите считается уже от цены новой услуги. Отменённые не трогаем.
CHANGEABLE_STATUSES = (
    "pending", "confirmed", "completed",
    "pending_prepayment", "prepayment_sent", "prepayment_confirmed",
)


class BookingChangeError(ValueError):
    """Правку нельзя выполнить; text пригоден для показа человеку."""

    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def local_now() -> datetime:
    """Текущее местное время (наивное), в котором хранятся даты и время записей."""
    return datetime.now(ZoneInfo(SCHEDULE_TZ)).replace(tzinfo=None)


def stamp_finished_now(items: Iterable[Booking], now_utc: Optional[datetime] = None) -> None:
    """Запомнить момент завершения у записей визита."""
    moment = now_utc or utc_now()
    for item in items:
        item.finished_at = moment


def planned_end(booking: Booking) -> datetime:
    start = datetime.combine(booking.booking_date, booking.booking_time)
    return start + timedelta(minutes=booking_resource_duration(booking))


def actual_end_time(booking: Booking) -> Optional[str]:
    """«ЧЧ:ММ» фактического конца, если запись завершили раньше плана; иначе None."""
    if booking.status != "completed" or booking.finished_at is None:
        return None
    finished = booking.finished_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo(SCHEDULE_TZ)
    ).replace(tzinfo=None, second=0, microsecond=0)
    start = datetime.combine(booking.booking_date, booking.booking_time)
    end = planned_end(booking)
    if finished >= end:
        return None
    return max(finished, start).strftime("%H:%M")


async def change_booking_service(
    db: AsyncSession, booking: Booking, new_service_id: int
) -> Booking:
    """Заменить услугу в записи (клиент выбрал не ту). Коммит делает вызывающий код.

    Правила: тот же бизнес, активная услуга того же вида (основная/дополнительная),
    в т.ч. пакеты аренды залов (кроме почасовых заявок); предоплата (если была) остаётся как есть; если услуга длиннее, время мастера на это должно быть свободно.
    """
    if booking.status not in CHANGEABLE_STATUSES:
        raise BookingChangeError("У отменённой записи услугу изменить нельзя")
    # Почасовая заявка (длительность и цену задаёт владелец вручную) устроена иначе:
    # в ней одна общая услуга, менять там нечего — меняется длительность/цена в карточке.
    # Аренда залов (площадки) — обычные записи на услуги-пакеты, для них замена работает.
    if booking.duration_minutes is not None:
        raise BookingChangeError("В почасовой заявке услуга одна: измените длительность или цену в карточке")
    if booking.service_id == new_service_id:
        raise BookingChangeError("В записи уже выбрана эта услуга", 400)

    new_service = (await db.execute(
        select(Service).where(
            Service.id == new_service_id,
            Service.business_id == booking.business_id,
            Service.is_active.is_(True),
        )
    )).scalar_one_or_none()
    if new_service is None:
        raise BookingChangeError("Такой услуги в этом бизнесе нет", 404)
    old_service = await db.get(Service, booking.service_id)
    if bool(new_service.is_additional) != bool(getattr(old_service, "is_additional", False)):
        raise BookingChangeError("Основную услугу можно заменить только на основную, дополнительную — на дополнительную")

    new_duration = service_resource_duration(new_service)
    new_occupies = service_occupies_resource(new_service)
    old_duration = booking_resource_duration(booking)

    if booking.status != "completed":
        visit = await contiguous_visit_bookings(
            db,
            booking,
            statuses=(
                ("prepayment_sent", "pending_prepayment")
                if booking.status in ("prepayment_sent", "pending_prepayment")
                else (booking.status,)
            ),
        )
        if len(visit) > 1 and new_duration != old_duration:
            raise BookingChangeError(
                "В визите несколько услуг подряд, а новая идёт по-другому по времени. "
                "Отмените запись и создайте заново"
            )
        if new_occupies and new_duration > old_duration:
            await lock_resource_days(db, booking.employee_id, [booking.booking_date])
            try:
                await ensure_resource_available(
                    db,
                    employee_id=booking.employee_id,
                    booking_date=booking.booking_date,
                    booking_time=booking.booking_time,
                    duration_minutes=new_duration,
                    exclude_booking_ids=[item.id for item in visit],
                    acquire_lock=False,
                )
            except BookingConflictError:
                raise BookingChangeError(
                    "Новая услуга длиннее, а следом у мастера уже есть запись. Сначала перенесите соседнюю запись"
                ) from None

    booking.service_id = new_service.id
    booking.service = new_service
    booking.resource_duration_minutes = new_duration
    booking.occupies_resource = new_occupies
    # Напоминания считаются от времени начала; услуга в тексте берётся из записи на лету.
    booking.version = (booking.version or 1) + 1
    return booking


FINISHABLE_STATUSES = ("pending", "confirmed", "prepayment_confirmed")


async def finish_visit_now(db: AsyncSession, booking: Booking) -> list[Booking]:
    """Завершить весь визит (все услуги подряд) сейчас. Коммит делает вызывающий код.

    Статус completed сам освобождает время мастера; finished_at нужен, чтобы расписание
    показывало конец именно с этого момента.
    """
    if booking.status not in FINISHABLE_STATUSES:
        if booking.status == "completed":
            raise BookingChangeError("Эта запись уже завершена", 400)
        raise BookingChangeError("Эту запись нельзя завершить (отменена или ждёт оплаты)")
    visit = await contiguous_visit_bookings(db, booking, statuses=(booking.status,))
    moment = utc_now()
    for item in visit:
        item.status = "completed"
        item.version = (item.version or 1) + 1
    stamp_finished_now(visit, moment)
    return visit
