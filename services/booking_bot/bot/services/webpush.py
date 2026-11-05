"""Web Push transport for ServGo client and staff reminders."""

from datetime_utils import utc_now
import json
import logging
import os
from datetime import datetime
from typing import Iterable

from pywebpush import WebPushException, webpush_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Booking, BusinessOwner, Employee, PushSubscription, User

logger = logging.getLogger(__name__)


def webpush_configured() -> bool:
    return bool(
        os.getenv("VAPID_PUBLIC_KEY", "").strip()
        and os.getenv("VAPID_PRIVATE_KEY", "").strip()
        and os.getenv("VAPID_SUBJECT", "").strip()
    )


def _status_from_exception(exc: WebPushException) -> int | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    return getattr(response, "status", None) or getattr(response, "status_code", None)


async def send_webpush_to_subscriptions(
    subscriptions: Iterable[PushSubscription],
    payload: dict,
    session: AsyncSession,
    ttl: int = 86400,
) -> int:
    if not webpush_configured():
        return 0

    private_key = os.environ["VAPID_PRIVATE_KEY"].strip()
    subject = os.environ["VAPID_SUBJECT"].strip()
    sent = 0

    for subscription in subscriptions:
        try:
            await webpush_async(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                ttl=ttl,
                timeout=10,
            )
            subscription.last_success_at = utc_now()
            subscription.failure_count = 0
            subscription.updated_at = utc_now()
            sent += 1
        except WebPushException as exc:
            status = _status_from_exception(exc)
            subscription.failure_count = (subscription.failure_count or 0) + 1
            subscription.updated_at = utc_now()
            if status in (404, 410):
                subscription.is_active = False
            logger.warning(
                "Web Push failed for subscription %s status=%s: %s",
                subscription.id,
                status,
                exc,
            )
        except Exception as exc:
            subscription.failure_count = (subscription.failure_count or 0) + 1
            subscription.updated_at = utc_now()
            logger.warning("Web Push failed for subscription %s: %s", subscription.id, exc)

    await session.flush()
    return sent


async def send_booking_webpush(
    bookings: list[Booking],
    session: AsyncSession,
    reminder_kind: str,
) -> int:
    if not bookings:
        return 0

    first = bookings[0]
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == first.client_id,
            PushSubscription.is_active == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return 0

    business_name = first.business.name if first.business else "ServGo"
    services = ", ".join(
        b.service.name for b in bookings if getattr(b, "service", None)
    ) or "Запись"
    time_text = first.booking_time.strftime("%H:%M")

    if reminder_kind == "2h":
        title = "⏰ Через 2 часа ваша запись"
        body = f"{business_name} · {time_text} · {services}"
    else:
        title = "🔔 Напоминание о записи"
        body = f"{business_name} · {first.booking_date.strftime('%d.%m.%Y')} в {time_text} · {services}"

    target = f"/b/{first.notify_token}" if first.notify_token else "/my"
    payload = {
        "title": title,
        "body": body,
        "url": target,
        "booking_id": first.id,
        "tag": f"servgo-booking-{first.id}-{reminder_kind}",
    }

    return await send_webpush_to_subscriptions(subscriptions, payload, session)

async def send_booking_status_webpush(
    bookings: list[Booking],
    session: AsyncSession,
    status_kind: str,
) -> int:
    """Send an immediate client push when payment/booking status changes."""
    if not bookings:
        return 0

    first = bookings[0]
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == first.client_id,
            PushSubscription.is_active == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return 0

    business_name = first.business.name if first.business else "ServGo"
    time_text = first.booking_time.strftime("%H:%M")

    if status_kind == "prepayment_confirmed":
        title = "✅ Предоплата подтверждена"
        body = f"{business_name} · запись подтверждена на {first.booking_date.strftime('%d.%m.%Y')} в {time_text}"
    elif status_kind == "prepayment_rejected":
        title = "❌ Предоплата не подтверждена"
        body = f"{business_name} · проверьте оплату и реквизиты"
    else:
        title = "ServGo · статус записи обновлён"
        body = f"{business_name} · {first.booking_date.strftime('%d.%m.%Y')} в {time_text}"

    target = f"/b/{first.notify_token}" if first.notify_token else "/my"
    payload = {
        "title": title,
        "body": body,
        "url": target,
        "booking_id": first.id,
        "tag": f"servgo-booking-{first.id}-{status_kind}",
    }
    return await send_webpush_to_subscriptions(subscriptions, payload, session)

async def _staff_user_ids(
    bookings: list[Booking],
    session: AsyncSession,
    audience: str = "all",
) -> set[int]:
    """Resolve PWA users for owners, assigned masters and administrators."""
    if not bookings:
        return set()
    first = bookings[0]
    business = first.business
    if business is None:
        return set()

    user_ids: set[int] = set()

    if audience in ("owner", "all"):
        if getattr(business, "owner_id", None):
            user_ids.add(int(business.owner_id))
        owner_rows = await session.execute(
            select(BusinessOwner.user_id).where(BusinessOwner.business_id == first.business_id)
        )
        user_ids.update(int(row[0]) for row in owner_rows.all())

    telegram_ids: set[int] = set()
    if audience in ("master", "all"):
        telegram_ids.update(
            int(b.employee.telegram_id)
            for b in bookings
            if getattr(b, "employee", None) is not None
            and getattr(b.employee, "telegram_id", None)
            and int(b.employee.telegram_id) > 0
        )

    if audience == "all":
        admin_rows = await session.execute(
            select(Employee.telegram_id).where(
                Employee.business_id == first.business_id,
                Employee.is_active.is_(True),
                Employee.role.in_(["administrator", "master_and_admin"]),
                Employee.telegram_id.is_not(None),
            )
        )
        telegram_ids.update(
            int(row[0]) for row in admin_rows.all() if row[0] and int(row[0]) > 0
        )

    if telegram_ids:
        employee_users = await session.execute(
            select(User.id).where(User.telegram_id.in_(telegram_ids))
        )
        user_ids.update(int(row[0]) for row in employee_users.all())
    return user_ids


async def send_staff_booking_webpush(
    bookings: list[Booking],
    session: AsyncSession,
    reminder_kind: str,
    audience: str = "all",
) -> tuple[int, int]:
    """Send a configurable upcoming-visit push to owners or masters."""
    if not bookings:
        return 0, 0

    first = bookings[0]
    user_ids = await _staff_user_ids(bookings, session, audience)
    if not user_ids:
        return 0, 0

    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id.in_(user_ids),
            PushSubscription.is_active == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return 0, 0

    service_names = ", ".join(
        dict.fromkeys(
            b.service.name for b in bookings if getattr(b, "service", None)
        )
    ) or "Запись"
    client_name = (
        first.client.full_name
        if getattr(first, "client", None) is not None
        else "Клиент"
    )
    time_text = first.booking_time.strftime("%H:%M")

    title = "⏰ Скоро клиент"
    body = (
        f"{client_name} · {first.booking_date.strftime('%d.%m.%Y')} "
        f"в {time_text} · {service_names}"
    )
    payload = {
        "title": title,
        "body": body,
        "url": "/admin/schedule",
        "booking_id": first.id,
        "tag": f"servgo-{audience}-reminder-{first.id}-{reminder_kind}",
    }
    sent = await send_webpush_to_subscriptions(subscriptions, payload, session)
    return sent, len(subscriptions)


async def send_staff_event_webpush(
    bookings: list[Booking],
    session: AsyncSession,
    event_kind: str,
    *,
    audience: str = "all",
    old_date=None,
    old_time=None,
) -> tuple[int, int]:
    """Immediate PWA event for people involved in a booking."""
    if not bookings:
        return 0, 0
    first = bookings[0]
    user_ids = await _staff_user_ids(bookings, session, audience)
    if not user_ids:
        return 0, 0
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id.in_(user_ids),
            PushSubscription.is_active == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return 0, 0

    business_name = first.business.name if first.business else "ServGo"
    client_name = first.client.full_name if getattr(first, "client", None) else "Клиент"
    service_names = ", ".join(
        dict.fromkeys(
            b.service.name for b in bookings if getattr(b, "service", None)
        )
    ) or "Запись"
    when = f"{first.booking_date.strftime('%d.%m.%Y')} в {first.booking_time.strftime('%H:%M')}"

    if event_kind == "booking_created":
        title = "🆕 Новая запись"
        body = f"{business_name} · {client_name} · {when} · {service_names}"
    elif event_kind == "booking_cancelled":
        title = "❌ Запись отменена"
        body = f"{client_name} · {when} · {service_names}"
    elif event_kind == "booking_rescheduled":
        title = "🔄 Запись перенесена"
        old = ""
        if old_date and old_time:
            old = f" (было {old_date.strftime('%d.%m')} {old_time.strftime('%H:%M')})"
        body = f"{client_name} · теперь {when}{old}"
    elif event_kind == "prepayment_submitted":
        title = "💳 Клиент отправил предоплату"
        body = f"{business_name} · {client_name} · {when}"
    elif event_kind == "rental_request_created":
        title = "🛥 Новая заявка на аренду"
        body = f"{business_name} · {client_name} · {when}"
    else:
        title = "ServGo · запись обновлена"
        body = f"{client_name} · {when}"

    payload = {
        "title": title,
        "body": body,
        "url": "/admin/prepayments" if event_kind == "prepayment_submitted" else "/admin/schedule",
        "booking_id": first.id,
        "tag": f"servgo-staff-event-{event_kind}-{first.id}",
        "event_kind": event_kind,
    }
    sent = await send_webpush_to_subscriptions(subscriptions, payload, session)
    return sent, len(subscriptions)


async def send_client_event_webpush(
    bookings: list[Booking],
    session: AsyncSession,
    event_kind: str,
    *,
    old_date=None,
    old_time=None,
) -> int:
    """Immediate client PWA event."""
    if not bookings:
        return 0
    first = bookings[0]
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == first.client_id,
            PushSubscription.is_active == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return 0

    business_name = first.business.name if first.business else "ServGo"
    when = f"{first.booking_date.strftime('%d.%m.%Y')} в {first.booking_time.strftime('%H:%M')}"
    if event_kind == "booking_created":
        title = "✅ Запись создана"
        body = f"{business_name} · {when}"
    elif event_kind == "rental_request_created":
        title = "🛥 Заявка на аренду создана"
        body = f"{business_name} · {when}"
    elif event_kind == "booking_cancelled":
        title = "❌ Запись отменена"
        body = f"{business_name} · {when}"
    elif event_kind == "booking_rescheduled":
        title = "🔄 Запись перенесена"
        body = f"{business_name} · теперь {when}"
    else:
        title = "ServGo · запись обновлена"
        body = f"{business_name} · {when}"

    target = f"/b/{first.notify_token}" if first.notify_token else "/my"
    payload = {
        "title": title,
        "body": body,
        "url": target,
        "booking_id": first.id,
        "tag": f"servgo-client-event-{event_kind}-{first.id}",
    }
    return await send_webpush_to_subscriptions(subscriptions, payload, session)
