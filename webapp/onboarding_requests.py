"""Public business-intake submissions and platform-admin review for promo.servgo.ru.

Separate intake table (not tenant/business tables). Only platform administrators
may read applications. Existing ServGo SMTP sender is injected by webapp.server.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from sqlalchemy import (
    JSON, Column, DateTime, Integer, MetaData, String, Table,
    and_, insert, or_, select, update,
)
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)
metadata = MetaData()
applications = Table(
    "servgo_onboarding_applications", metadata,
    Column("id", String(32), primary_key=True),
    Column("client_key", String(36), unique=True, nullable=False),
    Column("business_name", String(160), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("email_state", String(16), nullable=False, default="pending"),
    Column("email_attempted_at", DateTime(timezone=True), nullable=True),
    Column("emailed_at", DateTime(timezone=True), nullable=True),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
)
MAX_BODY = 131072
MAX_FIELD = 3000
DAYS = ("Понедельник", "Вторник", "Среда", "Четверг",
        "Пятница", "Суббота", "Воскресенье")
RATE_WINDOW = 900
RATE_LIMIT = 4
_recent: dict[str, list[float]] = defaultdict(list)


def _string(value, limit=MAX_FIELD):
    if not isinstance(value, str):
        raise ValueError("Некорректный формат текстового поля")
    value = value.strip()
    if len(value) > limit:
        raise ValueError("Одно из полей слишком длинное")
    return value


def validate_application(data: dict) -> tuple[dict, str, str]:
    """Reject unknown/nested data and keep all valid supplied information."""
    if not isinstance(data, dict) or data.get("consent") is not True:
        raise ValueError("Подтвердите согласие на передачу данных ServGo")
    client_key = data.get("idempotency_key")
    if not isinstance(client_key, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        client_key, re.I,
    ):
        raise ValueError("Некорректный идентификатор отправки")
    form = data.get("form")
    if not isinstance(form, dict) or form.get("version") != 1:
        raise ValueError("Обновите страницу анкеты и повторите попытку")
    incoming_fields = form.get("fields")
    if not isinstance(incoming_fields, dict) or len(incoming_fields) > 100:
        raise ValueError("Некорректный список полей")
    fields = {}
    for k, v in incoming_fields.items():
        if not isinstance(k, str) or len(k) > 100 or not k:
            raise ValueError("Некорректное название поля")
        fields[k] = _string(v)
    name = fields.get("Название бизнеса", "")
    contact_name = fields.get("Ответственный за запуск", "")
    contact = (fields.get("Контакт для настройки") or
               fields.get("Email для связи") or
               fields.get("Телефон для клиентов"))
    if not name or len(name) > 160:
        raise ValueError("Укажите название бизнеса (до 160 символов)")
    if not contact_name or not contact:
        raise ValueError("Укажите имя ответственного и контакт для связи")
    inn = fields.get("ИНН", "")
    if inn and not re.fullmatch(r"(?:\d{10}|\d{12})", inn):
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    applicant_email = fields.get("Email для связи", "")
    if applicant_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", applicant_email):
        raise ValueError("Проверьте email ответственного")

    rows = {}
    allowed_keys = {
        "services": {"name", "price", "duration", "category"},
        "staff": {"name", "role", "services", "hours"},
    }
    for kind, keys in allowed_keys.items():
        entries = form.get(kind)
        if not isinstance(entries, list) or len(entries) > 75:
            raise ValueError("Слишком много услуг или сотрудников")
        clean = []
        for item in entries:
            if not isinstance(item, dict) or set(item) - keys:
                raise ValueError("Некорректные сведения об услугах или сотрудниках")
            clean.append({k: _string(v, 500) for k, v in item.items()})
        rows[kind] = clean

    hours = form.get("hours")
    if not isinstance(hours, dict) or set(hours) - set(DAYS):
        raise ValueError("Некорректное расписание")
    clean_hours = {}
    for day, value in hours.items():
        if not isinstance(value, dict) or set(value) - {"start", "end", "off"}:
            raise ValueError("Некорректное расписание")
        start, end, off = value.get("start"), value.get("end"), value.get("off")
        if (not isinstance(start, str) or not re.fullmatch(r"\d\d:\d\d", start) or
            not isinstance(end, str) or not re.fullmatch(r"\d\d:\d\d", end) or
            not isinstance(off, bool)):
            raise ValueError("Проверьте часы работы")
        clean_hours[day] = {"start": start, "end": end, "off": off}

    done = form.get("done")
    if not isinstance(done, dict) or len(done) > 100 or any(
        not isinstance(k, str) or not re.fullmatch(r"t\d{1,3}", k) or not isinstance(v, bool)
        for k, v in done.items()
    ):
        raise ValueError("Некорректный чек-лист")

    choices = {}
    for k in ("resource_mode", "deposit_mode", "online_mode"):
        choices[k] = _string(form.get(k, ""), 160)
    clean_form = {
        "version": 1, "fields": fields, **rows, "hours": clean_hours,
        "done": done, **choices,
    }
    if len(json.dumps(clean_form, ensure_ascii=False).encode("utf-8")) > MAX_BODY - 4096:
        raise ValueError("Анкета слишком большая")
    return clean_form, name, client_key.lower()


def _no_store(response: web.StreamResponse):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _rate_allowed(request: web.Request, form: dict) -> bool:
    # Per-contact throttling avoids blocking every business behind one nginx proxy.
    # Never trust X-Forwarded-For from public callers.
    contact = (form["fields"].get("Контакт для настройки") or
               form["fields"].get("Email для связи") or
               form["fields"].get("Телефон для клиентов") or "")
    key = hashlib.sha256(((request.remote or "unknown") + ":" + contact.casefold()).encode()).hexdigest()
    now = time.monotonic()
    if len(_recent) > 4000:
        for host in list(_recent):
            _recent[host] = [v for v in _recent[host] if now - v < RATE_WINDOW]
            if not _recent[host]:
                del _recent[host]
    recent = [v for v in _recent[key] if now - v < RATE_WINDOW]
    if len(recent) >= RATE_LIMIT:
        _recent[key] = recent
        return False
    recent.append(now)
    _recent[key] = recent
    return True


def _email_body(application_id: str, payload: dict) -> str:
    fields = payload["fields"]
    public_base = os.getenv("ONBOARDING_PUBLIC_BASE_URL", "https://promo.servgo.ru").rstrip("/")
    # This is a deployment setting, never the untrusted Host header.
    if not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::\d{2,5})?", public_base):
        raise ValueError("ONBOARDING_PUBLIC_BASE_URL must be a trusted HTTPS origin")
    link = public_base + "/onboarding/request/" + application_id
    return "\n".join([
        "Новая анкета подключения бизнеса в ServGo",
        "",
        "Бизнес: " + fields.get("Название бизнеса", ""),
        "Ниша: " + fields.get("Сфера деятельности", "Не указана"),
        "ИНН: " + (fields.get("ИНН") or "Не указан"),
        "Контакт: " + fields.get("Ответственный за запуск", ""),
        "Связь: " + (fields.get("Контакт для настройки") or fields.get("Email для связи") or ""),
        "Услуг: " + str(len(payload["services"])),
        "Мастеров/залов: " + str(len(payload["staff"])),
        "",
        "Открыть полную анкету (только администратору ServGo):",
        link,
        "",
        "После открытия может потребоваться вход в ServGo.",
        "Анкета сохранена в базе, ссылка ведёт на её актуальную копию.",
    ])


def register_onboarding_routes(app, *, engine, get_user, admin_ids, send_email):
    """Register using existing BookingBot DB and shared ServGo authentication."""
    from pathlib import Path

    reviewers = set(int(v) for v in admin_ids)
    view_template = Path(__file__).parent / "templates" / "onboarding_request.html"
    app["onboarding_table_ready"] = False

    async def create_table(_app):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            _app["onboarding_table_ready"] = True
        except Exception:
            logger.exception("Onboarding table initialization failed")

    app.on_startup.append(create_table)

    async def submit(request):
        if request.content_type != "application/json":
            return _no_store(web.json_response({"error": "Нужен формат JSON"}, status=415))
        if request.content_length is not None and request.content_length > MAX_BODY:
            return _no_store(web.json_response({"error": "Анкета слишком большая"}, status=413))
        origin = request.headers.get("Origin")
        if origin and origin not in {"https://promo.servgo.ru", "https://servgo.ru"}:
            return _no_store(web.json_response({"error": "Недопустимый источник"}, status=403))
        if not request.app["onboarding_table_ready"]:
            return _no_store(web.json_response({"error": "Сохранение временно недоступно"}, status=503))
        raw = await request.content.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            return _no_store(web.json_response({"error": "Анкета слишком большая"}, status=413))
        try:
            data = json.loads(raw)
            form, business_name, client_key = validate_application(data)
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            return _no_store(web.json_response({"error": str(exc)}, status=400))
        if not _rate_allowed(request, form):
            return _no_store(web.json_response({"error": "Слишком много отправок. Попробуйте позднее"}, status=429))

        async with engine.connect() as conn:
            previous = (await conn.execute(select(applications).where(
                applications.c.client_key == client_key
            ))).mappings().first()
        if previous is not None:
            application_id = previous["id"]
            form = previous["payload"]
        else:
            application_id = uuid4().hex
            try:
                async with engine.begin() as conn:
                    await conn.execute(insert(applications).values(
                        id=application_id, client_key=client_key, business_name=business_name,
                        payload=form, email_state="pending",
                        submitted_at=datetime.now(timezone.utc),
                    ))
            except IntegrityError:
                async with engine.connect() as conn:
                    previous = (await conn.execute(select(applications).where(
                        applications.c.client_key == client_key
                    ))).mappings().first()
                if previous is None:
                    logger.exception("Application insert integrity error")
                    return _no_store(web.json_response({"error": "Сохранение не удалось"}, status=503))
                application_id, form = previous["id"], previous["payload"]
            except Exception:
                logger.exception("Unable to persist onboarding form")
                return _no_store(web.json_response({"error": "Сохранение временно недоступно"}, status=503))

        # Atomic claim prevents duplicate sends from two simultaneous POST requests.
        async with engine.begin() as conn:
            claimed = (await conn.execute(
                update(applications).where(
                    applications.c.id == application_id,
                    or_(
                        applications.c.email_state.in_(("pending", "failed")),
                        and_(
                            applications.c.email_state == "sending",
                            applications.c.email_attempted_at < datetime.now(timezone.utc) - timedelta(minutes=10),
                        ),
                    ),
                ).values(
                    email_state="sending", email_attempted_at=datetime.now(timezone.utc)
                ).returning(applications.c.id)
            )).scalar_one_or_none()
        email_sent = False
        if claimed:
            try:
                recipient = (os.getenv("ONBOARDING_NOTIFY_EMAIL") or
                             os.getenv("LANDING_REQUEST_EMAIL") or
                             "grunvaldlab@gmail.com")
                message = _email_body(application_id, form)
                email_sent = bool(await asyncio.to_thread(
                    send_email,
                    {"recipient": recipient, "email": form["fields"].get("Email для связи", "")},
                    subject="ServGo — новая анкета подключения бизнеса",
                    message_body=message,
                ))
            except Exception:
                logger.exception("Onboarding email notification failed for %s", application_id)
            async with engine.begin() as conn:
                await conn.execute(update(applications).where(
                    applications.c.id == application_id
                ).values(
                    email_state="sent" if email_sent else "failed",
                    emailed_at=datetime.now(timezone.utc) if email_sent else None,
                ))
        else:
            async with engine.connect() as conn:
                state = (await conn.execute(select(applications.c.email_state).where(
                    applications.c.id == application_id
                ))).scalar_one()
            email_sent = state == "sent"

        result = {
            "success": True, "saved": True, "email_sent": email_sent,
            "request_id": application_id[:8].upper(),
            "message": ("Анкета сохранена, уведомление отправлено" if email_sent
                        else "Анкета сохранена, но письмо ещё не отправлено. Повторите отправку позже."),
        }
        return _no_store(web.json_response(result, status=201 if email_sent else 202))

    async def _authorized(request):
        if not reviewers:
            return False
        user = await get_user(request)
        try:
            return bool(user) and int(user["id"]) in reviewers
        except (TypeError, KeyError, ValueError):
            return False

    async def view_page(request):
        if not await _authorized(request):
            return _no_store(web.Response(
                text='<html lang="ru"><meta charset="utf-8"><title>Доступ ограничен</title>'
                     '<body><h1>Доступ только администратору ServGo</h1>'
                     '<p>Войдите в ServGo под учётной записью администратора и откройте ссылку снова.</p>'
                     '<a href="https://servgo.ru/login">Войти в ServGo</a></body></html>',
                content_type="text/html", status=403,
            ))
        return _no_store(web.FileResponse(view_template))

    async def view_data(request):
        if not await _authorized(request):
            return _no_store(web.json_response({"error": "Нет доступа"}, status=403))
        if not request.app["onboarding_table_ready"]:
            return _no_store(web.json_response({"error": "Данные недоступны"}, status=503))
        application_id = request.match_info["application_id"]
        async with engine.connect() as conn:
            row = (await conn.execute(select(applications).where(
                applications.c.id == application_id
            ))).mappings().first()
        if row is None:
            return _no_store(web.json_response({"error": "Заявка не найдена"}, status=404))
        return _no_store(web.json_response({
            "request_id": row["id"][:8].upper(),
            "submitted_at": row["submitted_at"].isoformat(),
            "business_name": row["business_name"],
            "email_state": row["email_state"],
            "form": row["payload"],
        }))

    app.router.add_post("/api/onboarding/requests", submit)
    app.router.add_get("/api/onboarding/requests/{application_id:[0-9a-f]{32}}", view_data)
    app.router.add_get("/onboarding/request/{application_id:[0-9a-f]{32}}", view_page)
