"""Unified immediate booking event notifications for Telegram + PWA.

Reminder preferences do not apply here: booking-created/confirmed events are
mandatory system events. Optional upcoming reminders are handled separately by
bot.services.notifications.
"""
from __future__ import annotations

import html
import logging
import os
from datetime import date, time
from typing import Iterable, Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.crud import get_business_owners
from database.models import Booking, Employee, NotificationDelivery
from bot.services.webpush import send_client_event_webpush, send_staff_event_webpush

logger = logging.getLogger(__name__)


async def _fresh_bookings(
    session: AsyncSession,
    bookings: Iterable[Booking],
) -> list[Booking]:
    ids = [int(b.id) for b in bookings if getattr(b, "id", None)]
    if not ids:
        return []
    result = await session.execute(
        select(Booking)
        .where(Booking.id.in_(ids))
        .options(
            selectinload(Booking.client),
            selectinload(Booking.business),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .order_by(Booking.booking_date, Booking.booking_time, Booking.id)
    )
    return list(result.scalars().all())


async def _telegram_send(
    chat_id: int,
    text: str,
    *,
    reply_markup: Optional[dict] = None,
) -> bool:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or ":" not in token or not chat_id or int(chat_id) <= 0:
        return False
    payload: dict = {
        "chat_id": int(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
        if response.status_code != 200:
            logger.warning(
                "Telegram event delivery failed chat=%s status=%s body=%s",
                chat_id,
                response.status_code,
                response.text[:300],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram event delivery failed chat=%s: %s", chat_id, exc)
        return False


async def _staff_telegram_targets(
    bookings: list[Booking],
    session: AsyncSession,
    audience: str,
) -> set[int]:
    if not bookings:
        return set()
    first = bookings[0]
    targets: set[int] = set()

    if audience in ("owner", "all"):
        owners = await get_business_owners(session, first.business_id)
        targets.update(
            int(owner.telegram_id)
            for owner in owners
            if owner.telegram_id and int(owner.telegram_id) > 0
        )

    if audience in ("master", "all"):
        targets.update(
            int(booking.employee.telegram_id)
            for booking in bookings
            if booking.employee
            and booking.employee.telegram_id
            and int(booking.employee.telegram_id) > 0
        )

    if audience == "all":
        admins_result = await session.execute(
            select(Employee.telegram_id).where(
                Employee.business_id == first.business_id,
                Employee.is_active.is_(True),
                Employee.role.in_(["administrator", "master_and_admin"]),
                Employee.telegram_id.is_not(None),
            )
        )
        targets.update(
            int(row[0])
            for row in admins_result.all()
            if row[0] and int(row[0]) > 0
        )

    return targets


def _service_names(bookings: list[Booking]) -> str:
    names: list[str] = []
    for booking in bookings:
        name = booking.service.name if booking.service else None
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or "Запись"


def _staff_message(
    bookings: list[Booking],
    event_kind: str,
    *,
    old_date: date | None = None,
    old_time: time | None = None,
) -> str:
    first = bookings[0]
    business = first.business
    client = first.client
    business_name = html.escape(business.name if business else "ServGo")
    client_name = html.escape(client.full_name if client else "Клиент")
    phone = html.escape(client.phone) if client and client.phone else ""
    services = html.escape(_service_names(bookings))
    employee_names = list(
        dict.fromkeys(
            b.employee.name for b in bookings if b.employee and b.employee.name
        )
    )
    employee_line = (
        f"\n👤 Мастер / ресурс: {html.escape(', '.join(employee_names))}"
        if employee_names
        else ""
    )
    when = f"{first.booking_date.strftime('%d.%m.%Y')} в {first.booking_time.strftime('%H:%M')}"

    if event_kind == "booking_created":
        header = "🆕 <b>Новая подтверждённая запись</b>"
        extra = ""
    elif event_kind == "booking_cancelled":
        header = "❌ <b>Запись отменена</b>"
        extra = ""
    elif event_kind == "booking_rescheduled":
        header = "🔄 <b>Запись перенесена</b>"
        extra = ""
        if old_date and old_time:
            extra = (
                f"\n↩️ Было: {old_date.strftime('%d.%m.%Y')} "
                f"в {old_time.strftime('%H:%M')}"
            )
    elif event_kind == "prepayment_submitted":
        header = "💳 <b>Клиент сообщил об отправке предоплаты</b>"
        extra = "\nПроверьте поступление и подтвердите или отклоните оплату."
    elif event_kind == "rental_request_created":
        header = "🛥 <b>Новая заявка на аренду</b>"
        extra = "\nОткройте ServGo → Расписание для обработки заявки."
    else:
        header = "🔔 <b>Запись обновлена</b>"
        extra = ""

    phone_line = f"\n📱 Телефон: {phone}" if phone else ""
    return (
        f"{header}\n\n"
        f"🏢 {business_name}\n"
        f"📅 {when}\n"
        f"👤 Клиент: {client_name}{phone_line}\n"
        f"💼 {services}"
        f"{employee_line}"
        f"{extra}"
    )


def _client_message(
    bookings: list[Booking],
    event_kind: str,
    *,
    old_date: date | None = None,
    old_time: time | None = None,
) -> str:
    first = bookings[0]
    business = first.business
    business_name = html.escape(business.name if business else "ServGo")
    services = html.escape(_service_names(bookings))
    when = f"{first.booking_date.strftime('%d.%m.%Y')} в {first.booking_time.strftime('%H:%M')}"

    if event_kind == "booking_created":
        header = "✅ <b>Запись подтверждена!</b>"
        tail = "\n\nЖдём вас! 🎉"
    elif event_kind == "booking_cancelled":
        header = "❌ <b>Запись отменена</b>"
        tail = ""
    elif event_kind == "booking_rescheduled":
        header = "🔄 <b>Запись перенесена</b>"
        tail = ""
        if old_date and old_time:
            tail = (
                f"\n\nБыло: {old_date.strftime('%d.%m.%Y')} "
                f"в {old_time.strftime('%H:%M')}"
            )
    elif event_kind == "prepayment_rejected":
        header = "❌ <b>Предоплата не подтверждена</b>"
        tail = "\n\nУ вас ещё 10 минут, чтобы проверить перевод и отправить верный чек. Если деньги уже списаны, не переводите их повторно — свяжитесь с бизнесом."
    else:
        header = "🔔 <b>Статус записи обновлён</b>"
        tail = ""

    address_line = (
        f"\n📍 {html.escape(business.address)}"
        if business and business.address
        else ""
    )
    phone_line = (
        f"\n📞 {html.escape(business.phone)}"
        if business and business.phone
        else ""
    )
    return (
        f"{header}\n\n"
        f"🏢 {business_name}\n"
        f"💼 {services}\n"
        f"📅 {when}"
        f"{address_line}{phone_line}{tail}"
    )


async def clear_upcoming_reminder_deliveries(
    bookings: list[Booking],
    session: AsyncSession,
) -> None:
    """Allow reminders to be scheduled again after a visit is moved."""
    ids = [int(item.id) for item in bookings if getattr(item, "id", None)]
    if not ids:
        return
    await session.execute(
        delete(NotificationDelivery).where(
            NotificationDelivery.booking_id.in_(ids),
            NotificationDelivery.event_kind == "upcoming_reminder",
        )
    )
    for item in bookings:
        item.reminder_24h_sent = False
        item.reminder_2h_sent = False
        item.staff_reminder_24h_sent = False
        item.staff_reminder_2h_sent = False


async def dispatch_booking_event(
    bookings: list[Booking],
    session: AsyncSession,
    event_kind: str,
    *,
    include_client: bool = True,
    staff_audience: str = "all",
    old_date: date | None = None,
    old_time: time | None = None,
    prepayment_actions: bool = False,
    send_staff_telegram: bool = True,
    send_staff_webpush: bool = True,
    send_client_telegram: bool = True,
    send_client_webpush: bool = True,
) -> dict[str, int]:
    """Send one immediate booking event through Telegram and Web Push.

    Failures are isolated from booking persistence: callers should invoke this
    after their booking transaction commits.
    """
    loaded = await _fresh_bookings(session, bookings)
    if not loaded:
        return {"staff_telegram": 0, "staff_webpush": 0, "client_telegram": 0, "client_webpush": 0}

    first = loaded[0]
    staff_message = _staff_message(
        loaded,
        event_kind,
        old_date=old_date,
        old_time=old_time,
    )
    staff_targets = await _staff_telegram_targets(loaded, session, staff_audience)

    reply_markup = None
    if prepayment_actions:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Подтвердить оплату",
                        "callback_data": f"confirm_prepayment:{first.id}:{first.version}",
                    }
                ],
                [
                    {
                        "text": "❌ Нет оплаты / отклонить",
                        "callback_data": f"reject_prepayment:{first.id}:{first.version}",
                    }
                ],
            ]
        }

    staff_telegram = 0
    if send_staff_telegram:
        for telegram_id in staff_targets:
            if await _telegram_send(
                telegram_id,
                staff_message,
                reply_markup=reply_markup,
            ):
                staff_telegram += 1

    staff_webpush = 0
    if send_staff_webpush:
        try:
            staff_webpush, _ = await send_staff_event_webpush(
                loaded,
                session,
                event_kind,
                audience=staff_audience,
                old_date=old_date,
                old_time=old_time,
            )
        except Exception as exc:
            logger.warning("Staff Web Push event %s failed: %s", event_kind, exc)

    client_telegram = 0
    client_webpush = 0
    if include_client and event_kind != "prepayment_submitted":
        client = first.client
        if (
            send_client_telegram
            and client
            and client.telegram_id
            and int(client.telegram_id) > 0
        ):
            client_markup = None
            if event_kind == "prepayment_rejected":
                client_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "✅ Оплата отправлена",
                                "callback_data": f"prepay_sent:{first.id}",
                            }
                        ],
                        [
                            {
                                "text": "📸 Прикрепить чек",
                                "callback_data": f"prepay_attach_receipt:{first.id}",
                            }
                        ],
                    ]
                }
            if await _telegram_send(
                int(client.telegram_id),
                _client_message(
                    loaded,
                    event_kind,
                    old_date=old_date,
                    old_time=old_time,
                ),
                reply_markup=client_markup,
            ):
                client_telegram = 1
        if send_client_webpush:
            try:
                client_webpush = await send_client_event_webpush(
                    loaded,
                    session,
                    event_kind,
                    old_date=old_date,
                    old_time=old_time,
                )
            except Exception as exc:
                logger.warning("Client Web Push event %s failed: %s", event_kind, exc)

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    logger.info(
        "Booking event=%s booking_ids=%s staff_tg=%s staff_push=%s client_tg=%s client_push=%s",
        event_kind,
        [b.id for b in loaded],
        staff_telegram,
        staff_webpush,
        client_telegram,
        client_webpush,
    )
    return {
        "staff_telegram": staff_telegram,
        "staff_webpush": staff_webpush,
        "client_telegram": client_telegram,
        "client_webpush": client_webpush,
    }
