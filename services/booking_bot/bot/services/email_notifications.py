"""SMTP email notifications for ServGo bookings.

The channel is optional. If SMTP_HOST / SMTP_FROM_EMAIL are not configured,
booking flows continue normally and email delivery is skipped.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.database import async_session_maker
from database.models import Booking

logger = logging.getLogger(__name__)


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def email_channel_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM_EMAIL"))


def _is_loopback_host(host: str) -> bool:
    value = (host or "").strip().lower()
    return value in ("localhost", "::1") or value.startswith("127.")


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send one message through configured SMTP without blocking the event loop."""
    host = os.getenv("SMTP_HOST")
    from_email = os.getenv("SMTP_FROM_EMAIL")
    if not host or not from_email or not to_email:
        return False

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    from_name = os.getenv("SMTP_FROM_NAME", "ServGo")
    use_ssl = _truthy(os.getenv("SMTP_USE_SSL"), False)
    # Почтовый сервер на этой же машине (127.0.0.1): трафик не покидает сервер, а сертификат выписан
    # на имя (mail.servgo.ru), поэтому проверка при STARTTLS по IP всегда падала с «IP address mismatch».
    # Для внешнего сервера шифрование с проверкой сертификата остаётся по умолчанию.
    starttls = _truthy(os.getenv("SMTP_STARTTLS"), not use_ssl and not _is_loopback_host(host))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = to_email
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    def _send_sync() -> None:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(
                host,
                port,
                timeout=12,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(host, port, timeout=12)
        try:
            smtp.ehlo()
            if starttls and not use_ssl:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(message)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()

    try:
        await asyncio.to_thread(_send_sync)
        return True
    except Exception as exc:
        logger.warning("Email delivery to %s failed: %s", to_email, exc)
        return False


def _service_names(bookings: Iterable[Booking]) -> str:
    names: list[str] = []
    for booking in bookings:
        service = getattr(booking, "service", None)
        if service and service.name not in names:
            names.append(service.name)
    return ", ".join(names) or "Запись"


def _booking_link(booking: Booking) -> str:
    base = os.getenv("SERVGO_PUBLIC_URL", "https://servgo.ru").rstrip("/")
    if booking.notify_token:
        return f"{base}/b/{booking.notify_token}"
    return f"{base}/my"


async def send_booking_email(
    bookings: list[Booking],
    kind: str,
) -> bool:
    """Send a client booking email for created/status/reminder events."""
    if not bookings:
        return False

    first = bookings[0]
    client = getattr(first, "client", None)
    to_email = getattr(client, "email", None) if client else None
    if not to_email:
        return False

    business = getattr(first, "business", None)
    business_name = getattr(business, "name", None) or "ServGo"
    employee = getattr(first, "employee", None)
    employee_name = getattr(employee, "name", None)
    services = _service_names(bookings)
    date_text = first.booking_date.strftime("%d.%m.%Y")
    time_text = first.booking_time.strftime("%H:%M")
    link = _booking_link(first)

    if kind == "2h":
        subject = f"Через 2 часа запись — {business_name}"
        lead = "Напоминаем: до вашей записи осталось около 2 часов."
    elif kind == "24h":
        subject = f"Напоминание о записи — {business_name}"
        lead = "Напоминаем о предстоящей записи."
    elif kind in {"confirmed", "prepayment_confirmed"}:
        subject = (
            f"Предоплата подтверждена — {business_name}"
            if kind == "prepayment_confirmed"
            else f"Запись подтверждена — {business_name}"
        )
        lead = (
            "Предоплата получена владельцем. Ваша запись подтверждена."
            if kind == "prepayment_confirmed"
            else "Владелец подтвердил вашу запись."
        )
    elif kind == "cancelled":
        subject = f"Запись отменена — {business_name}"
        lead = "Ваша запись была отменена."
    elif kind == "prepayment_rejected":
        subject = f"Предоплата не подтверждена — {business_name}"
        lead = "Владелец пока не подтвердил получение предоплаты. У вас ещё 10 минут, чтобы проверить перевод и отправить верный чек. Если деньги уже списаны, не переводите их повторно — свяжитесь с бизнесом."
    else:
        subject = f"Ваша запись в {business_name}"
        if first.status == "pending_prepayment":
            lead = "Запись создана. Для окончательного подтверждения требуется предоплата."
        else:
            lead = "Запись успешно создана."

    details = [
        f"Бизнес: {business_name}",
        f"Услуга: {services}",
        f"Дата: {date_text}",
        f"Время: {time_text}",
    ]
    if employee_name:
        details.append(f"Мастер/ресурс: {employee_name}")
    if getattr(business, "address", None):
        details.append(f"Адрес: {business.address}")

    text_body = lead + "\n\n" + "\n".join(details) + f"\n\nСтатус записи: {link}"

    safe_details = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in details
    )
    html_body = (
        f"<p>{html.escape(lead)}</p>"
        f"<ul>{safe_details}</ul>"
        f'<p><a href="{html.escape(link, quote=True)}">Открыть статус записи в ServGo</a></p>'
        "<p style=\"color:#666\">Это автоматическое уведомление ServGo.</p>"
    )

    return await send_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


async def send_booking_email_by_ids(
    booking_ids: list[int],
    kind: str,
) -> bool:
    """Reload bookings in a fresh session and send email safely from a task."""
    if not booking_ids:
        return False
    async with async_session_maker() as session:
        result = await session.execute(
            select(Booking)
            .where(Booking.id.in_(booking_ids))
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.employee),
            )
            .order_by(Booking.booking_date, Booking.booking_time, Booking.id)
        )
        bookings = list(result.scalars().all())
        return await send_booking_email(bookings, kind)
