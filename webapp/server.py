"""
Веб-сервер для Telegram Mini App календаря администратора
"""
import asyncio
import json
import hashlib
import hmac
import logging
import os
import re
import smtplib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import parse_qs, unquote

import jinja2
from aiohttp import web
from sqlalchemy.orm import selectinload

from aiogram import Bot
from bot.config import BOT_TOKEN, ADMIN_IDS, BOT_USERNAME
from servgo_auth import (
    AUTH_COOKIE_NAME,
    auth_cookie_kwargs,
    auth_dict_from_payload,
    clear_cookie_kwargs,
    create_or_update_user as shared_create_or_update_user,
    create_session as shared_create_session,
    expire_session as shared_expire_session,
    get_session as shared_get_session,
    init_tables as init_shared_auth_tables,
    verify_telegram_login,
)
from bot.services.notifications import (
    notify_master_new_booking,
    notify_owner_new_booking,
    notify_administrator_new_booking,
    notify_owner_rescheduled_booking,
)
from database import async_session_maker
from database.models import MarketingLead
from database.crud import (
    get_administrator_by_telegram_id,
    get_administrators_by_telegram_id,
    get_business_bookings,
    get_business_employees,
    get_booking_by_id,
    update_booking_status,
    update_booking_with_lock,
    OptimisticLockError,
    get_employee_by_id,
    get_user_businesses,
    get_user_by_telegram_id,
    get_business_by_id,
    get_business_services,
    get_business_main_services,
    get_business_clients,
    get_business_subscriptions_stats,
    get_all_active_businesses,
    get_employees_for_services,
    create_booking,
    get_or_create_user,
    get_or_create_guest_user,
    get_service_by_id,
    is_employee_day_off,
    get_user_bookings,
    get_business_subscriptions,
    get_subscription_by_id,
    purchase_subscription,
    get_client_subscriptions,
    get_client_subscription_by_id,
    update_client_subscription_status,
    set_payment_receipt,
    get_subscription_remaining,
    is_business_owner,
    get_employee_time_pricing,
    calculate_time_based_price_detailed,
)

logger = logging.getLogger(__name__)

LANDING_REQUEST_RECIPIENT = "artur_grunvald@mail.ru"
LANDING_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_landing_field(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _landing_niche_label(value) -> str:
    """Known niche slug -> its label; 'other' -> 'Другое'; anything else -> ''."""
    slug = _clean_landing_field(value, 60)
    if slug == "other":
        return "Другое"
    if not slug:
        return ""
    from webapp.niches import get_niche

    niche = get_niche(slug)
    if not niche:
        return ""
    return (niche.get("meta") or {}).get("short_label") or slug


def landing_request_tag(payload: dict) -> str:
    """Short tag for the lead: '[Бесплатное место · Салоны красоты]'."""
    parts = []
    if payload.get("intent") == "free_seat":
        parts.append("Бесплатное место")
    if payload.get("niche"):
        parts.append(payload["niche"])
    return f"[{' · '.join(parts)}]" if parts else ""


def validate_landing_request_payload(data: dict) -> dict:
    """Validate and normalize a public landing-page connection request."""
    name = _clean_landing_field(data.get("name"), 120)
    phone = _clean_landing_field(data.get("phone"), 80)
    email = _clean_landing_field(data.get("email"), 160)
    comment = _clean_landing_field(data.get("comment"), 1200)
    self_reported_source = _clean_landing_field(data.get("self_reported_source"), 500)

    if not name:
        raise ValueError("Укажите имя")
    if not phone and not email:
        raise ValueError("Укажите телефон или email")
    if email and not LANDING_EMAIL_RE.match(email):
        raise ValueError("Проверьте email")
    # Commercial SEO applications require explicit contact-data consent.
    # The legacy general landing flow stays backwards compatible.
    seo_source = data.get("utm_source") == "servgo_blog"
    if seo_source and data.get("consent") is not True:
        raise ValueError("Подтвердите согласие на обработку данных для ответа на заявку")
    module = str(data.get("seo_module") or "platform")
    if module not in {"platform", "booking", "growth", "mail", "shifts", "seo"}:
        module = "platform"
    requested_module = str(data.get("requested_module") or module)
    if requested_module not in {"platform", "booking", "growth", "mail", "shifts", "seo"}:
        requested_module = "platform"

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "comment": comment,
        "self_reported_source": self_reported_source,
        "experiment_id": _clean_landing_field(data.get("experiment_id"), 100),
        "utm_source": _clean_landing_field(data.get("utm_source"), 255),
        "seo_module": module if seo_source else "",
        "requested_module": requested_module,
        "consent": data.get("consent") is True,
        "utm_medium": _clean_landing_field(data.get("utm_medium"), 255),
        "utm_campaign": _clean_landing_field(data.get("utm_campaign"), 255),
        "utm_content": _clean_landing_field(data.get("utm_content"), 255),
        "utm_term": _clean_landing_field(data.get("utm_term"), 255),
        "referrer": _clean_landing_field(data.get("referrer"), 2000),
        "landing_path": _clean_landing_field(data.get("landing_path"), 500),
        "niche": _landing_niche_label(data.get("niche")),
        "intent": "free_seat" if data.get("intent") == "free_seat" else "",
        "recipient": os.getenv("LANDING_REQUEST_EMAIL", LANDING_REQUEST_RECIPIENT),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_landing_request_message(payload: dict) -> str:
    lines = [
        (
            "Новая заявка: занять бесплатное место"
            if payload.get("intent") == "free_seat"
            else "Новая заявка на подключение бизнеса"
        ),
        "",
        f"Ниша: {payload.get('niche') or 'не указана'}",
        f"Имя: {payload['name']}",
        f"Телефон: {payload['phone'] or 'не указан'}",
        f"Email: {payload['email'] or 'не указан'}",
        f"Комментарий: {payload['comment'] or 'не указан'}",
        f"Откуда узнали: {payload.get('self_reported_source') or 'не указано'}",
        "",
        "Атрибуция:",
        f"Experiment: {payload.get('experiment_id') or 'нет'}",
        f"UTM: source={payload.get('utm_source') or '-'}; medium={payload.get('utm_medium') or '-'}; campaign={payload.get('utm_campaign') or '-'}",
        f"SEO статья: {payload.get('utm_content') or '-'}" if payload.get("utm_source") == "servgo_blog" else "SEO статья: -",
        f"Интересующий модуль: {payload.get('requested_module') or '-'}",
        f"Согласие на связь: {'да' if payload.get('consent') else 'нет'}",
        f"Landing: {payload.get('landing_path') or '-'}",
        f"Referrer: {payload.get('referrer') or '-'}",
        "",
        f"Получатель email: {payload['recipient']}",
        f"Время: {payload['created_at']}",
    ]
    return "\n".join(lines)


async def persist_landing_marketing_lead(payload: dict) -> int:
    """Store a contact request, verifying SEO attribution against a real click."""
    seo_source = payload.get("utm_source") == "servgo_blog"
    verified = False
    if seo_source:
        from webapp.seo_commercial import verify_click
        from database.database import engine
        module = payload.get("seo_module") or "platform"
        verified = (
            payload.get("utm_medium") == "seo"
            and payload.get("utm_campaign") == f"seo_{module}"
            and await verify_click(engine, payload.get("utm_term") or "",
                                   payload.get("utm_content") or "", module)
        )
    confidence = ("verified" if verified else "unverified") if seo_source else (
        "high" if payload.get("experiment_id") or payload.get("utm_campaign") else (
        "medium" if payload.get("self_reported_source") or payload.get("referrer") else "low"
    ))
    async with async_session_maker() as session:
        lead = MarketingLead(
            name=payload["name"],
            phone=payload.get("phone") or None,
            email=payload.get("email") or None,
            comment=(
                " ".join(x for x in (landing_request_tag(payload), payload.get("comment") or "") if x)
                or None
            ),
            experiment_id=payload.get("experiment_id") or None,
            utm_source=payload.get("utm_source") or None,
            utm_medium=payload.get("utm_medium") or None,
            utm_campaign=payload.get("utm_campaign") or None,
            utm_content=payload.get("utm_content") or None,
            utm_term=payload.get("utm_term") or None,
            referrer=payload.get("referrer") or None,
            landing_path=payload.get("landing_path") or None,
            self_reported_source=payload.get("self_reported_source") or None,
            attribution_confidence=confidence,
            status="pending" if seo_source else "new",
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        return lead.id


async def mark_received_seo_lead(lead_id: int) -> bool:
    """Accepted only after contact was actually delivered and persisted."""
    from sqlalchemy import update
    async with async_session_maker() as session:
        result = await session.execute(
            update(MarketingLead).where(
                MarketingLead.id == lead_id,
                MarketingLead.status == "pending",
            ).values(status="received"))
        await session.commit()
        return result.rowcount == 1


def send_landing_request_email(payload: dict, *, subject: str | None = None, message_body: str | None = None) -> bool:
    """Send request email when SMTP settings are configured."""
    host = os.getenv("LANDING_SMTP_HOST") or os.getenv("SMTP_HOST")
    if not host:
        logger.warning("Landing request email skipped: SMTP host is not configured")
        return False

    port = int(os.getenv("LANDING_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
    username = os.getenv("LANDING_SMTP_USER") or os.getenv("SMTP_USER")
    password = os.getenv("LANDING_SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD")
    use_ssl = (os.getenv("LANDING_SMTP_SSL") or os.getenv("SMTP_SSL") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    use_tls = (os.getenv("LANDING_SMTP_TLS") or os.getenv("SMTP_TLS") or "true").lower() not in (
        "0",
        "false",
        "no",
    )
    sender = os.getenv("LANDING_SMTP_FROM") or os.getenv("SMTP_FROM") or username or payload["recipient"]

    message = EmailMessage()
    message["Subject"] = subject or "Заявка на подключение бизнеса ServGo"
    message["From"] = sender
    message["To"] = payload["recipient"]
    if payload.get("email"):
        message["Reply-To"] = payload["email"]
    message.set_content(message_body if message_body is not None else format_landing_request_message(payload))

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=15) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
    return True


def _landing_env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def get_landing_request_bot_token() -> str | None:
    """Return the bot token that should send promo landing requests."""
    token = os.getenv("LANDING_NOTIFY_BOT_TOKEN") or os.getenv("MLM_BOT_TOKEN")
    if token:
        return token
    if _landing_env_flag("LANDING_NOTIFY_ALLOW_BOOKING_BOT_FALLBACK", False):
        return BOT_TOKEN
    return None


def is_landing_request_telegram_required() -> bool:
    return _landing_env_flag("LANDING_NOTIFY_REQUIRE_TELEGRAM", True)


async def send_landing_request_telegram(payload: dict) -> bool:
    """Send request details to configured Telegram admin chats."""
    token = get_landing_request_bot_token()
    raw_chat_ids = os.getenv("LANDING_NOTIFY_CHAT_IDS") or ",".join(str(chat_id) for chat_id in ADMIN_IDS)
    chat_ids = [chat_id.strip() for chat_id in raw_chat_ids.split(",") if chat_id.strip()]

    if not token or not chat_ids:
        logger.warning(
            "Landing request Telegram notification skipped: set MLM_BOT_TOKEN "
            "or LANDING_NOTIFY_BOT_TOKEN and LANDING_NOTIFY_CHAT_IDS"
        )
        return False

    bot = Bot(token=token)
    try:
        sent = 0
        message = format_landing_request_message(payload)
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                sent += 1
            except Exception as exc:
                logger.error(f"Landing request Telegram notification failed for {chat_id}: {exc}")
        return sent > 0
    finally:
        await bot.session.close()


async def api_landing_connect_request(request: web.Request) -> web.Response:
    """Receive the normal promo lead or an explicitly routed onboarding operation.

    Promo's existing public endpoint is already published through nginx.
    Reuse exactly that URL for the onboarding submit and admin-only read API,
    so no new nginx route or catch-all changes are required.
    """
    onboarding_operation = request.query.get("onboarding")
    if onboarding_operation == "submit":
        return await request.app["onboarding_submit_handler"](request)
    if onboarding_operation == "view":
        return await request.app["onboarding_review_handler"](request)
    if request.query.get("seo") == "report":
        return await request.app["seo_report_handler"](request)
    try:
        payload = validate_landing_request_payload(await request.json())
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        return web.json_response({"error": "Некорректные данные заявки"}, status=400)

    is_seo_lead = payload.get("utm_source") == "servgo_blog"
    if is_seo_lead:
        origin = request.headers.get("Origin")
        if origin and origin not in {"https://servgo.ru", "https://promo.servgo.ru"}:
            return web.json_response({"error": "Недопустимый источник заявки"}, status=403)
    email_sent = False
    telegram_sent = False
    tracking_saved = False
    lead_id = None

    try:
        lead_id = await persist_landing_marketing_lead(payload)
        tracking_saved = True
    except Exception as exc:
        logger.error(f"Landing request attribution persistence failed: {exc}")
    if is_seo_lead and not tracking_saved:
        # An unsaved business request must not be called a successful conversion.
        return web.json_response({"error": "Не удалось сохранить заявку. Повторите позже."},
                                 status=503)

    try:
        email_sent = await asyncio.to_thread(send_landing_request_email, payload)
    except Exception as exc:
        logger.error(f"Landing request email notification failed: {exc}")

    try:
        telegram_sent = await send_landing_request_telegram(payload)
    except Exception as exc:
        logger.error(f"Landing request Telegram notification failed: {exc}")

    if is_landing_request_telegram_required() and not telegram_sent:
        return web.json_response(
            {
                "error": "Не удалось отправить заявку в Telegram. Попробуйте написать в Telegram.",
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "tracking_saved": tracking_saved,
                "lead_id": lead_id,
            },
            status=502,
        )

    if not email_sent and not telegram_sent:
        return web.json_response(
            {"error": "Не удалось отправить заявку. Попробуйте написать в Telegram."},
            status=502,
        )

    if is_seo_lead:
        try:
            if not await mark_received_seo_lead(lead_id):
                raise RuntimeError("SEO lead status transition unavailable")
        except Exception:
            logger.exception("SEO commercial request was delivered but acceptance state failed")
            return web.json_response({"error": "Не удалось подтвердить приём заявки. Свяжитесь с поддержкой."},
                                     status=503)

    return web.json_response(
        {
            "success": True,
            "email_sent": email_sent,
            "telegram_sent": telegram_sent,
            "tracking_saved": tracking_saved,
            "lead_id": lead_id,
        }
    )

# === Web Auth (без Telegram) ===

def generate_owner_token(telegram_id: int) -> str:
    """Генерация токена для веб-доступа владельца"""
    return hmac.new(
        BOT_TOKEN.encode(),
        f"web_owner_{telegram_id}".encode(),
        hashlib.sha256
    ).hexdigest()


def generate_owner_pin(telegram_id: int) -> str:
    """Генерация 6-значного PIN для входа"""
    token = generate_owner_token(telegram_id)
    # Берём первые 6 цифр из hex-токена (конвертируем в число)
    pin_num = int(token[:8], 16) % 1000000
    return f"{pin_num:06d}"


def create_session_cookie(telegram_id: int) -> str:
    """Создание подписанного session cookie"""
    data = f"{telegram_id}:{int(datetime.utcnow().timestamp())}"
    signature = hmac.new(
        BOT_TOKEN.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{data}:{signature}"


def verify_session_cookie(cookie: str) -> int | None:
    """Проверка session cookie, возвращает telegram_id"""
    try:
        parts = cookie.split(':')
        if len(parts) != 3:
            return None
        telegram_id_str, timestamp_str, received_sig = parts
        data = f"{telegram_id_str}:{timestamp_str}"
        expected_sig = hmac.new(
            BOT_TOKEN.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        if expected_sig != received_sig:
            return None
        # Cookie действует 30 дней
        timestamp = int(timestamp_str)
        if datetime.utcnow().timestamp() - timestamp > 30 * 24 * 3600:
            return None
        return int(telegram_id_str)
    except Exception:
        return None


def validate_webapp_data(init_data: str) -> dict | None:
    """Проверка данных от Telegram WebApp"""
    if not init_data:
        logger.warning("Empty init_data received")
        return None

    try:
        # URL decode the init_data
        init_data = unquote(init_data)
        parsed = parse_qs(init_data)

        logger.info(f"Parsed init_data keys: {list(parsed.keys())}")

        data_check_string_parts = []

        for key in sorted(parsed.keys()):
            if key != 'hash':
                data_check_string_parts.append(f"{key}={parsed[key][0]}")

        data_check_string = '\n'.join(data_check_string_parts)

        secret_key = hmac.new(
            b'WebAppData',
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        received_hash = parsed.get('hash', [''])[0]

        if calculated_hash == received_hash:
            user_data = json.loads(parsed.get('user', ['{}'])[0])
            logger.info(f"Validated user: {user_data.get('id')}")
            return user_data
        else:
            logger.warning(f"Hash mismatch. Expected: {calculated_hash[:16]}..., Got: {received_hash[:16]}...")
            return None
    except Exception as e:
        logger.error(f"Error validating webapp data: {e}")
        return None


async def get_authenticated_telegram_user(request: web.Request) -> dict | None:
    """Return Telegram identity from WebApp initData or shared Servgo session."""
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user = validate_webapp_data(init_data)
    if user:
        return user

    shared_token = request.cookies.get(AUTH_COOKIE_NAME)
    session = shared_get_session(shared_token)
    if not session:
        return None

    return {
        'id': session['telegram_id'],
        'first_name': session.get('first_name', '') or '',
        'last_name': session.get('last_name', '') or '',
        'username': session.get('username', '') or '',
        'photo_url': session.get('photo_url', '') or '',
    }


async def get_or_create_booking_user_from_auth(session, auth_user: dict):
    telegram_id = int(auth_user['id'])
    first_name = auth_user.get('first_name', '') or ''
    last_name = auth_user.get('last_name', '') or ''
    username = auth_user.get('username', '') or ''
    full_name = f"{first_name} {last_name}".strip() or username or f"User {telegram_id}"
    return await get_or_create_user(
        session,
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
    )


async def check_admin_access(telegram_id: int) -> tuple:
    """Проверка доступа администратора, возвращает (admin, business_id)"""
    async with async_session_maker() as session:
        # Сначала проверяем владельца (ADMIN_IDS имеет приоритет над ролью Employee)
        if telegram_id in ADMIN_IDS:
            user = await get_user_by_telegram_id(session, telegram_id)
            if user:
                businesses = await get_user_businesses(session, user.id)
                if businesses:
                    # Возвращаем первый бизнес владельца
                    business = businesses[0]
                    logger.info(f"User {telegram_id} is owner of business {business.id}")
                    return user, business.id

        # Затем проверяем, является ли пользователь администратором бизнеса (Employee)
        admin = await get_administrator_by_telegram_id(session, telegram_id)
        if admin:
            logger.info(f"User {telegram_id} is administrator of business {admin.business_id}")
            return admin, admin.business_id

    logger.warning(f"User {telegram_id} has no admin access")
    return None, None


# === API Endpoints ===

async def api_calendar_data(request: web.Request) -> web.Response:
    """Получить данные календаря с записями"""
    try:
        user, admin, business_id = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        if not admin:
            return web.json_response({'error': 'Access denied'}, status=403)

        # Получаем параметры
        year = int(request.query.get('year', date.today().year))
        month = int(request.query.get('month', date.today().month))

        # Определяем диапазон дат
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)

        # Получаем записи
        async with async_session_maker() as session:
            bookings = await get_business_bookings(session, business_id, start_date, end_date)
            employees = await get_business_employees(session, business_id)

        # Формируем данные по дням
        calendar_data = {}
        for booking in bookings:
            day_key = booking.booking_date.isoformat()
            if day_key not in calendar_data:
                calendar_data[day_key] = []

            calendar_data[day_key].append({
                'id': booking.id,
                'time': booking.booking_time.strftime('%H:%M'),
                'client_name': booking.client.full_name if booking.client else 'Без имени',
                'client_phone': booking.client.phone if booking.client else None,
                'service_name': booking.service.name if booking.service else 'Услуга',
                'service_duration': booking.service.duration_minutes if booking.service else 0,
                'service_price': booking.service.price if booking.service else 0,
                'employee_name': booking.employee.name if booking.employee else 'Любой',
                'employee_id': booking.employee_id,
                'status': booking.status,
            })

        # Сортируем записи внутри дней по времени
        for day_key in calendar_data:
            calendar_data[day_key].sort(key=lambda x: x['time'])

        # Список мастеров для фильтра
        masters_list = [
            {'id': e.id, 'name': e.name, 'specialization': e.specialization}
            for e in employees if e.role in ('master', 'master_and_admin') and e.is_active
        ]

        logger.info(f"Returning {len(bookings)} bookings for business {business_id}")

        return web.json_response({
            'year': year,
            'month': month,
            'bookings': calendar_data,
            'masters': masters_list,
            'business_id': business_id,
        })

    except Exception as e:
        logger.error(f"Error in api_calendar_data: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def get_user_from_request(request: web.Request):
    """Получить пользователя из запроса (Telegram initData или web session cookie)"""
    user = await get_authenticated_telegram_user(request)

    # Fallback: проверяем web session cookie
    if not user:
        session_cookie = request.cookies.get('owner_session')
        if session_cookie:
            telegram_id = verify_session_cookie(session_cookie)
            if telegram_id:
                admin, business_id = await check_admin_access(telegram_id)
                if admin:
                    return {'id': telegram_id}, admin, business_id

    if not user:
        return None, None, None

    telegram_id = user.get('id')
    admin, business_id = await check_admin_access(telegram_id)
    return user, admin, business_id


async def api_booking_details(request: web.Request) -> web.Response:
    """Получить детали записи"""
    try:
        user, admin, business_id = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        if not admin:
            return web.json_response({'error': 'Access denied'}, status=403)

        booking_id = int(request.match_info['booking_id'])

        async with async_session_maker() as session:
            booking = await get_booking_by_id(session, booking_id)

            if not booking or booking.business_id != business_id:
                return web.json_response({'error': 'Booking not found'}, status=404)

            return web.json_response({
                'id': booking.id,
                'date': booking.booking_date.isoformat(),
                'time': booking.booking_time.strftime('%H:%M'),
                'client_name': booking.client.full_name if booking.client else 'Без имени',
                'client_phone': booking.client.phone if booking.client else None,
                'client_telegram_id': booking.client.telegram_id if booking.client else None,
                'service_name': booking.service.name if booking.service else 'Услуга',
                'service_duration': booking.service.duration_minutes if booking.service else 0,
                'service_price': booking.service.price if booking.service else 0,
                'employee_name': booking.employee.name if booking.employee else 'Любой',
                'employee_id': booking.employee_id,
                'status': booking.status,
                'notes': booking.notes,
                'created_at': booking.created_at.isoformat() if booking.created_at else None,
                'version': getattr(booking, 'version', 1),  # Версия для оптимистичной блокировки
            })

    except Exception as e:
        logger.error(f"Error in api_booking_details: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_update_booking_status(request: web.Request) -> web.Response:
    """Обновить статус записи с поддержкой оптимистичной блокировки"""
    try:
        user, admin, business_id = await get_user_from_request(request)

        logger.info(f"api_update_booking_status: user={user}, admin={admin}, business_id={business_id}")

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        if not admin:
            return web.json_response({'error': 'Access denied'}, status=403)

        booking_id = int(request.match_info['booking_id'])
        data = await request.json()
        new_status = data.get('status')
        expected_version = data.get('version')  # Опционально для оптимистичной блокировки

        logger.info(f"Updating booking {booking_id} to status {new_status}")

        valid_statuses = ['pending', 'confirmed', 'cancelled', 'completed']
        if new_status not in valid_statuses:
            return web.json_response({'error': 'Invalid status'}, status=400)

        async with async_session_maker() as session:
            booking = await get_booking_by_id(session, booking_id)

            logger.info(f"Found booking: {booking}, booking.business_id={booking.business_id if booking else None}")

            if not booking:
                return web.json_response({'error': 'Booking not found'}, status=404)

            # Проверяем доступ: либо это бизнес админа, либо пользователь - владелец этого бизнеса
            has_access = False
            if booking.business_id == business_id:
                has_access = True
                logger.info(f"Access granted: booking.business_id == business_id")
            else:
                # Проверяем, является ли пользователь владельцем бизнеса этой брони
                telegram_id = user.get('id') if isinstance(user, dict) else getattr(user, 'telegram_id', None)
                logger.info(f"Checking owner access: telegram_id={telegram_id}, ADMIN_IDS={ADMIN_IDS}")
                if telegram_id and telegram_id in ADMIN_IDS:
                    db_user = await get_user_by_telegram_id(session, telegram_id)
                    if db_user:
                        user_businesses = await get_user_businesses(session, db_user.id)
                        business_ids = [b.id for b in user_businesses]
                        logger.info(f"User businesses: {business_ids}, booking.business_id={booking.business_id}")
                        if any(b.id == booking.business_id for b in user_businesses):
                            has_access = True
                            logger.info(f"Access granted: user owns the business")

            if not has_access:
                logger.warning(f"Access denied for booking {booking_id}")
                return web.json_response({'error': 'Booking not found'}, status=404)

            # При подтверждении оплаты — проверяем, не занято ли время другой записью
            from sqlalchemy import select as sa_select
            from database.models import Booking
            if new_status == 'confirmed' and booking.status in ('pending_prepayment', 'prepayment_sent'):
                conflict_result = await session.execute(
                    sa_select(Booking).where(
                        Booking.employee_id == booking.employee_id,
                        Booking.booking_date == booking.booking_date,
                        Booking.booking_time == booking.booking_time,
                        Booking.status.in_(['pending', 'confirmed']),
                        Booking.client_id != booking.client_id,
                    )
                )
                if conflict_result.scalars().first():
                    return web.json_response(
                        {'error': '⚠️ Это время уже занято другой подтверждённой записью. Отмените одну из них.'},
                        status=409
                    )

            # Сохраняем старый статус до обновления
            old_status = booking.status

            # Находим все записи в группе (тот же клиент, дата, время, бизнес)
            group_result = await session.execute(
                sa_select(Booking)
                .options(selectinload(Booking.service), selectinload(Booking.employee))
                .where(
                    Booking.client_id == booking.client_id,
                    Booking.business_id == booking.business_id,
                    Booking.booking_date == booking.booking_date,
                    Booking.booking_time == booking.booking_time,
                    Booking.status.notin_(['cancelled', 'completed']),
                )
            )
            group_bookings = group_result.scalars().all()

            # Собираем данные для уведомлений до коммита (после expire_on_commit не доступны)
            notify_client_id = booking.client.telegram_id if booking.client else None
            notify_business_name = booking.business.name if booking.business else ''
            notify_business_address = booking.business.address if booking.business else None
            notify_business_phone = booking.business.phone if booking.business else None
            notify_business_directions = booking.business.directions if booking.business else None
            notify_business_type = booking.business.business_type if booking.business else None
            notify_employee_name = booking.employee.name if booking.employee else None
            notify_date = booking.booking_date
            notify_time = booking.booking_time
            notify_prepayment = booking.prepayment_amount
            notify_services_list = []
            for b in group_bookings:
                if b.service:
                    notify_services_list.append({
                        'name': b.service.name,
                        'duration': b.service.duration_minutes,
                        'price': b.service.price or 0,
                        'is_additional': getattr(b.service, 'is_additional', False),
                        'employee_id': booking.employee_id,
                        'employee_name': booking.employee.name if booking.employee else None,
                        'employee_telegram_id': booking.employee.telegram_id if booking.employee else None,
                    })

            # Обновляем статус всех записей группы
            updated_ids = []
            for b in group_bookings:
                b.status = new_status
                updated_ids.append(b.id)
            await session.commit()

            logger.info(f"Updated group bookings {updated_ids} to status {new_status}")

            # Отправляем уведомления после коммита
            try:
                bot = Bot(token=BOT_TOKEN)
                from bot.utils.business_types import is_venue_rental_type
                from urllib.parse import quote as _quote

                if new_status == 'confirmed' and old_status in ('pending_prepayment', 'prepayment_sent'):
                    # Подтверждение предоплаты — уведомляем клиента, мастера, администратора
                    if notify_client_id:
                        is_venue = is_venue_rental_type(notify_business_type or '')
                        client_text = (
                            f"✅ <b>Ваше бронирование подтверждено!</b>\n\n"
                            f"🏢 {notify_business_name}\n"
                            f"📅 Дата: {notify_date.strftime('%d.%m.%Y')}\n"
                            f"🕐 Время: {notify_time.strftime('%H:%M')}\n"
                        )
                        if notify_employee_name:
                            label = "🏠 Помещение" if is_venue else "👤 Мастер"
                            client_text += f"{label}: {notify_employee_name}\n"
                        if notify_prepayment:
                            client_text += f"\n💰 Предоплата получена: {notify_prepayment}₽\n"
                        if notify_business_address:
                            enc = _quote(notify_business_address)
                            client_text += f'\n📍 <a href="https://yandex.ru/maps/?text={enc}">{notify_business_address}</a>\n'
                        if notify_business_phone:
                            client_text += f"📞 {notify_business_phone}\n"
                        if notify_business_directions:
                            client_text += f"\n🚶 {notify_business_directions}\n"
                        client_text += "\nЖдём вас! 🎉"
                        try:
                            await bot.send_message(
                                chat_id=notify_client_id,
                                text=client_text,
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                            )
                        except Exception as e:
                            logger.error(f"Error notifying client about confirmed booking: {e}")

                    # Уведомления мастеру и администраторам
                    # Перезагружаем booking с отношениями для notify-функций
                    async with async_session_maker() as notify_session:
                        fresh_booking = await get_booking_by_id(notify_session, booking_id)
                        if fresh_booking:
                            await notify_master_new_booking(bot, fresh_booking, notify_services_list)
                            await notify_administrator_new_booking(bot, fresh_booking, notify_services_list)

                elif new_status == 'cancelled' and notify_client_id:
                    # Отмена записи — уведомляем клиента
                    cancel_text = (
                        f"❌ <b>Ваша запись отменена</b>\n\n"
                        f"🏢 {notify_business_name}\n"
                        f"📅 {notify_date.strftime('%d.%m.%Y')} в {notify_time.strftime('%H:%M')}\n\n"
                        f"Если это ошибка, свяжитесь с администратором."
                    )
                    try:
                        await bot.send_message(
                            chat_id=notify_client_id,
                            text=cancel_text,
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.error(f"Error notifying client about cancelled booking: {e}")

            except Exception as notify_err:
                logger.error(f"Error sending notifications in status update: {notify_err}")

            return web.json_response({
                'success': True,
                'booking_id': booking_id,
                'updated_ids': updated_ids,
                'new_status': new_status,
                'version': getattr(booking, 'version', 1) + 1,
            })

    except Exception as e:
        logger.error(f"Error in api_update_booking_status: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_day_bookings(request: web.Request) -> web.Response:
    """Получить записи на конкретный день"""
    try:
        user, admin, business_id = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        if not admin:
            return web.json_response({'error': 'Access denied'}, status=403)

        date_str = request.match_info['date']
        target_date = date.fromisoformat(date_str)

        # Опциональный фильтр по мастеру
        employee_id = request.query.get('employee_id')
        if employee_id:
            employee_id = int(employee_id)

        async with async_session_maker() as session:
            bookings = await get_business_bookings(session, business_id, target_date, target_date)

            # Фильтруем по мастеру если нужно
            if employee_id:
                bookings = [b for b in bookings if b.employee_id == employee_id]

            result = []
            for booking in bookings:
                result.append({
                    'id': booking.id,
                    'time': booking.booking_time.strftime('%H:%M'),
                    'client_name': booking.client.full_name if booking.client else 'Без имени',
                    'client_phone': booking.client.phone if booking.client else None,
                    'service_name': booking.service.name if booking.service else 'Услуга',
                    'service_duration': booking.service.duration_minutes if booking.service else 0,
                    'service_price': booking.service.price if booking.service else 0,
                    'employee_name': booking.employee.name if booking.employee else 'Любой',
                    'employee_id': booking.employee_id,
                    'status': booking.status,
                    'version': getattr(booking, 'version', 1),  # Версия для оптимистичной блокировки
                })

            # Сортируем по времени
            result.sort(key=lambda x: x['time'])

            return web.json_response({
                'date': date_str,
                'bookings': result,
                'total': len(result),
            })

    except Exception as e:
        logger.error(f"Error in api_day_bookings: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def index_handler(request: web.Request) -> web.Response:
    """Публичная главная страница сервиса — рендерится Jinja2 чтобы
    подтянуть динамический список ниш для блока «выберите сегмент» и
    JSON-LD ItemList."""
    from webapp.niches import get_niche, get_offer, list_niches

    niches = []
    for slug in list_niches():
        n = get_niche(slug)
        if not n:
            continue
        label = (n.get('meta') or {}).get('short_label') or slug
        niches.append({
            'slug': slug,
            'label': label,
            'seats_left': n['offer']['seats_left'],
            'studio_free_left': n['offer']['studio_free_left'],
        })

    template = _niche_jinja_env.get_template('landing.html')
    html = template.render(BOT_USERNAME=BOT_USERNAME, niches=niches, offer=get_offer())
    return web.Response(text=html, content_type='text/html')



async def blog_sitemap_handler(request: web.Request) -> web.Response:
    """Dedicated sitemap, including when the blog is served on servgo.ru."""
    from xml.sax.saxutils import escape
    from webapp.blog_content import BLOG_ARTICLES, BLOG_PUBLIC_ORIGIN
    from webapp.blog_feed import all_articles

    base = escape(BLOG_PUBLIC_ORIGIN)
    rows = [f'<url><loc>{base}/blog</loc></url>']
    for article in await all_articles(BLOG_ARTICLES):
        rows.append(
            f'<url><loc>{base}/blog/{article["slug"]}</loc>'
            f'<lastmod>{article["updated"]}</lastmod></url>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + '\n'.join(rows)
        + '\n</urlset>\n'
    )
    return web.Response(text=xml, content_type='application/xml')


# Permanent redirects activate only after replacement articles appear in the
# public feed. Until then old pages continue to serve without broken links.
_SERVGO_OWNER_ARTICLE_REDIRECTS = {
    "zapis-k-psihologu": "kak-psihologu-organizovat-zapis-klientov",
    "zapisatsya-na-manikyur-onlayn": "kak-masteru-manikyura-vesti-zapis-klientov",
}


async def blog_handler(request: web.Request) -> web.Response:
    """Server-rendered, indexable editorial blog and individual articles."""
    from webapp.blog_content import (
        BLOG_ARTICLES, BLOG_CATEGORIES,
        BLOG_PUBLIC_ORIGIN, filter_blog_articles,
    )

    from webapp.blog_feed import all_articles

    # The blog lives on one host; the old promo address permanently forwards to it.
    request_host = request.host.split(":")[0]
    canonical_host = BLOG_PUBLIC_ORIGIN.split("://", 1)[-1]
    if request_host == "promo.servgo.ru" and canonical_host != request_host:
        raise web.HTTPMovedPermanently(BLOG_PUBLIC_ORIGIN + request.path_qs)

    everything = await all_articles(BLOG_ARTICLES)
    by_slug = {item["slug"]: item for item in everything}
    slug = request.match_info.get("article_slug")
    if slug in _SERVGO_OWNER_ARTICLE_REDIRECTS:
        new_slug = _SERVGO_OWNER_ARTICLE_REDIRECTS[slug]
        if new_slug in by_slug:
            raise web.HTTPMovedPermanently(f"{BLOG_PUBLIC_ORIGIN}/blog/{new_slug}")
    article = None
    query = ""
    topic = "all"
    if slug is not None:
        article = by_slug.get(slug)
        if article is None:
            raise web.HTTPNotFound()
        articles = []
        # Prefer the same topic while keeping related links useful.
        related = [
            item for item in everything
            if item["slug"] != slug and item["topic"] == article["topic"]
        ][:2]
        if len(related) < 2:
            related += [
                item for item in everything
                if item["slug"] != slug and item not in related
            ][:2 - len(related)]
        canonical = f"{BLOG_PUBLIC_ORIGIN}/blog/{article['slug']}"
    else:
        query = request.query.get("q", "").strip()[:120]
        requested_topic = request.query.get("topic", "")
        topic = requested_topic if requested_topic in dict(BLOG_CATEGORIES) else "all"
        articles = filter_blog_articles(topic, query, everything)
        related = []
        canonical = f"{BLOG_PUBLIC_ORIGIN}/blog"

    from webapp.seo_commercial import article_cta_url
    template = _niche_jinja_env.get_template("blog.html")
    html = template.render(
        origin=BLOG_PUBLIC_ORIGIN,
        canonical=canonical,
        commercial_cta_url=article_cta_url(article["slug"]) if article else None,
        article=article,
        articles=articles,
        related=related,
        categories=BLOG_CATEGORIES,
        selected_topic=topic,
        query=query,
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8")


_NICHE_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
_niche_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_NICHE_TEMPLATES_DIR),
    autoescape=jinja2.select_autoescape(['html']),
    trim_blocks=True,
    lstrip_blocks=True,
)


async def serve_roadmap(request: web.Request) -> web.Response:
    """Public roadmap: promo.servgo.ru/roadmap, data from webapp/roadmap.yaml."""
    from webapp.roadmap import load_roadmap

    template = _niche_jinja_env.get_template('roadmap.html')
    html = template.render(**load_roadmap())
    return web.Response(text=html, content_type='text/html', charset='utf-8')


async def serve_niche_landing(request: web.Request) -> web.Response:
    """Render promo.servgo.ru/<slug> from a YAML config in webapp/niches/."""
    from webapp.niches import get_niche, list_niches

    slug = request.match_info.get('slug', '')
    niche = get_niche(slug)
    if niche is None:
        raise web.HTTPNotFound()

    other_niches = []
    for other_slug in list_niches():
        if other_slug == slug:
            continue
        other = get_niche(other_slug)
        if not other:
            continue
        label = (other.get('meta') or {}).get('short_label') or other_slug
        other_niches.append({'slug': other_slug, 'label': label})

    template = _niche_jinja_env.get_template('niche.html')
    html = template.render(
        niche=niche,
        bot_username=BOT_USERNAME,
        other_niches=other_niches,
    )
    return web.Response(text=html, content_type='text/html')


async def sitemap_handler(request: web.Request) -> web.Response:
    """Dynamic /sitemap.xml — enumerates all niche landings."""
    from webapp.niches import list_niches
    from webapp.blog_content import BLOG_ARTICLES, BLOG_PUBLIC_ORIGIN
    from datetime import datetime

    today = datetime.utcnow().strftime('%Y-%m-%d')
    base = 'https://promo.servgo.ru'

    urls = [
        f'  <url><loc>{base}/</loc><lastmod>{today}</lastmod>'
        f'<changefreq>weekly</changefreq><priority>1.0</priority></url>'
    ]
    for slug in list_niches():
        urls.append(
            f'  <url><loc>{base}/{slug}</loc><lastmod>{today}</lastmod>'
            f'<changefreq>weekly</changefreq><priority>0.9</priority></url>'
        )
    urls.append(
        f'  <url><loc>{base}/roadmap</loc><lastmod>{today}</lastmod>'
        f'<changefreq>weekly</changefreq><priority>0.6</priority></url>'
    )

    # Sitemaps may list only canonical URLs belonging to this hostname.
    if BLOG_PUBLIC_ORIGIN == base:
        urls.append(
            f'  <url><loc>{base}/blog</loc>'
            '<changefreq>weekly</changefreq><priority>0.8</priority></url>'
        )
        for article in BLOG_ARTICLES:
            urls.append(
                f'  <url><loc>{base}/blog/{article["slug"]}</loc>'
                f'<lastmod>{article["updated"]}</lastmod>'
                '<changefreq>monthly</changefreq><priority>0.7</priority></url>'
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + '\n'.join(urls)
        + '\n</urlset>\n'
    )
    return web.Response(text=xml, content_type='application/xml')


async def robots_handler(request: web.Request) -> web.Response:
    """Static /robots.txt — allow everything except internal API/auth."""
    from webapp.blog_content import BLOG_PUBLIC_ORIGIN
    text = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /auth/\n"
        "Disallow: /blog/cta/\n"
        "\n"
        "Sitemap: https://promo.servgo.ru/sitemap.xml\n"
        f"Sitemap: {BLOG_PUBLIC_ORIGIN}/blog/sitemap.xml\n"
    )
    return web.Response(text=text, content_type='text/plain')


async def llms_txt_handler(request: web.Request) -> web.Response:
    """/llms.txt — a short, honest map of the blog for AI assistants and search agents."""
    from webapp.blog_content import BLOG_ARTICLES, BLOG_PUBLIC_ORIGIN
    from webapp.blog_feed import all_articles

    lines = [
        "# ServGo",
        "",
        "> ServGo — платформа для малого бизнеса: ИИ-маркетинг и онлайн-запись клиентов.",
        "",
        "## Блог",
        "",
    ]
    for item in await all_articles(BLOG_ARTICLES):
        summary = (item.get("direct_answer") or item["description"]).replace("\n", " ")[:200]
        lines.append(f"- [{item['title']}]({BLOG_PUBLIC_ORIGIN}/blog/{item['slug']}): {summary}")
    lines += ["", f"Карта сайта: {BLOG_PUBLIC_ORIGIN}/blog/sitemap.xml", ""]
    return web.Response(text="\n".join(lines), content_type="text/plain", charset="utf-8")


async def calendar_handler(request: web.Request) -> web.Response:
    """Страница календаря записей"""
    import os
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'calendar.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()
    return web.Response(text=html, content_type='text/html')


async def analytics_handler(request: web.Request) -> web.Response:
    """Страница аналитики"""
    import os
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'analytics.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()
    return web.Response(text=html, content_type='text/html')


async def login_handler(request: web.Request) -> web.Response:
    """Страница входа для владельцев"""
    import os
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'login.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()
    return web.Response(text=html, content_type='text/html')


async def auth_token_handler(request: web.Request) -> web.Response:
    """Вход по прямой ссылке с токеном"""
    token = request.match_info['token']
    redirect_to = request.query.get('to', '/schedule')

    # Ищем владельца по токену
    async with async_session_maker() as session:
        from sqlalchemy import select
        from database.models import User as UserModel, Business, BusinessOwner
        
        # Ищем всех пользователей (и первичных владельцев, и совладельцев)
        result = await session.execute(select(UserModel))
        all_users = result.scalars().all()

        for user in all_users:
            if generate_owner_token(user.telegram_id) == token:
                # Проверяем, является ли пользователь владельцем какого-то бизнеса
                # Проверяем как главного владельца
                biz_result = await session.execute(
                    select(Business).where(Business.owner_id == user.id)
                )
                if biz_result.scalar_one_or_none():
                    # Нашли - это главный владелец бизнеса
                    cookie = create_session_cookie(user.telegram_id)
                    response = web.HTTPFound(redirect_to)
                    response.set_cookie('owner_session', cookie, max_age=30*24*3600, httponly=True, samesite='Lax')
                    return response
                
                # Проверяем как совладельца
                co_owner_result = await session.execute(
                    select(BusinessOwner).where(BusinessOwner.user_id == user.id)
                )
                if co_owner_result.scalar_one_or_none():
                    # Нашли - это совладелец бизнеса
                    cookie = create_session_cookie(user.telegram_id)
                    response = web.HTTPFound(redirect_to)
                    response.set_cookie('owner_session', cookie, max_age=30*24*3600, httponly=True, samesite='Lax')
                    return response

    # Токен не найден
    return web.HTTPFound('/login?error=invalid_token')


async def api_auth_login(request: web.Request) -> web.Response:
    """API логин по телефону + PIN"""
    try:
        data = await request.json()
        phone = data.get('phone', '').strip()
        pin = data.get('pin', '').strip()

        if not phone or not pin:
            return web.json_response({'error': 'Введите телефон и PIN-код'}, status=400)

        # Нормализуем телефон - оставляем только цифры
        phone_digits = ''.join(c for c in phone if c.isdigit())
        if phone_digits.startswith('8') and len(phone_digits) == 11:
            phone_digits = '7' + phone_digits[1:]

        async with async_session_maker() as session:
            from sqlalchemy import select
            from database.models import User as UserModel, Business, BusinessOwner
            # Ищем пользователя по телефону
            result = await session.execute(
                select(UserModel).where(UserModel.phone.isnot(None))
            )
            users = result.scalars().all()

            for user in users:
                user_phone_digits = ''.join(c for c in (user.phone or '') if c.isdigit())
                if user_phone_digits.startswith('8') and len(user_phone_digits) == 11:
                    user_phone_digits = '7' + user_phone_digits[1:]

                if user_phone_digits == phone_digits and user_phone_digits:
                    # Проверяем PIN
                    expected_pin = generate_owner_pin(user.telegram_id)
                    if pin == expected_pin:
                        # Проверяем, что это владелец бизнеса (первичный или совладелец)
                        # Ищем как первичного владельца
                        biz_result = await session.execute(
                            select(Business).where(Business.owner_id == user.id)
                        )
                        business = biz_result.scalar_one_or_none()
                        if business:
                            cookie = create_session_cookie(user.telegram_id)
                            response = web.json_response({
                                'success': True,
                                'owner_name': user.full_name,
                                'business_name': business.name
                            })
                            response.set_cookie('owner_session', cookie, max_age=30*24*3600, httponly=True, samesite='Lax')
                            return response
                        
                        # Ищем как совладельца
                        co_owner_result = await session.execute(
                            select(BusinessOwner).where(BusinessOwner.user_id == user.id)
                        )
                        co_owner = co_owner_result.scalar_one_or_none()
                        if co_owner:
                            # Получаем информацию о бизнесе для ответа
                            co_business = await session.execute(
                                select(Business).where(Business.id == co_owner.business_id)
                            )
                            business = co_business.scalar_one_or_none()
                            if business:
                                cookie = create_session_cookie(user.telegram_id)
                                response = web.json_response({
                                    'success': True,
                                    'owner_name': user.full_name,
                                    'business_name': business.name
                                })
                                response.set_cookie('owner_session', cookie, max_age=30*24*3600, httponly=True, samesite='Lax')
                                return response

            return web.json_response({'error': 'Неверный телефон или PIN-код'}, status=401)

    except Exception as e:
        logger.error(f"Error in api_auth_login: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_auth_telegram(request: web.Request) -> web.Response:
    """Authenticate browser users via Telegram Login Widget."""
    try:
        payload = await request.json()
        auth_dict = auth_dict_from_payload(payload)

        if not verify_telegram_login(auth_dict, [BOT_TOKEN]):
            return web.json_response({'error': 'Invalid auth data'}, status=401)

        shared_user = shared_create_or_update_user(auth_dict)
        token = shared_create_session(int(auth_dict['id']), source_service='booking')

        response = web.json_response({
            'success': True,
            'user': {
                'telegram_id': shared_user['telegram_id'],
                'first_name': shared_user.get('first_name', ''),
                'last_name': shared_user.get('last_name', ''),
                'username': shared_user.get('username', ''),
                'photo_url': shared_user.get('photo_url', ''),
            },
        })
        response.set_cookie(AUTH_COOKIE_NAME, token, **auth_cookie_kwargs())
        return response
    except Exception as e:
        logger.error(f"Error in api_auth_telegram: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_auth_me(request: web.Request) -> web.Response:
    """Return current shared web user."""
    user = await get_authenticated_telegram_user(request)
    if not user:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    return web.json_response({
        'user': {
            'telegram_id': user['id'],
            'first_name': user.get('first_name', ''),
            'last_name': user.get('last_name', ''),
            'username': user.get('username', ''),
            'photo_url': user.get('photo_url', ''),
        },
    })


async def api_auth_logout(request: web.Request) -> web.Response:
    """Выход из веб-сессии"""
    shared_token = request.cookies.get(AUTH_COOKIE_NAME)
    if shared_token:
        shared_expire_session(shared_token)

    response = web.json_response({'success': True})
    response.del_cookie('owner_session')
    response.del_cookie(AUTH_COOKIE_NAME, **clear_cookie_kwargs())
    return response


async def api_auth_check(request: web.Request) -> web.Response:
    """Проверка текущей сессии"""
    session_cookie = request.cookies.get('owner_session')
    if session_cookie:
        telegram_id = verify_session_cookie(session_cookie)
        if telegram_id:
            admin, business_id = await check_admin_access(telegram_id)
            if admin:
                return web.json_response({'authenticated': True, 'telegram_id': telegram_id, 'kind': 'owner'})
    shared = shared_get_session(request.cookies.get(AUTH_COOKIE_NAME))
    if shared:
        return web.json_response({
            'authenticated': True,
            'telegram_id': shared['telegram_id'],
            'kind': 'shared',
        })
    return web.json_response({'authenticated': False}, status=401)


async def api_analytics_businesses(request: web.Request) -> web.Response:
    """Получить список бизнесов пользователя для аналитики"""
    try:
        user, admin, business_id = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        from database.models import Employee as EmployeeModel
        telegram_id = user.get('id')

        async with async_session_maker() as session:
            if isinstance(admin, EmployeeModel):
                # Администратор бизнеса — только бизнесы, в которых назначен
                admin_employees = await get_administrators_by_telegram_id(session, telegram_id)
                businesses_list = []
                seen_ids = set()
                for ae in admin_employees:
                    if ae.business_id not in seen_ids:
                        b = await get_business_by_id(session, ae.business_id)
                        if b:
                            businesses_list.append(b)
                            seen_ids.add(ae.business_id)
                return web.json_response({
                    'businesses': [
                        {'id': b.id, 'name': b.name, 'type': b.business_type}
                        for b in businesses_list
                    ],
                    'is_owner': False,
                })
            else:
                # Владелец — все его бизнесы
                db_user = await get_user_by_telegram_id(session, telegram_id)
                if db_user:
                    businesses = await get_user_businesses(session, db_user.id)
                    return web.json_response({
                        'businesses': [
                            {'id': b.id, 'name': b.name, 'type': b.business_type}
                            for b in businesses
                        ],
                        'is_owner': True,
                    })

        return web.json_response({'businesses': [], 'is_owner': False})

    except Exception as e:
        logger.error(f"Error in api_analytics_businesses: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_analytics_data(request: web.Request) -> web.Response:
    """Получить данные аналитики для бизнеса"""
    try:
        user, admin, _ = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        business_id = request.query.get('business_id')
        period = request.query.get('period', 'today')

        if not business_id:
            return web.json_response({'error': 'business_id required'}, status=400)

        business_id = int(business_id)

        # Определяем диапазон дат
        today = date.today()
        if period == 'today':
            start_date = today
            end_date = today
            prev_start = today - timedelta(days=1)
            prev_end = today - timedelta(days=1)
        elif period == 'week':
            start_date = today - timedelta(days=7)
            end_date = today
            prev_start = today - timedelta(days=14)
            prev_end = today - timedelta(days=8)
        elif period == 'month':
            start_date = today - timedelta(days=30)
            end_date = today
            prev_start = today - timedelta(days=60)
            prev_end = today - timedelta(days=31)
        elif period == 'year':
            start_date = today - timedelta(days=365)
            end_date = today
            prev_start = today - timedelta(days=730)
            prev_end = today - timedelta(days=366)
        else:
            start_date = today
            end_date = today
            prev_start = today - timedelta(days=1)
            prev_end = today - timedelta(days=1)

        async with async_session_maker() as session:
            # Получаем записи за текущий и предыдущий период
            bookings = await get_business_bookings(session, business_id, start_date, end_date)
            prev_bookings = await get_business_bookings(session, business_id, prev_start, prev_end)

            # Получаем сотрудников
            employees = await get_business_employees(session, business_id)
            masters = [e for e in employees if e.role in ('master', 'master_and_admin') and e.is_active]

            # Получаем услуги
            services = await get_business_services(session, business_id)

            # Расчет основных метрик
            completed_bookings = [b for b in bookings if b.status == 'completed']
            cancelled_bookings = [b for b in bookings if b.status == 'cancelled']

            total_revenue = sum(b.service.price or 0 for b in completed_bookings if b.service)
            prev_revenue = sum(b.service.price or 0 for b in prev_bookings if b.status == 'completed' and b.service)

            unique_clients = len(set(b.client_id for b in bookings))
            prev_unique_clients = len(set(b.client_id for b in prev_bookings))

            completion_rate = round(len(completed_bookings) / len(bookings) * 100) if bookings else 0
            cancellation_rate = round(len(cancelled_bookings) / len(bookings) * 100) if bookings else 0

            # Изменения по сравнению с предыдущим периодом
            revenue_change = calculate_change(total_revenue, prev_revenue)
            bookings_change = calculate_change(len(bookings), len(prev_bookings))
            clients_change = calculate_change(unique_clients, prev_unique_clients)

            # Выручка по датам
            revenue_by_date = calculate_revenue_by_date(bookings, start_date, end_date, period)

            # Записи по статусам
            bookings_by_status = {
                'completed': len([b for b in bookings if b.status == 'completed']),
                'confirmed': len([b for b in bookings if b.status == 'confirmed']),
                'pending': len([b for b in bookings if b.status == 'pending']),
                'cancelled': len([b for b in bookings if b.status == 'cancelled']),
            }

            # Записи по часам
            bookings_by_hour = calculate_bookings_by_hour(bookings)

            # Записи по дням недели
            bookings_by_day = calculate_bookings_by_day(bookings)

            # Топ услуг
            top_services = calculate_top_services(bookings, services)

            # Статистика мастеров
            masters_stats = calculate_masters_stats(bookings, masters)

            # Анализ клиентов
            clients_analysis = await calculate_clients_analysis(session, bookings, business_id, start_date)

            # Статистика абонементов
            subscriptions_stats = await get_business_subscriptions_stats(session, business_id, start_date, end_date)

            # Статистика предоплат
            prepayments_stats = calculate_prepayments_stats(bookings)

            return web.json_response({
                'summary': {
                    'revenue': total_revenue,
                    'revenue_change': revenue_change,
                    'bookings_count': len(bookings),
                    'bookings_change': bookings_change,
                    'unique_clients': unique_clients,
                    'clients_change': clients_change,
                    'completion_rate': completion_rate,
                    'cancellation_rate': cancellation_rate,
                },
                'revenue_by_date': revenue_by_date,
                'bookings_by_status': bookings_by_status,
                'bookings_by_hour': bookings_by_hour,
                'bookings_by_day': bookings_by_day,
                'top_services': top_services,
                'masters_stats': masters_stats,
                'clients': clients_analysis,
                'subscriptions': subscriptions_stats,
                'prepayments': prepayments_stats,
            })

    except Exception as e:
        logger.error(f"Error in api_analytics_data: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


def calculate_change(current: int, previous: int) -> int:
    """Вычислить процентное изменение"""
    if previous == 0:
        return 100 if current > 0 else 0
    return round((current - previous) / previous * 100)


def calculate_revenue_by_date(bookings, start_date: date, end_date: date, period: str) -> list:
    """Рассчитать выручку по датам"""
    completed = [b for b in bookings if b.status == 'completed']

    # Группируем в зависимости от периода
    if period == 'today':
        # По часам
        revenue_map = {}
        for b in completed:
            hour = b.booking_time.hour
            revenue_map[hour] = revenue_map.get(hour, 0) + (b.service.price or 0 if b.service else 0)

        return [
            {'label': f'{h}:00', 'value': revenue_map.get(h, 0)}
            for h in range(8, 22)
        ]
    elif period == 'week':
        # По дням
        revenue_map = {}
        for b in completed:
            day_key = b.booking_date.strftime('%d.%m')
            revenue_map[day_key] = revenue_map.get(day_key, 0) + (b.service.price or 0 if b.service else 0)

        result = []
        current = start_date
        while current <= end_date:
            day_key = current.strftime('%d.%m')
            result.append({'label': day_key, 'value': revenue_map.get(day_key, 0)})
            current += timedelta(days=1)
        return result
    elif period == 'month':
        # По неделям
        revenue_map = {}
        for b in completed:
            week_num = b.booking_date.isocalendar()[1]
            revenue_map[week_num] = revenue_map.get(week_num, 0) + (b.service.price or 0 if b.service else 0)

        weeks = sorted(revenue_map.keys())
        return [
            {'label': f'Нед {w}', 'value': revenue_map[w]}
            for w in weeks
        ] if weeks else [{'label': 'Нет данных', 'value': 0}]
    else:
        # По месяцам
        revenue_map = {}
        month_names = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
        for b in completed:
            month_key = b.booking_date.month
            revenue_map[month_key] = revenue_map.get(month_key, 0) + (b.service.price or 0 if b.service else 0)

        return [
            {'label': month_names[m - 1], 'value': revenue_map.get(m, 0)}
            for m in range(1, 13)
        ]


def calculate_bookings_by_hour(bookings) -> list:
    """Распределение записей по часам"""
    hour_map = {}
    for b in bookings:
        hour = b.booking_time.hour
        hour_map[hour] = hour_map.get(hour, 0) + 1

    return [
        {'hour': h, 'count': hour_map.get(h, 0)}
        for h in range(8, 22)
    ]


def calculate_bookings_by_day(bookings) -> list:
    """Распределение записей по дням недели"""
    day_map = {i: 0 for i in range(7)}
    for b in bookings:
        day = b.booking_date.weekday()
        day_map[day] = day_map.get(day, 0) + 1

    return [
        {'day': d, 'count': day_map[d]}
        for d in range(7)
    ]


def calculate_top_services(bookings, services) -> list:
    """Топ услуг по популярности"""
    service_stats = {}
    for b in bookings:
        if b.service_id:
            if b.service_id not in service_stats:
                service_stats[b.service_id] = {
                    'name': b.service.name if b.service else 'Неизвестно',
                    'count': 0,
                    'revenue': 0
                }
            service_stats[b.service_id]['count'] += 1
            if b.status == 'completed' and b.service:
                service_stats[b.service_id]['revenue'] += b.service.price or 0

    sorted_services = sorted(service_stats.values(), key=lambda x: x['count'], reverse=True)
    return sorted_services[:10]


def calculate_masters_stats(bookings, masters) -> list:
    """Статистика по мастерам"""
    master_stats = {}
    for m in masters:
        master_stats[m.id] = {
            'name': m.name,
            'specialization': m.specialization,
            'bookings_count': 0,
            'revenue': 0
        }

    for b in bookings:
        if b.employee_id and b.employee_id in master_stats:
            master_stats[b.employee_id]['bookings_count'] += 1
            if b.status == 'completed' and b.service:
                master_stats[b.employee_id]['revenue'] += b.service.price or 0

    sorted_masters = sorted(master_stats.values(), key=lambda x: x['bookings_count'], reverse=True)
    return sorted_masters


async def calculate_clients_analysis(session, bookings, business_id: int, start_date: date) -> dict:
    """Анализ клиентов"""
    from sqlalchemy import select, func
    from database.models import Booking

    if not bookings:
        return {
            'new_clients': 0,
            'returning_clients': 0,
            'avg_check': 0,
            'bookings_per_client': 0
        }

    client_ids = set(b.client_id for b in bookings)

    # Новые клиенты - те, у кого нет записей до start_date
    new_clients = 0
    for client_id in client_ids:
        stmt = select(func.count()).select_from(Booking).where(
            Booking.business_id == business_id,
            Booking.client_id == client_id,
            Booking.booking_date < start_date
        )
        result = await session.execute(stmt)
        prev_count = result.scalar()
        if prev_count == 0:
            new_clients += 1

    returning_clients = len(client_ids) - new_clients

    completed = [b for b in bookings if b.status == 'completed']
    total_revenue = sum(b.service.price or 0 for b in completed if b.service)
    avg_check = round(total_revenue / len(completed)) if completed else 0

    bookings_per_client = round(len(bookings) / len(client_ids), 1) if client_ids else 0

    return {
        'new_clients': new_clients,
        'returning_clients': returning_clients,
        'avg_check': avg_check,
        'bookings_per_client': bookings_per_client
    }


def calculate_prepayments_stats(bookings) -> dict:
    """Статистика предоплат"""
    prepayment_bookings = [b for b in bookings if b.prepayment_amount and b.prepayment_amount > 0]
    total_amount = sum(b.prepayment_amount or 0 for b in prepayment_bookings)
    confirmed = [b for b in prepayment_bookings if b.status in ('confirmed', 'completed', 'prepayment_confirmed')]
    confirmed_amount = sum(b.prepayment_amount or 0 for b in confirmed)

    return {
        'bookings_count': len(prepayment_bookings),
        'total_amount': total_amount,
        'confirmed_amount': confirmed_amount
    }


# === Client Booking API ===

# Клиентская запись живёт только в веб-приложении ServGo (servgo.ru): одна
# страница, один расчёт цены и предоплаты, одни правила записи.
CLIENT_APP_URL = (os.getenv('CLIENT_APP_URL') or 'https://servgo.ru').rstrip('/')


def client_booking_url(query_string: str = '') -> str:
    return f"{CLIENT_APP_URL}/book" + (f"?{query_string}" if query_string else '')


async def booking_handler(request: web.Request) -> web.Response:
    """Старая страница записи → новая запись на servgo.ru (параметры сохраняются)."""
    raise web.HTTPFound(client_booking_url(request.query_string))


async def api_get_photo(request: web.Request) -> web.Response:
    """Получить URL фото по file_id из Telegram"""
    try:
        file_id = request.query.get('file_id')
        if not file_id:
            return web.json_response({'error': 'file_id required'}, status=400)

        # Получаем file_path через Telegram Bot API
        import aiohttp
        async with aiohttp.ClientSession() as http_session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
            async with http_session.get(url) as resp:
                data = await resp.json()

                if data.get('ok') and data.get('result', {}).get('file_path'):
                    file_path = data['result']['file_path']
                    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                    return web.json_response({'url': photo_url})

        return web.json_response({'error': 'Photo not found'}, status=404)

    except Exception as e:
        logger.error(f"Error in api_get_photo: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_media_proxy(request: web.Request) -> web.Response:
    """Проксирует медиа-файлы из Telegram с кешированием на диске"""
    import os
    import aiohttp

    file_id = request.match_info.get('file_id', '')
    if not file_id:
        return web.Response(status=400, text='file_id required')

    # Кеш-директория
    cache_dir = os.path.join(os.path.dirname(__file__), 'static', 'media_cache')
    os.makedirs(cache_dir, exist_ok=True)

    # Хеш file_id для имени файла
    file_hash = hashlib.md5(file_id.encode()).hexdigest()

    # Проверяем кеш (ищем файл с любым расширением)
    for ext in ('.jpg', '.mp4', '.webm', '.mpeg', '.png', '.gif'):
        cached_path = os.path.join(cache_dir, file_hash + ext)
        if os.path.exists(cached_path):
            content_types = {
                '.jpg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
                '.mp4': 'video/mp4', '.webm': 'video/webm', '.mpeg': 'video/mpeg',
            }
            return web.FileResponse(cached_path, headers={
                'Content-Type': content_types.get(ext, 'application/octet-stream'),
                'Cache-Control': 'public, max-age=86400',
            })

    # Скачиваем из Telegram
    try:
        async with aiohttp.ClientSession() as http_session:
            # Получаем file_path
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
            async with http_session.get(url) as resp:
                data = await resp.json()

            if not data.get('ok') or not data.get('result', {}).get('file_path'):
                return web.Response(status=404, text='File not found in Telegram')

            file_path = data['result']['file_path']
            file_size = data['result'].get('file_size', 0)

            # Ограничение 50MB
            if file_size > 50 * 1024 * 1024:
                return web.Response(status=413, text='File too large')

            # Определяем расширение
            ext = os.path.splitext(file_path)[1].lower() or '.bin'
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm', '.mpeg'):
                ext = '.mp4' if 'video' in file_path else '.jpg'
            if ext == '.jpeg':
                ext = '.jpg'

            # Скачиваем файл
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            async with http_session.get(download_url) as resp:
                if resp.status != 200:
                    return web.Response(status=502, text='Failed to download from Telegram')
                file_data = await resp.read()

            # Сохраняем в кеш
            cached_path = os.path.join(cache_dir, file_hash + ext)
            with open(cached_path, 'wb') as f:
                f.write(file_data)

            content_types = {
                '.jpg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
                '.mp4': 'video/mp4', '.webm': 'video/webm', '.mpeg': 'video/mpeg',
            }
            return web.Response(
                body=file_data,
                content_type=content_types.get(ext, 'application/octet-stream'),
                headers={'Cache-Control': 'public, max-age=86400'},
            )

    except Exception as e:
        logger.error(f"Error in api_media_proxy: {e}")
        return web.Response(status=500, text='Internal error')


BUSINESS_TYPE_NAMES = {
    'beauty_salon': 'Салон красоты',
    'barbershop': 'Барбершоп',
    'spa': 'СПА-салон',
    'nail_studio': 'Ногтевая студия',
    'massage': 'Массажный кабинет',
    'auto_service': 'Автосервис',
    'car_wash': 'Автомойка',
    'tire_service': 'Шиномонтаж',
    'medical_clinic': 'Медицинская клиника',
    'dental_clinic': 'Стоматология',
    'fitness': 'Фитнес-клуб',
    'yoga': 'Йога-студия',
    'education': 'Образовательный центр',
    'photo_studio': 'Фотостудия',
    'coworking': 'Коворкинг',
    'consultation': 'Консультации',
    'other': 'Другое',
    'venue_rental': 'Аренда помещений',
}


async def api_client_businesses(request: web.Request) -> web.Response:
    """Получить список всех активных бизнесов для клиентов"""
    try:
        async with async_session_maker() as session:
            businesses = await get_all_active_businesses(session)

            result = []
            for b in businesses:
                # Получаем количество услуг и сотрудников
                services = await get_business_services(session, b.id)
                employees = await get_business_employees(session, b.id)
                active_employees = [e for e in employees if e.is_active and e.role in ('master', 'master_and_admin')]

                # Форматируем время работы
                start = b.working_hours_start.strftime('%H:%M') if b.working_hours_start else '09:00'
                end = b.working_hours_end.strftime('%H:%M') if b.working_hours_end else '18:00'

                # Форматируем перерыв
                break_time = None
                if b.break_start and b.break_end:
                    break_time = f"{b.break_start.strftime('%H:%M')} - {b.break_end.strftime('%H:%M')}"

                result.append({
                    'id': b.id,
                    'name': b.name,
                    'description': b.description,
                    'address': b.address,
                    'phone': b.phone,
                    'type': b.business_type,  # Для совместимости с JS
                    'business_type': b.business_type,
                    'type_name': BUSINESS_TYPE_NAMES.get(b.business_type, 'Другое'),
                    'photos': b.photos,
                    'services_count': len(services),
                    'employees_count': len(active_employees),
                    'working_hours': f'{start} - {end}',
                    'break_time': break_time,
                })

            return web.json_response({'businesses': result})

    except Exception as e:
        logger.error(f"Error in api_client_businesses: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_business_details(request: web.Request) -> web.Response:
    """Получить детали бизнеса с услугами и сотрудниками"""
    try:
        business_id = int(request.match_info['business_id'])

        async with async_session_maker() as session:
            business = await get_business_by_id(session, business_id)
            if not business:
                return web.json_response({'error': 'Business not found'}, status=404)

            # Получаем услуги (основные и доп. отдельно)
            services = await get_business_services(session, business_id)
            import json as _json
            services_list = [
                {
                    'id': s.id,
                    'name': s.name,
                    'description': s.description,
                    'price': s.price,
                    'duration': s.duration_minutes,
                    'available_employee_ids': _json.loads(s.available_employee_ids) if s.available_employee_ids else None,
                    'tariff_description': s.tariff_description if hasattr(s, 'tariff_description') else None,
                }
                for s in services if s.is_active and not s.is_additional
            ]
            additional_services_list = [
                {
                    'id': s.id,
                    'name': s.name,
                    'description': s.description,
                    'price': s.price,
                    'duration': s.duration_minutes,
                    'is_hourly_price': getattr(s, 'is_hourly_price', False),
                }
                for s in services if s.is_active and s.is_additional
            ]

            # Получаем сотрудников (мастеров)
            employees = await get_business_employees(session, business_id)

            # Сортируем помещения venue_rental как в боте
            if business.business_type == 'venue_rental':
                def _room_sort_key(e):
                    n = e.name.lower()
                    if 'красн' in n: return 0
                    if 'сер' in n: return 1
                    if 'чёрн' in n or 'черн' in n: return 2
                    if 'бел' in n: return 3
                    if 'бежев' in n: return 4
                    return 5
                employees = sorted(employees, key=lambda e: _room_sort_key(e))

            employees_list = []
            has_time_pricing = False
            for e in employees:
                if not e.is_active or e.role not in ('master', 'master_and_admin'):
                    continue
                time_pricing = await get_employee_time_pricing(session, e.id)
                emp_has_tp = len(time_pricing) > 0
                if emp_has_tp:
                    has_time_pricing = True
                emp_photos = None
                if e.photos:
                    try:
                        emp_photos = _json.loads(e.photos)
                    except Exception:
                        pass
                employees_list.append({
                    'id': e.id,
                    'name': e.name,
                    'specialization': e.specialization,
                    'has_time_pricing': emp_has_tp,
                    'capacity': e.capacity,
                    'area_sqm': e.area_sqm,
                    'description': e.description,
                    'min_booking_minutes': e.min_booking_minutes,
                    'solo_price_per_hour': e.solo_price_per_hour,
                    'photo_ids': emp_photos or [],
                    'video_id': e.video,
                })

            return web.json_response({
                'business': {
                    'id': business.id,
                    'name': business.name,
                    'description': business.description,
                    'address': business.address,
                    'phone': business.phone,
                    'business_type': business.business_type,
                    'photos': business.photos,
                    'working_hours': {
                        'start': business.working_hours_start.strftime('%H:%M') if business.working_hours_start else '09:00',
                        'end': business.working_hours_end.strftime('%H:%M') if business.working_hours_end else '18:00',
                    },
                    'break_time': {
                        'start': business.break_start.strftime('%H:%M') if business.break_start else None,
                        'end': business.break_end.strftime('%H:%M') if business.break_end else None,
                    } if business.break_start else None,
                    'booking_interval': business.booking_interval_minutes or 30,
                    'owner_username': business.owner.username if business.owner else None,
                    'video_id': getattr(business, 'video', None),
                },
                'services': services_list,
                'additional_services': additional_services_list,
                'employees': employees_list,
                'has_time_pricing': has_time_pricing,
            })

    except Exception as e:
        logger.error(f"Error in api_client_business_details: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_last_bookings(request: web.Request) -> web.Response:
    """Получить последние записи пользователя для повторного бронирования"""
    try:
        # Получаем данные пользователя из Telegram WebApp
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'bookings': []})

        telegram_id = user.get('id')

        async with async_session_maker() as session:
            # Получаем пользователя
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'bookings': []})

            # Получаем последние записи (уникальные комбинации бизнес+услуга)
            from sqlalchemy import select, desc, and_, distinct
            from database.models import Booking

            result = await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.business),
                    selectinload(Booking.service),
                    selectinload(Booking.employee)
                )
                .where(
                    and_(
                        Booking.client_id == db_user.id,
                        Booking.status.in_(['confirmed', 'completed', 'pending'])
                    )
                )
                .order_by(desc(Booking.created_at))
                .limit(20)  # Берем больше, потом фильтруем уникальные
            )
            bookings = result.scalars().all()

            # Собираем уникальные комбинации бизнес+услуга
            seen = set()
            unique_bookings = []

            for booking in bookings:
                key = (booking.business_id, booking.service_id)
                if key in seen:
                    continue
                seen.add(key)

                # Проверяем, что бизнес и услуга еще активны
                business = await get_business_by_id(session, booking.business_id)
                if not business or not business.is_active:
                    continue

                service = await get_service_by_id(session, booking.service_id)
                if not service or not service.is_active:
                    continue

                unique_bookings.append({
                    'business_id': booking.business_id,
                    'business_name': business.name,
                    'service_id': booking.service_id,
                    'service_name': service.name,
                    'employee_id': booking.employee_id,
                    'employee_name': booking.employee.name if booking.employee else None,
                })

                if len(unique_bookings) >= 5:  # Максимум 5 вариантов
                    break

            return web.json_response({'bookings': unique_bookings})

    except Exception as e:
        logger.error(f"Error in api_client_last_bookings: {e}")
        return web.json_response({'bookings': []})


async def api_client_slots(request: web.Request) -> web.Response:
    """Получить доступные слоты для записи"""
    try:
        business_id = int(request.match_info['business_id'])
        date_str = request.query.get('date')
        employee_id = request.query.get('employee_id')
        # Поддерживаем оба параметра: services и service_ids
        service_ids = request.query.get('services', '') or request.query.get('service_ids', '')
        service_ids = service_ids.split(',') if service_ids else []
        # Параметр duration для переноса бронирований (когда у услуги duration=0)
        override_duration = request.query.get('duration')

        if not date_str:
            return web.json_response({'error': 'date is required'}, status=400)

        if not service_ids or service_ids == ['']:
            return web.json_response({'error': 'services is required'}, status=400)

        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        service_ids = [int(sid) for sid in service_ids if sid]

        if employee_id and employee_id != 'any':
            employee_id = int(employee_id)
        else:
            employee_id = None

        async with async_session_maker() as session:
            business = await get_business_by_id(session, business_id)
            if not business:
                return web.json_response({'error': 'Business not found'}, status=404)

            # Получаем услуги и вычисляем общую длительность
            total_duration = 0
            for sid in service_ids:
                service = await get_service_by_id(session, sid)
                if service:
                    total_duration += service.duration_minutes

            # Если duration=0 (например, услуги уборки), используем переданный duration
            if total_duration == 0 and override_duration:
                total_duration = int(override_duration)

            if total_duration == 0:
                # Для услуг без фиксированной длительности используем 60 минут по умолчанию
                total_duration = 60

            # Определяем интервал и время работы
            interval = business.booking_interval_minutes or 30
            start_time = business.working_hours_start if business.working_hours_start else datetime.strptime("09:00", "%H:%M").time()
            end_time = business.working_hours_end if business.working_hours_end else datetime.strptime("18:00", "%H:%M").time()

            # Получаем существующие записи
            from sqlalchemy import select, and_, or_
            from database.models import Booking, Employee

            # Cross-midnight detection
            is_cross_midnight = end_time < start_time
            prev_day = selected_date - timedelta(days=1)
            next_day = selected_date + timedelta(days=1)

            # Проверяем индивидуальный интервал и мин. время для конкретного сотрудника
            if employee_id:
                emp_result = await session.execute(
                    select(Employee).where(Employee.id == employee_id)
                )
                emp_obj = emp_result.scalar_one_or_none()
                if emp_obj:
                    if emp_obj.booking_interval_minutes:
                        interval = emp_obj.booking_interval_minutes
                    if emp_obj.min_booking_minutes and total_duration < emp_obj.min_booking_minutes:
                        return web.json_response({
                            'slots': [],
                            'message': f'Минимальное время бронирования: {emp_obj.min_booking_minutes} мин',
                            'min_booking_minutes': emp_obj.min_booking_minutes
                        })

                # Проверяем выходной сотрудника
                is_day_off = await is_employee_day_off(session, employee_id, selected_date)
                if is_day_off:
                    return web.json_response({'slots': [], 'message': 'Employee is off this day'})

                date_filter = Booking.booking_date.in_([selected_date, prev_day]) if is_cross_midnight else Booking.booking_date == selected_date
                result = await session.execute(
                    select(Booking)
                    .options(selectinload(Booking.service))
                    .where(
                        and_(
                            Booking.business_id == business_id,
                            Booking.employee_id == employee_id,
                            date_filter,
                            Booking.status.in_(["pending", "confirmed"])
                        )
                    )
                )
                existing_bookings = result.scalars().all()
                available_employees = None
            else:
                # "Любой мастер" - получаем всех мастеров
                result = await session.execute(
                    select(Employee).where(
                        and_(
                            Employee.business_id == business_id,
                            Employee.is_active == True,
                            Employee.role.in_(['master', 'master_and_admin'])
                        )
                    )
                )
                available_employees = result.scalars().all()

                date_filter = Booking.booking_date.in_([selected_date, prev_day]) if is_cross_midnight else Booking.booking_date == selected_date
                result = await session.execute(
                    select(Booking)
                    .options(selectinload(Booking.service))
                    .where(
                        and_(
                            Booking.business_id == business_id,
                            date_filter,
                            Booking.status.in_(["pending", "confirmed"])
                        )
                    )
                )
                existing_bookings = result.scalars().all()

            # Convert bookings to absolute datetime ranges
            booking_ranges = []
            for booking in existing_bookings:
                b_start = datetime.combine(booking.booking_date, booking.booking_time)
                b_dur = booking.service.duration_minutes if booking.service else 60
                b_end = b_start + timedelta(minutes=b_dur)
                booking_ranges.append((b_start, b_end, booking.employee_id))

            # Генерируем слоты
            available_slots = []
            current_time = datetime.combine(selected_date, start_time)
            if is_cross_midnight:
                end_datetime = datetime.combine(next_day, end_time)
            else:
                end_datetime = datetime.combine(selected_date, end_time)

            # Если сегодня, начинаем с текущего времени + 1 час
            if selected_date == date.today():
                now_plus_hour = datetime.now() + timedelta(hours=1)
                if current_time < now_plus_hour:
                    current_time = now_plus_hour
                    minutes = (current_time.minute // interval + 1) * interval
                    if minutes >= 60:
                        current_time = current_time.replace(minute=0) + timedelta(hours=1)
                    else:
                        current_time = current_time.replace(minute=minutes)

            while current_time + timedelta(minutes=total_duration) <= end_datetime:
                # Проверяем перерыв
                is_break = False
                if business.break_start and business.break_end:
                    ct = current_time.time()
                    if business.break_start <= ct < business.break_end:
                        is_break = True

                if not is_break:
                    slot_available = False
                    service_end_time = current_time + timedelta(minutes=total_duration)

                    if employee_id:
                        # Проверяем конкретного мастера
                        master_busy = False
                        for b_start, b_end, b_emp_id in booking_ranges:
                            if not (service_end_time <= b_start or current_time >= b_end):
                                master_busy = True
                                break

                        slot_available = not master_busy
                    else:
                        # Проверяем всех мастеров
                        for emp in available_employees:
                            emp_day_off = await is_employee_day_off(session, emp.id, selected_date)
                            if emp_day_off:
                                continue

                            master_busy = False
                            for b_start, b_end, b_emp_id in booking_ranges:
                                if b_emp_id != emp.id:
                                    continue
                                if not (service_end_time <= b_start or current_time >= b_end):
                                    master_busy = True
                                    break

                            if not master_busy:
                                slot_available = True
                                break

                    available_slots.append({
                        'time': current_time.strftime('%H:%M'),
                        'available': slot_available
                    })

                current_time += timedelta(minutes=interval)

            return web.json_response({'slots': available_slots})

    except Exception as e:
        logger.error(f"Error in api_client_slots: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


async def api_client_calculate_price(request: web.Request) -> web.Response:
    """Рассчитать стоимость аренды по тарифам времени"""
    try:
        business_id = int(request.match_info['business_id'])
        employee_id = request.query.get('employee_id')
        time_str = request.query.get('time')
        duration = request.query.get('duration')

        if not all([employee_id, time_str, duration]):
            return web.json_response({'error': 'Missing parameters'}, status=400)

        employee_id = int(employee_id)
        duration = int(duration)
        from datetime import time as dt_time
        t_parts = time_str.split(':')
        booking_time = dt_time(hour=int(t_parts[0]), minute=int(t_parts[1]))

        # Опциональная дата для фильтрации будни/выходные
        date_str = request.query.get('date')
        booking_date = None
        if date_str:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        async with async_session_maker() as session:
            result = await calculate_time_based_price_detailed(session, employee_id, booking_time, duration, booking_date=booking_date)

            if result is not None and result['total'] > 0:
                # Format segments for display
                segments = []
                for seg in result['segments']:
                    hours = seg['minutes'] / 60
                    if hours == int(hours):
                        hours_str = f"{int(hours)} ч"
                    else:
                        hours_str = f"{seg['minutes']} мин"
                    segments.append({
                        'time_from': seg['time_from'],
                        'time_to': seg['time_to'],
                        'minutes': seg['minutes'],
                        'price_per_hour': seg['price_per_hour'],
                        'subtotal': seg['subtotal'],
                        'hours_str': hours_str,
                    })
                return web.json_response({
                    'has_time_price': True,
                    'total': result['total'],
                    'segments': segments,
                })
            else:
                return web.json_response({
                    'has_time_price': False,
                    'total': 0,
                    'segments': [],
                })

    except Exception as e:
        logger.error(f"Error in api_client_calculate_price: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_create_booking(request: web.Request) -> web.Response:
    """Создание записи через старую страницу закрыто: только servgo.ru (/api/bookings)."""
    return web.json_response(
        {'error': 'Запись перенесена на новую страницу', 'booking_url': client_booking_url()},
        status=410,
    )


async def get_client_from_request(request: web.Request):
    """Получить клиента из запроса WebApp"""
    user = await get_authenticated_telegram_user(request)

    if not user:
        return None, None

    telegram_id = user.get('id')
    async with async_session_maker() as session:
        db_user = await get_user_by_telegram_id(session, telegram_id)
        return user, db_user


async def api_client_my_bookings(request: web.Request) -> web.Response:
    """Получить активные записи клиента"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'bookings': []})

            bookings = await get_user_bookings(session, db_user.id)

            # Фильтруем только активные записи (pending, confirmed)
            # и записи на будущие даты
            from datetime import date as date_type
            today = date_type.today()

            result = []
            for booking in bookings:
                if booking.status not in ('pending', 'confirmed'):
                    continue
                if booking.booking_date < today:
                    continue

                # Определяем, можно ли отменить/перенести
                can_cancel = booking.status in ('pending', 'confirmed')
                can_reschedule = booking.status in ('pending', 'confirmed')

                result.append({
                    'id': booking.id,
                    'business_name': booking.business.name if booking.business else 'Неизвестно',
                    'business_id': booking.business_id,
                    'business_type': booking.business.business_type if booking.business else None,
                    'services': [{
                        'name': booking.service.name if booking.service else 'Услуга',
                        'price': booking.service.price if booking.service else 0,
                    }],
                    'service_id': booking.service_id,
                    'duration': booking.service.duration_minutes if booking.service else 0,
                    'employee_name': booking.employee.name if booking.employee else 'Любой',
                    'employee_id': booking.employee_id,
                    'date': booking.booking_date.isoformat(),
                    'time': booking.booking_time.strftime('%H:%M'),
                    'status': booking.status,
                    'can_cancel': can_cancel,
                    'can_reschedule': can_reschedule,
                })

            return web.json_response({'bookings': result})

    except Exception as e:
        logger.error(f"Error in api_client_my_bookings: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_cancel_booking(request: web.Request) -> web.Response:
    """Отменить запись клиента"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        booking_id = int(request.match_info['booking_id'])

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            booking = await get_booking_by_id(session, booking_id)
            if not booking:
                return web.json_response({'error': 'Booking not found'}, status=404)

            # Проверяем, что запись принадлежит пользователю
            if booking.client_id != db_user.id:
                return web.json_response({'error': 'Access denied'}, status=403)

            # Проверяем, можно ли отменить
            if booking.status not in ('pending', 'confirmed'):
                return web.json_response({'error': 'Cannot cancel this booking'}, status=400)

            # Отменяем запись
            await update_booking_status(session, booking_id, 'cancelled')

            return web.json_response({
                'success': True,
                'message': 'Запись отменена'
            })

    except Exception as e:
        logger.error(f"Error in api_client_cancel_booking: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_reschedule_booking(request: web.Request) -> web.Response:
    """Перенести запись клиента на новую дату/время"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        booking_id = int(request.match_info['booking_id'])
        data = await request.json()
        new_date_str = data.get('date')
        new_time_str = data.get('time')

        if not new_date_str or not new_time_str:
            return web.json_response({'error': 'date and time are required'}, status=400)

        new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
        new_time = datetime.strptime(new_time_str, '%H:%M').time()

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            booking = await get_booking_by_id(session, booking_id)
            if not booking:
                return web.json_response({'error': 'Booking not found'}, status=404)

            # Проверяем, что запись принадлежит пользователю
            if booking.client_id != db_user.id:
                return web.json_response({'error': 'Access denied'}, status=403)

            # Проверяем, можно ли перенести
            if booking.status not in ('pending', 'confirmed'):
                return web.json_response({'error': 'Cannot reschedule this booking'}, status=400)

            # Сохраняем старые значения
            old_date = booking.booking_date
            old_time = booking.booking_time

            # Находим группу связанных записей (один визит)
            all_user_bookings = await get_user_bookings(session, db_user.id)
            booking_group = _find_booking_group(all_user_bookings, booking)

            # Вычисляем общую длительность группы
            total_duration = sum(
                b.service.duration_minutes if b.service else 30
                for b in booking_group
            )

            # Проверяем доступность слотов
            employee_id = booking.employee_id
            business = await get_business_by_id(session, booking.business_id)
            if not business:
                return web.json_response({'error': 'Business not found'}, status=404)

            from sqlalchemy import select, and_
            from database.models import Booking as BookingModel, Employee

            slot_start = datetime.combine(new_date, new_time)
            slot_end = slot_start + timedelta(minutes=total_duration)

            # Проверяем рабочие часы
            start_time = business.working_hours_start if business.working_hours_start else datetime.strptime("09:00", "%H:%M").time()
            end_time = business.working_hours_end if business.working_hours_end else datetime.strptime("18:00", "%H:%M").time()

            if new_time < start_time or slot_end.time() > end_time:
                return web.json_response({'error': 'Выбранное время вне рабочих часов'}, status=400)

            # Проверяем перерыв
            if business.break_start and business.break_end:
                break_start_dt = datetime.combine(new_date, business.break_start)
                break_end_dt = datetime.combine(new_date, business.break_end)
                if not (slot_end <= break_start_dt or slot_start >= break_end_dt):
                    return web.json_response({'error': 'Выбранное время попадает на перерыв'}, status=400)

            # Получаем существующие записи на новую дату (исключая текущую группу)
            group_ids = [b.id for b in booking_group]

            if employee_id:
                # Проверяем выходной
                is_day_off = await is_employee_day_off(session, employee_id, new_date)
                if is_day_off:
                    return web.json_response({'error': 'Специалист не работает в этот день'}, status=400)

                result = await session.execute(
                    select(BookingModel)
                    .options(selectinload(BookingModel.service))
                    .where(
                        and_(
                            BookingModel.business_id == booking.business_id,
                            BookingModel.employee_id == employee_id,
                            BookingModel.booking_date == new_date,
                            BookingModel.status.in_(["pending", "confirmed"]),
                            BookingModel.id.notin_(group_ids)
                        )
                    )
                )
                existing_bookings = result.scalars().all()

                # Проверяем пересечения
                for eb in existing_bookings:
                    eb_start = datetime.combine(new_date, eb.booking_time)
                    eb_end = eb_start + timedelta(minutes=eb.service.duration_minutes if eb.service else 30)
                    if not (slot_end <= eb_start or slot_start >= eb_end):
                        return web.json_response({'error': 'Выбранное время уже занято'}, status=400)
            else:
                # Если мастер "любой" — найти свободного
                result = await session.execute(
                    select(Employee).where(
                        and_(
                            Employee.business_id == booking.business_id,
                            Employee.is_active == True,
                            Employee.role.in_(['master', 'master_and_admin'])
                        )
                    )
                )
                available_employees = result.scalars().all()

                found_employee = None
                for emp in available_employees:
                    emp_day_off = await is_employee_day_off(session, emp.id, new_date)
                    if emp_day_off:
                        continue

                    result_bookings = await session.execute(
                        select(BookingModel)
                        .options(selectinload(BookingModel.service))
                        .where(
                            and_(
                                BookingModel.employee_id == emp.id,
                                BookingModel.booking_date == new_date,
                                BookingModel.status.in_(['pending', 'confirmed']),
                                BookingModel.id.notin_(group_ids)
                            )
                        )
                    )
                    emp_bookings = result_bookings.scalars().all()

                    busy = False
                    for eb in emp_bookings:
                        eb_start = datetime.combine(new_date, eb.booking_time)
                        eb_end = eb_start + timedelta(minutes=eb.service.duration_minutes if eb.service else 30)
                        if not (slot_end <= eb_start or slot_start >= eb_end):
                            busy = True
                            break

                    if not busy:
                        found_employee = emp.id
                        break

                if not found_employee:
                    return web.json_response({'error': 'Нет свободных специалистов на это время'}, status=400)

                employee_id = found_employee

            # Обновляем все записи в группе
            current_time = slot_start
            for b in booking_group:
                b.booking_date = new_date
                b.booking_time = current_time.time()
                b.employee_id = employee_id
                b.updated_at = datetime.utcnow()
                # Сбрасываем напоминания для нового времени
                b.reminder_24h_sent = False
                b.reminder_2h_sent = False
                current_time += timedelta(minutes=b.service.duration_minutes if b.service else 30)

            await session.commit()

            # Перезагружаем запись для уведомлений
            updated_booking = await get_booking_by_id(session, booking_id)

            # Отправляем уведомления
            try:
                bot = Bot(token=BOT_TOKEN)
                await notify_owner_rescheduled_booking(bot, updated_booking, old_date, old_time)
                await bot.session.close()
            except Exception as notify_error:
                logger.error(f"Error sending reschedule notifications: {notify_error}")

            return web.json_response({
                'success': True,
                'old_date': old_date.isoformat(),
                'old_time': old_time.strftime('%H:%M'),
                'new_date': new_date.isoformat(),
                'new_time': new_time.strftime('%H:%M'),
                'message': f'Запись перенесена на {new_date_str} в {new_time_str}'
            })

    except Exception as e:
        logger.error(f"Error in api_client_reschedule_booking: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


def _find_booking_group(bookings: list, target_booking) -> list:
    """Находит группу связанных записей (один визит) для указанной записи."""
    same_context = [
        b for b in bookings
        if b.business_id == target_booking.business_id
        and b.booking_date == target_booking.booking_date
        and b.status in ['pending', 'confirmed']
    ]

    if not same_context:
        return [target_booking]

    same_context = sorted(same_context, key=lambda b: b.booking_time)

    groups = []
    current_group = [same_context[0]]

    for i in range(1, len(same_context)):
        prev = same_context[i - 1]
        curr = same_context[i]

        prev_end = datetime.combine(prev.booking_date, prev.booking_time) + timedelta(minutes=prev.service.duration_minutes if prev.service else 30)
        curr_start = datetime.combine(curr.booking_date, curr.booking_time)

        if abs((curr_start - prev_end).total_seconds()) <= 300:
            current_group.append(curr)
        else:
            groups.append(current_group)
            current_group = [curr]

    groups.append(current_group)

    for group in groups:
        if any(b.id == target_booking.id for b in group):
            return group

    return [target_booking]


async def api_client_all_subscriptions(request: web.Request) -> web.Response:
    """Получить все доступные абонементы (по всем бизнесам)"""
    try:
        async with async_session_maker() as session:
            businesses = await get_all_active_businesses(session)

            result = []
            for business in businesses:
                subscriptions = await get_business_subscriptions(session, business.id, active_only=True)

                for sub in subscriptions:
                    # Рассчитываем стоимость услуг по отдельности и экономию
                    total_value = 0
                    services_list = []
                    for sub_svc in sub.services:
                        service = sub_svc.service
                        service_total = (service.price or 0) * sub_svc.quantity
                        total_value += service_total
                        services_list.append({
                            'name': service.name,
                            'quantity': sub_svc.quantity,
                            'price': service.price or 0,
                        })

                    savings = max(0, total_value - sub.price)
                    savings_percent = int((savings / total_value) * 100) if total_value > 0 else 0

                    result.append({
                        'id': sub.id,
                        'name': sub.name,
                        'business_id': business.id,
                        'business_name': business.name,
                        'price': sub.price,
                        'validity_days': sub.validity_days,
                        'description': sub.description,
                        'services': services_list,
                        'savings': savings,
                        'savings_percent': savings_percent,
                    })

            return web.json_response({'subscriptions': result})

    except Exception as e:
        logger.error(f"Error in api_client_all_subscriptions: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_my_subscriptions(request: web.Request) -> web.Response:
    """Получить абонементы клиента"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'subscriptions': []})

            client_subs = await get_client_subscriptions(session, db_user.id)

            result = []
            for cs in client_subs:
                # Получаем остаток услуг
                remaining = await get_subscription_remaining(session, cs.id)

                remaining_list = []
                for service_id, info in remaining.items():
                    remaining_list.append({
                        'service': info['service_name'],
                        'used': info['used'],
                        'total': info['total'],
                    })

                # Расчет дней до окончания
                from datetime import datetime
                days_left = 0
                if cs.status == 'active' and cs.expires_at:
                    days_left = max(0, (cs.expires_at - datetime.utcnow()).days)

                result.append({
                    'id': cs.id,
                    'name': cs.subscription.name if cs.subscription else 'Абонемент',
                    'business_name': cs.business.name if cs.business else 'Неизвестно',
                    'status': cs.status,
                    'expires_at': cs.expires_at.strftime('%Y-%m-%d') if cs.expires_at else None,
                    'days_left': days_left,
                    'remaining': remaining_list,
                })

            return web.json_response({'subscriptions': result})

    except Exception as e:
        logger.error(f"Error in api_client_my_subscriptions: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_subscription_details(request: web.Request) -> web.Response:
    """Получить детали абонемента"""
    try:
        subscription_id = int(request.match_info['subscription_id'])

        async with async_session_maker() as session:
            subscription = await get_subscription_by_id(session, subscription_id)
            if not subscription:
                return web.json_response({'error': 'Subscription not found'}, status=404)

            business = subscription.business

            # Рассчитываем стоимость услуг и экономию
            total_value = 0
            services_list = []
            for sub_svc in subscription.services:
                service = sub_svc.service
                service_total = (service.price or 0) * sub_svc.quantity
                total_value += service_total
                services_list.append({
                    'name': service.name,
                    'quantity': sub_svc.quantity,
                    'price': service.price or 0,
                    'total': service_total,
                })

            savings = max(0, total_value - subscription.price)
            savings_percent = int((savings / total_value) * 100) if total_value > 0 else 0

            # Проверяем, настроены ли реквизиты оплаты
            has_payment_details = bool(business.payment_phone or business.payment_details)

            return web.json_response({
                'subscription': {
                    'id': subscription.id,
                    'name': subscription.name,
                    'description': subscription.description,
                    'rules': subscription.rules,
                    'price': subscription.price,
                    'validity_days': subscription.validity_days,
                    'services': services_list,
                    'total_value': total_value,
                    'savings': savings,
                    'savings_percent': savings_percent,
                    'business_id': business.id,
                    'business_name': business.name,
                    'has_payment_details': has_payment_details,
                }
            })

    except Exception as e:
        logger.error(f"Error in api_client_subscription_details: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_buy_subscription(request: web.Request) -> web.Response:
    """Инициировать покупку абонемента"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        first_name = user.get('first_name', '')
        last_name = user.get('last_name', '')
        username = user.get('username', '')

        subscription_id = int(request.match_info['subscription_id'])

        async with async_session_maker() as session:
            subscription = await get_subscription_by_id(session, subscription_id)
            if not subscription:
                return web.json_response({'error': 'Subscription not found'}, status=404)

            business = subscription.business

            # Проверяем наличие реквизитов
            if not business.payment_phone and not business.payment_details:
                return web.json_response({
                    'error': 'Владелец не настроил реквизиты для оплаты'
                }, status=400)

            # Получаем или создаем пользователя
            full_name = f"{first_name} {last_name}".strip() or username or f"User {telegram_id}"
            db_user = await get_or_create_user(
                session,
                telegram_id=telegram_id,
                username=username,
                full_name=full_name
            )

            # Создаем запись о покупке
            client_sub = await purchase_subscription(
                session,
                subscription_id=subscription_id,
                client_id=db_user.id,
                business_id=business.id
            )

            # Формируем реквизиты оплаты
            payment_details = []
            if business.payment_phone:
                payment_details.append({
                    'type': 'phone',
                    'value': business.payment_phone,
                    'label': 'Телефон для перевода'
                })
            if business.payment_details:
                payment_details.append({
                    'type': 'text',
                    'value': business.payment_details,
                    'label': 'Реквизиты'
                })

            return web.json_response({
                'success': True,
                'client_subscription_id': client_sub.id,
                'subscription_name': subscription.name,
                'amount': subscription.price,
                'payment_details': payment_details,
            })

    except Exception as e:
        logger.error(f"Error in api_client_buy_subscription: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


async def api_client_subscription_payment_sent(request: web.Request) -> web.Response:
    """Подтвердить отправку оплаты (без чека)"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        client_sub_id = int(request.match_info['client_sub_id'])

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            cs = await get_client_subscription_by_id(session, client_sub_id)
            if not cs:
                return web.json_response({'error': 'Subscription not found'}, status=404)

            # Проверяем, что абонемент принадлежит пользователю
            if cs.client_id != db_user.id:
                return web.json_response({'error': 'Access denied'}, status=403)

            # Обновляем статус
            await update_client_subscription_status(session, client_sub_id, "payment_sent")

            # Отправляем уведомление владельцу
            business = cs.business
            owner_telegram_id = business.owner.telegram_id if business.owner else None

            if owner_telegram_id:
                try:
                    from bot.keyboards import subscription_kb
                    bot = Bot(token=BOT_TOKEN)
                    await bot.send_message(
                        owner_telegram_id,
                        f"💳 <b>Новая заявка на абонемент!</b>\n\n"
                        f"🎫 Абонемент: {cs.subscription.name}\n"
                        f"👤 Клиент: {db_user.full_name}\n"
                        f"💰 Сумма: {cs.subscription.price}₽\n\n"
                        f"Клиент сообщил, что отправил оплату.\n"
                        f"Проверьте поступление и подтвердите:",
                        parse_mode="HTML",
                        reply_markup=subscription_kb.get_pending_payment_keyboard(client_sub_id, business.id)
                    )
                    await bot.session.close()
                except Exception as notify_error:
                    logger.error(f"Failed to send payment notification: {notify_error}")

            return web.json_response({
                'success': True,
                'message': 'Заявка отправлена! Ожидайте подтверждения.'
            })

    except Exception as e:
        logger.error(f"Error in api_client_subscription_payment_sent: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_client_subscription_upload_receipt(request: web.Request) -> web.Response:
    """Загрузить фото чека"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        client_sub_id = int(request.match_info['client_sub_id'])

        # Читаем multipart данные
        reader = await request.multipart()
        field = await reader.next()

        if not field or field.name != 'receipt':
            return web.json_response({'error': 'No receipt file provided'}, status=400)

        # Читаем файл
        file_data = await field.read()

        if len(file_data) > 10 * 1024 * 1024:  # 10MB limit
            return web.json_response({'error': 'File too large'}, status=400)

        async with async_session_maker() as session:
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            cs = await get_client_subscription_by_id(session, client_sub_id)
            if not cs:
                return web.json_response({'error': 'Subscription not found'}, status=404)

            if cs.client_id != db_user.id:
                return web.json_response({'error': 'Access denied'}, status=403)

            business = cs.business
            owner_telegram_id = business.owner.telegram_id if business.owner else None

            # Отправляем фото владельцу через Telegram и получаем file_id
            file_id = None
            if owner_telegram_id:
                try:
                    from aiogram.types import BufferedInputFile
                    from bot.keyboards import subscription_kb

                    bot = Bot(token=BOT_TOKEN)

                    # Отправляем уведомление
                    await bot.send_message(
                        owner_telegram_id,
                        f"💳 <b>Новая заявка на абонемент!</b>\n\n"
                        f"🎫 Абонемент: {cs.subscription.name}\n"
                        f"👤 Клиент: {db_user.full_name}\n"
                        f"💰 Сумма: {cs.subscription.price}₽\n"
                        f"📎 Чек прикреплен (см. ниже)\n\n"
                        f"Проверьте и подтвердите:",
                        parse_mode="HTML",
                        reply_markup=subscription_kb.get_pending_payment_keyboard(client_sub_id, business.id)
                    )

                    # Отправляем фото
                    photo_input = BufferedInputFile(file_data, filename="receipt.jpg")
                    photo_msg = await bot.send_photo(
                        owner_telegram_id,
                        photo_input,
                        caption=f"📎 Чек от {db_user.full_name}"
                    )
                    file_id = photo_msg.photo[-1].file_id

                    await bot.session.close()
                except Exception as notify_error:
                    logger.error(f"Failed to send receipt: {notify_error}")

            # Сохраняем file_id чека
            if file_id:
                await set_payment_receipt(session, client_sub_id, file_id)
            else:
                # Если не удалось отправить в Telegram, просто обновляем статус
                await update_client_subscription_status(session, client_sub_id, "payment_sent")

            return web.json_response({
                'success': True,
                'message': 'Чек прикреплен! Ожидайте подтверждения.'
            })

    except Exception as e:
        logger.error(f"Error in api_client_subscription_upload_receipt: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


async def api_analytics_schedule(request: web.Request) -> web.Response:
    """Получить расписание для Excel-подобного отображения"""
    try:
        user, admin, _ = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        business_id = request.query.get('business_id')
        start_date_str = request.query.get('start_date')
        end_date_str = request.query.get('end_date')

        if not all([business_id, start_date_str, end_date_str]):
            return web.json_response({'error': 'Missing parameters'}, status=400)

        business_id = int(business_id)
        start_date = date.fromisoformat(start_date_str)
        end_date = date.fromisoformat(end_date_str)

        async with async_session_maker() as session:
            # Получаем бизнес
            business = await get_business_by_id(session, business_id)
            if not business:
                return web.json_response({'error': 'Business not found'}, status=404)

            # Получаем записи за период
            bookings = await get_business_bookings(session, business_id, start_date, end_date)

            # Получаем сотрудников
            employees = await get_business_employees(session, business_id)
            active_employees = [e for e in employees if e.is_active and e.role in ('master', 'master_and_admin')]

            # Сортируем помещения venue_rental так же, как при бронировании клиентом
            if business.business_type == 'venue_rental':
                def _room_group_sort(name: str) -> int:
                    n = name.lower()
                    if 'красн' in n: return 0
                    if 'сер' in n: return 1
                    if 'чёрн' in n or 'черн' in n: return 2
                    if 'бел' in n: return 3
                    if 'бежев' in n: return 4
                    return 5
                active_employees.sort(key=lambda e: _room_group_sort(e.name))

            # Формируем данные для расписания
            # Отмененные записи показываем только в течение 1 минуты после отмены
            # Группируем записи с доп. услугами
            now = datetime.utcnow()

            # Группируем по клиенту + дата + время + сотрудник
            booking_groups = {}
            for booking in bookings:
                # Пропускаем отмененные записи старше 1 минуты
                if booking.status == 'cancelled':
                    if booking.updated_at and (now - booking.updated_at).total_seconds() > 60:
                        continue

                # Ключ группировки
                group_key = f"{booking.client_id}_{booking.booking_date}_{booking.booking_time}_{booking.employee_id}"

                if group_key not in booking_groups:
                    booking_groups[group_key] = {
                        'main': None,
                        'additional': [],
                        'booking': booking
                    }

                # Определяем основную или доп. услугу
                is_additional = booking.service.is_additional if booking.service else False
                if is_additional:
                    booking_groups[group_key]['additional'].append(booking)
                else:
                    booking_groups[group_key]['main'] = booking

            # Формируем итоговые данные
            bookings_data = []
            for group_key, group in booking_groups.items():
                main_booking = group['main'] or group['booking']
                additional_bookings = group['additional']

                # Собираем названия услуг
                service_names = []
                total_duration = 0
                total_price = 0

                if group['main']:
                    service_names.append(group['main'].service.name if group['main'].service else 'Услуга')
                    total_duration += group['main'].service.duration_minutes if group['main'].service else 60
                    total_price += group['main'].service.price or 0 if group['main'].service else 0

                for add_booking in additional_bookings:
                    if add_booking.service:
                        service_names.append(f"+{add_booking.service.name}")
                        total_price += add_booking.service.price or 0

                bookings_data.append({
                    'id': main_booking.id,
                    'date': main_booking.booking_date.isoformat(),
                    'time': main_booking.booking_time.strftime('%H:%M'),
                    'client_id': main_booking.client_id,
                    'client_name': main_booking.client.full_name if main_booking.client else 'Клиент',
                    'client_phone': main_booking.client.phone if main_booking.client else None,
                    'service_id': main_booking.service_id,
                    'service_name': ', '.join(service_names) if service_names else 'Услуга',
                    'duration': total_duration if total_duration > 0 else 60,
                    'price': total_price if total_price > 0 else None,
                    'employee_id': main_booking.employee_id,
                    'employee_name': main_booking.employee.name if main_booking.employee else 'Не указан',
                    'status': main_booking.status,
                    'prepayment_amount': main_booking.prepayment_amount,
                    'comment': getattr(main_booking, 'comment', None),
                })

            # Формируем список сотрудников для табов
            employees_data = []
            is_venue = business.business_type == 'venue_rental'
            for emp in active_employees:
                employees_data.append({
                    'id': emp.id,
                    'name': emp.name,
                    'is_venue': is_venue,
                })

            # Рабочие часы
            working_hours = {
                'start': business.working_hours_start.hour if business.working_hours_start else 9,
                'end': business.working_hours_end.hour if business.working_hours_end else 22,
            }

            # Минимальный интервал бронирования (по всем активным сотрудникам)
            min_interval = business.booking_interval_minutes or 30
            for emp in active_employees:
                if emp.booking_interval_minutes:
                    min_interval = min(min_interval, emp.booking_interval_minutes)

            # Получаем услуги для формы создания записи
            services = await get_business_main_services(session, business_id)
            services_data = []
            for svc in services:
                emp_ids = None
                if svc.available_employee_ids:
                    try:
                        emp_ids = json.loads(svc.available_employee_ids)
                    except:
                        pass
                services_data.append({
                    'id': svc.id,
                    'name': svc.name,
                    'duration': svc.duration_minutes,
                    'price': svc.price,
                    'employee_ids': emp_ids,
                })

            # Получаем клиентов бизнеса для автокомплита
            clients = await get_business_clients(session, business_id, limit=200)
            clients_data = []
            for cl in clients:
                clients_data.append({
                    'id': cl.id,
                    'name': cl.full_name or '',
                    'phone': cl.phone or '',
                })

            # Определяем, может ли текущий пользователь создавать записи
            # (только владельцы могут, администраторы-сотрудники — нет)
            from database.models import Employee as EmployeeModel
            can_create_booking = not isinstance(admin, EmployeeModel)

            return web.json_response({
                'bookings': bookings_data,
                'employees': employees_data,
                'services': services_data,
                'clients': clients_data,
                'working_hours': working_hours,
                'booking_interval': min_interval,
                'business_type': business.business_type,
                'can_create_booking': can_create_booking,
            })

    except Exception as e:
        logger.error(f"Error in api_analytics_schedule: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


async def api_admin_create_booking(request: web.Request) -> web.Response:
    """Создать запись от владельца через расписание"""
    try:
        user, admin, _ = await get_user_from_request(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        data = await request.json()
        business_id = data.get('business_id')
        service_id = data.get('service_id')
        employee_id = data.get('employee_id')
        booking_date_str = data.get('date')
        booking_time_str = data.get('time')
        client_name = data.get('client_name', '').strip()
        client_phone = data.get('client_phone', '').strip()

        if not all([business_id, service_id, booking_date_str, booking_time_str, client_name]):
            return web.json_response({'error': 'Заполните все обязательные поля'}, status=400)

        selected_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        selected_time = datetime.strptime(booking_time_str, '%H:%M').time()

        async with async_session_maker() as session:
            # Проверяем права владельца
            telegram_id = user.get('id')
            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            if not await is_business_owner(session, business_id, db_user.id):
                return web.json_response({'error': 'Access denied'}, status=403)

            business = await get_business_by_id(session, business_id)
            if not business:
                return web.json_response({'error': 'Business not found'}, status=404)

            # Создаём или находим клиента
            from sqlalchemy import select, and_
            client = await get_or_create_guest_user(session, client_name, client_phone)

            # Если сотрудник не выбран, выбираем первого свободного
            if not employee_id:
                employees = await get_business_employees(session, business_id)
                active_emps = [e for e in employees if e.is_active and e.role in ('master', 'master_and_admin')]

                service = await get_service_by_id(session, service_id)
                svc_duration = service.duration_minutes if service else 30

                slot_start = datetime.combine(selected_date, selected_time)
                slot_end = slot_start + timedelta(minutes=svc_duration)

                from database.models import Booking as BookingModel
                for emp in active_emps:
                    is_off = await is_employee_day_off(session, emp.id, selected_date)
                    if is_off:
                        continue

                    result_bookings = await session.execute(
                        select(BookingModel)
                        .options(selectinload(BookingModel.service))
                        .where(
                            and_(
                                BookingModel.employee_id == emp.id,
                                BookingModel.booking_date == selected_date,
                                BookingModel.status.in_(['pending', 'confirmed'])
                            )
                        )
                    )
                    emp_bookings = result_bookings.scalars().all()

                    busy = False
                    for b in emp_bookings:
                        b_start = datetime.combine(selected_date, b.booking_time)
                        b_end = b_start + timedelta(minutes=b.service.duration_minutes if b.service else 30)
                        if not (slot_end <= b_start or slot_start >= b_end):
                            busy = True
                            break

                    if not busy:
                        employee_id = emp.id
                        break

                if not employee_id:
                    return web.json_response({'error': 'Нет свободных сотрудников на это время'}, status=400)

            # Создаём запись
            booking = await create_booking(
                session,
                business_id=business_id,
                client_id=client.id,
                service_id=service_id,
                employee_id=employee_id,
                booking_date=selected_date,
                booking_time=selected_time,
                status='confirmed',
            )

            service = await get_service_by_id(session, service_id)
            employee_obj = await get_employee_by_id(session, employee_id)

            # Отправляем уведомления мастеру, администраторам и клиенту
            try:
                bot = Bot(token=BOT_TOKEN)
                booking_with_relations = await get_booking_by_id(session, booking.id)

                services_list = [{
                    'name': service.name if service else '',
                    'duration': service.duration_minutes if service else 0,
                    'price': service.price or 0,
                    'is_additional': False,
                    'employee_id': employee_id,
                    'employee_name': employee_obj.name if employee_obj else '',
                    'employee_telegram_id': employee_obj.telegram_id if employee_obj else None,
                }]

                if booking_with_relations:
                    await notify_master_new_booking(bot, booking_with_relations, services_list)
                    await notify_administrator_new_booking(bot, booking_with_relations, services_list)

                    # Уведомление клиенту (если есть telegram_id)
                    if client.telegram_id and client.telegram_id != 0:
                        try:
                            from bot.utils.business_types import is_venue_rental_type
                            is_venue = is_venue_rental_type(business.business_type)
                            weekdays_ru = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
                            formatted_date = selected_date.strftime('%d.%m.%Y') + f" ({weekdays_ru[selected_date.weekday()]})"
                            price_str = f"\n💰 Стоимость: {service.price}₽" if service and service.price else ""
                            emp_line = ""
                            if employee_obj:
                                emp_label = "🏠 Помещение" if is_venue else "👤 Мастер"
                                emp_line = f"\n{emp_label}: {employee_obj.name}"
                            await bot.send_message(
                                client.telegram_id,
                                f"✅ <b>Вас записали!</b>\n\n"
                                f"🏢 {business.name}\n"
                                f"📅 {formatted_date} в {selected_time.strftime('%H:%M')}\n"
                                f"💼 Услуга: {service.name if service else ''}{price_str}"
                                f"{emp_line}",
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass

                await bot.session.close()
            except Exception as notify_err:
                logger.error(f"Notification error in api_admin_create_booking: {notify_err}")

            return web.json_response({
                'success': True,
                'booking': {
                    'id': booking.id,
                    'date': selected_date.isoformat(),
                    'time': selected_time.strftime('%H:%M'),
                    'client_name': client_name,
                    'service_name': service.name if service else '',
                    'status': 'confirmed',
                }
            })

    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Error in api_admin_create_booking: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


async def api_client_prepayment_sent(request: web.Request) -> web.Response:
    """Клиент подтверждает отправку предоплаты за бронирование"""
    try:
        user = await get_authenticated_telegram_user(request)

        if not user:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        telegram_id = user.get('id')
        booking_id = int(request.match_info['booking_id'])

        async with async_session_maker() as session:
            from sqlalchemy import select, and_
            from database.models import Booking as BookingModel

            db_user = await get_user_by_telegram_id(session, telegram_id)
            if not db_user:
                return web.json_response({'error': 'User not found'}, status=404)

            booking = await get_booking_by_id(session, booking_id)
            if not booking:
                return web.json_response({'error': 'Booking not found'}, status=404)

            if booking.client_id != db_user.id:
                return web.json_response({'error': 'Access denied'}, status=403)

            if booking.status not in ('pending_prepayment', 'prepayment_sent'):
                return web.json_response({'error': 'Invalid booking status'}, status=400)

            # Находим все связанные бронирования (того же клиента, на ту же дату/мастера)
            related_result = await session.execute(
                select(BookingModel)
                .options(selectinload(BookingModel.service))
                .where(and_(
                    BookingModel.client_id == booking.client_id,
                    BookingModel.business_id == booking.business_id,
                    BookingModel.employee_id == booking.employee_id,
                    BookingModel.booking_date == booking.booking_date,
                    BookingModel.status == 'pending_prepayment',
                ))
            )
            related_bookings = related_result.scalars().all()

            # Обновляем статус всех связанных на "prepayment_sent"
            for rb in related_bookings:
                rb.status = "prepayment_sent"
            if booking.status == 'pending_prepayment':
                booking.status = "prepayment_sent"
            await session.commit()

            # Собираем список всех услуг
            services_text = ""
            for rb in related_bookings:
                svc = rb.service
                if svc:
                    is_add = svc.is_additional if hasattr(svc, 'is_additional') else False
                    prefix = "  ➕" if is_add else "  📋"
                    price_txt = f" ({svc.price}₽)" if svc.price else ""
                    services_text += f"{prefix} {svc.name}{price_txt}\n"

            # Получаем бизнес для уведомления владельцу
            business = await get_business_by_id(session, booking.business_id)
            owner_telegram_id = business.owner.telegram_id if business.owner else None

            # Отправляем уведомление владельцу
            if owner_telegram_id:
                try:
                    from bot.keyboards.admin_kb import get_booking_admin_actions_keyboard
                    bot = Bot(token=BOT_TOKEN)

                    await bot.send_message(
                        owner_telegram_id,
                        f"💰 <b>Клиент отправил предоплату!</b>\n\n"
                        f"🏢 {business.name}\n"
                        f"👤 Клиент: {db_user.full_name}\n"
                        f"📱 Телефон: {db_user.phone or 'не указан'}\n"
                        f"📋 <b>Услуги:</b>\n{services_text}"
                        f"📅 {booking.booking_date.strftime('%d.%m.%Y')} в {booking.booking_time.strftime('%H:%M')}\n"
                        f"\n💰 <b>Сумма предоплаты: {booking.prepayment_amount}₽</b>\n\n"
                        f"Проверьте поступление и подтвердите:",
                        parse_mode="HTML",
                        reply_markup=get_booking_admin_actions_keyboard(booking_id, show_prepayment_confirm=True)
                    )

                    await bot.session.close()
                except Exception as notify_error:
                    logger.error(f"Error sending prepayment notification: {notify_error}")

            return web.json_response({
                'success': True,
                'message': 'Уведомление отправлено! Ожидайте подтверждения.'
            })

    except Exception as e:
        logger.error(f"Error in api_client_prepayment_sent: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)


def create_webapp() -> web.Application:
    """Создание веб-приложения"""
    import os

    app = web.Application()
    try:
        init_shared_auth_tables()
    except Exception as e:
        logger.error(f"Shared auth table initialization failed: {e}")

    # Статические файлы
    static_path = os.path.join(os.path.dirname(__file__), 'static')

    # Роуты - Календарь
    app.router.add_get('/', index_handler)
    app.router.add_get('/calendar', calendar_handler)
    app.router.add_get('/api/calendar', api_calendar_data)
    app.router.add_get('/api/booking/{booking_id}', api_booking_details)
    app.router.add_post('/api/booking/{booking_id}/status', api_update_booking_status)
    app.router.add_get('/api/day/{date}', api_day_bookings)

    # Роуты - Аналитика
    app.router.add_get('/analytics', analytics_handler)
    app.router.add_get('/api/analytics/businesses', api_analytics_businesses)
    app.router.add_get('/api/analytics/data', api_analytics_data)
    app.router.add_get('/api/analytics/schedule', api_analytics_schedule)

    # Роуты - Авторизация владельцев (веб)
    app.router.add_get('/login', login_handler)
    app.router.add_get('/auth/token/{token}', auth_token_handler)
    app.router.add_post('/api/landing/connect-request', api_landing_connect_request)
    app.router.add_post('/api/auth/login', api_auth_login)
    app.router.add_post('/api/auth/telegram', api_auth_telegram)
    app.router.add_get('/api/auth/me', api_auth_me)
    app.router.add_post('/api/auth/logout', api_auth_logout)
    app.router.add_get('/api/auth/check', api_auth_check)

    # Роуты - Расписание (страница расписания перенесена в React SPA web/frontend)
    app.router.add_post('/api/admin/booking', api_admin_create_booking)

    # Роуты - Клиентское бронирование
    app.router.add_get('/book', booking_handler)
    app.router.add_get('/api/client/businesses', api_client_businesses)
    app.router.add_get('/api/client/last-bookings', api_client_last_bookings)
    app.router.add_get('/api/client/photo', api_get_photo)
    app.router.add_get('/api/media/{file_id}', api_media_proxy)
    app.router.add_get('/api/client/business/{business_id}', api_client_business_details)
    app.router.add_get('/api/client/business/{business_id}/services', api_client_business_details)  # Alias
    app.router.add_get('/api/client/business/{business_id}/slots', api_client_slots)
    app.router.add_get('/api/client/business/{business_id}/calculate-price', api_client_calculate_price)
    app.router.add_post('/api/client/booking', api_client_create_booking)

    # Роуты - Мои записи и абонементы (клиент)
    app.router.add_get('/api/client/my-bookings', api_client_my_bookings)
    app.router.add_post('/api/client/booking/{booking_id}/cancel', api_client_cancel_booking)
    app.router.add_post('/api/client/booking/{booking_id}/reschedule', api_client_reschedule_booking)
    app.router.add_post('/api/client/booking/{booking_id}/prepayment-sent', api_client_prepayment_sent)
    app.router.add_get('/api/client/all-subscriptions', api_client_all_subscriptions)
    app.router.add_get('/api/client/my-subscriptions', api_client_my_subscriptions)
    app.router.add_get('/api/client/subscription/{subscription_id}', api_client_subscription_details)
    app.router.add_post('/api/client/subscription/{subscription_id}/buy', api_client_buy_subscription)
    app.router.add_post('/api/client/subscription/{client_sub_id}/payment-sent', api_client_subscription_payment_sent)
    app.router.add_post('/api/client/subscription/{client_sub_id}/upload-receipt', api_client_subscription_upload_receipt)

    # Статические файлы
    app.router.add_static('/static', static_path)

    # Публичный SEO-блог: доступен без аккаунта и JavaScript.
    app.router.add_get('/blog/sitemap.xml', blog_sitemap_handler)
    app.router.add_get('/blog', blog_handler)
    app.router.add_get('/blog/{article_slug:[a-z0-9-]+}', blog_handler)
    # The anonymous CTA redirect and admin report reuse the same public
    # blog host and existing /api/landing/connect-request nginx route.
    from webapp.seo_commercial import register_seo_commercial_routes
    from database.database import engine as seo_commercial_engine
    from webapp.blog_content import BLOG_PUBLIC_ORIGIN
    register_seo_commercial_routes(
        app, engine=seo_commercial_engine, get_user=get_authenticated_telegram_user,
        admin_ids=ADMIN_IDS, public_origin=BLOG_PUBLIC_ORIGIN,
    )

    # SEO / индексация — регистрируем до catch-all /{slug}, иначе
    # serve_niche_landing поймает /sitemap.xml и /robots.txt и вернёт 404.
    app.router.add_get('/sitemap.xml', sitemap_handler)
    app.router.add_get('/robots.txt', robots_handler)
    app.router.add_get('/llms.txt', llms_txt_handler)

    # Публичная дорожная карта — до catch-all /{slug}.
    app.router.add_get('/roadmap', serve_roadmap)

    # Данные подключаемых бизнесов: независимая таблица, просмотр только platform-admin.
    from database.database import engine as booking_engine
    from webapp.onboarding_requests import register_onboarding_routes
    register_onboarding_routes(
        app, engine=booking_engine, get_user=get_authenticated_telegram_user,
        admin_ids=ADMIN_IDS, send_email=send_landing_request_email,
    )

    # Нишевые лендинги promo.servgo.ru/<slug> — регистрируется последним,
    # чтобы конкретные роуты выше (/calendar, /book, /login и т.д.) ловили
    # свой запрос раньше, а сюда попадали только остаточные слаги.
    app.router.add_get('/{slug:[a-z][a-z0-9-]*}', serve_niche_landing)

    return app


async def run_webapp(host: str = '0.0.0.0', port: int = 8080):
    """Запуск веб-сервера"""
    app = create_webapp()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
