"""ServGo sales chat with tenant-scoped SQL history and atomic booking confirmation."""
from __future__ import annotations

from datetime_utils import utc_now

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from database.models import SalesConversation, BusinessOwner, Booking
from ..database import get_db, User, Business
from ..deps import get_current_user
from ..schemas import AIChatIn, AIChatOut, AIConfirmIn
from ..sales.engine import chat, response, handoff
from ..sales.provider import ClaudeHubClient, ProviderError
from ..sales.state import load_thread, public_pending, public_slot_offer, trim_history, visible_history
from ..sales.tools import ServGoTools, BookingArgs
from ..sales.policy import policy_for, policy_document, PolicyUpdate

router = APIRouter()
COOKIE_NAME = "servgo_sales_session"


@dataclass
class SalesIdentity:
    principal: str
    user: User | None = None


def _validate_init_data(init_data: str) -> dict | None:
    token = os.getenv("BOT_TOKEN", "")
    if not token or len(init_data) > 10000:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
        parsed = dict(pairs)
        if len(parsed) != len(pairs):
            return None
        check_hash = parsed.pop("hash")
        age = time.time() - int(parsed["auth_date"])
        if age < -60 or age > 86400:
            return None
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, check_hash):
            return None
        user = json.loads(parsed["user"])
        return user if isinstance(user.get("id"), int) and user["id"] > 0 else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def get_sales_identity(request: Request, response: Response, db=Depends(get_db)):
    authorization = request.headers.get("Authorization")
    if authorization:
        try:
            user = await get_current_user(authorization, db)
            return SalesIdentity(f"user:{user.id}", user)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            # The sales chat explicitly supports guests. A stale browser JWT
            # must not make its public endpoints unusable. Do NOT reuse the
            # previous user's conversation or identity on failed validation.
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        telegram = _validate_init_data(init_data)
        if telegram is None:
            raise HTTPException(401, "Invalid or expired Telegram session")
        user = (await db.execute(select(User).where(User.telegram_id == telegram["id"]))).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram["id"], full_name=" ".join(filter(None, [
                telegram.get("first_name"), telegram.get("last_name")])) or "Клиент")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return SalesIdentity(f"user:{user.id}", user)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if len(cookie) != 43 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in cookie):
        cookie = secrets.token_urlsafe(32)
        response.set_cookie(COOKIE_NAME, cookie, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", max_age=30 * 86400)
    return SalesIdentity("guest:" + hashlib.sha256(cookie.encode()).hexdigest())


async def active_business(db, business_id):
    business = (await db.execute(select(Business).where(
        Business.id == business_id, Business.is_active.is_(True)))).scalar_one_or_none()
    if not business:
        raise HTTPException(404, "Компания не найдена")
    return business


@router.post("/ai/chat", response_model=AIChatOut)
async def ai_chat(body: AIChatIn, identity=Depends(get_sales_identity), db=Depends(get_db)):
    if not body.message.strip():
        raise HTTPException(422, "Сообщение не должно быть пустым")
    if body.business_id is None:
        return AIChatOut(reply="Выберите компанию, чтобы я подобрал услугу и проверил время записи.")
    business = await active_business(db, body.business_id)
    conversation = await load_thread(db, business.id, identity.principal)
    provider = None
    try:
        provider = ClaudeHubClient() if conversation.mode == "ai" and policy_for(business).enabled else None
        result = await chat(provider, ServGoTools(db, business, identity.user), conversation, body.message.strip())
        conversation.updated_at = utc_now()
        await db.commit()
        return result
    except ProviderError as exc:
        await db.rollback()
        raise HTTPException(503, str(exc)) from exc
    finally:
        # One pooled connection per chat turn, never shared across customers.
        if provider is not None and hasattr(provider, "aclose"):
            await provider.aclose()


@router.get("/ai/history")
async def ai_history(business_id: int = Query(ge=1), identity=Depends(get_sales_identity), db=Depends(get_db)):
    business = await active_business(db, business_id)
    conversation = await load_thread(db, business_id, identity.principal, create=False)
    if not conversation:
        return {"messages": [], "mode": "ai", "pending_action": None}
    return {"conversation_id": conversation.id, "messages": visible_history(conversation),
            "stage": conversation.stage, "mode": conversation.mode,
            "pending_action": public_pending(conversation) if policy_for(business).allows("create_booking") else None,
            "available_slots": public_slot_offer(conversation) if policy_for(business).allows("create_booking") else None,
            "booking_url": (conversation.action or {}).get("result", {}).get("booking_url")}


class SelectSlotIn(BaseModel):
    business_id: int = Field(ge=1)
    employee_id: int = Field(gt=0)
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


@router.post("/ai/select-slot", response_model=AIChatOut)
async def ai_select_slot(body: SelectSlotIn, identity=Depends(get_sales_identity), db=Depends(get_db)):
    # Only the signed-in/guest owner of a live server-created offer may select a time.
    business = await active_business(db, body.business_id)
    policy_for(business).require("create_booking")
    conversation = await load_thread(db, body.business_id, identity.principal, create=False)
    if not conversation or conversation.mode != "ai":
        raise HTTPException(409, "Попросите ИИ показать доступное время")
    offer = public_slot_offer(conversation)
    if not offer:
        raise HTTPException(409, "Предложенные окна устарели. Проверьте время ещё раз")
    slot = next((item for item in offer["slots"]
                 if item["time"] == body.time and item["employee_id"] == body.employee_id), None)
    if slot is None:
        raise HTTPException(409, "Это время не было предложено. Проверьте окна снова")
    tools = ServGoTools(db, business, identity.user)
    prepared = await tools.prepare_booking(BookingArgs(
        service_id=offer["service_id"], employee_id=body.employee_id,
        date=offer["date"], time=body.time, solo=offer["solo"]))
    conversation.action_id = str(uuid.uuid4())
    conversation.action = {**prepared, "status": "pending",
        "expires_at": (utc_now() + timedelta(minutes=20)).isoformat()}
    conversation.stage = "closing"
    conversation.facts = {k: v for k, v in conversation.facts.items() if k != "_slot_offer"}
    reply = "Отличный выбор! Проверьте детали и подтвердите запись в карточке ниже 😊"
    conversation.messages = trim_history([*conversation.messages,
        {"role": "user", "content": f"Выбираю {date.fromisoformat(offer['date']).strftime('%d.%m')} в {body.time}, {slot['employee_name']}."},
        {"role": "assistant", "content": reply}])
    conversation.updated_at = utc_now()
    result = response(conversation, reply)
    await db.commit()
    return result


class HandoffRequest(BaseModel):
    business_id: int = Field(ge=1)


@router.post("/ai/handoff", response_model=AIChatOut)
async def ai_handoff(body: HandoffRequest, identity=Depends(get_sales_identity), db=Depends(get_db)):
    business = await active_business(db, body.business_id)
    conversation = await load_thread(db, business.id, identity.principal)
    reason = "Клиент нажал «Позвать оператора»"
    handoff(conversation, reason)
    reply = "Диалог передан оператору. Его ответ появится здесь."
    conversation.messages = [*conversation.messages, {"role": "user", "content": reason},
        {"role": "assistant", "content": reply}]
    conversation.updated_at = utc_now()
    await db.commit()
    return response(conversation, reply)


async def owned_action(db, identity, action_id):
    business_id = (await db.execute(select(SalesConversation.business_id).where(
        SalesConversation.action_id == action_id,
        SalesConversation.principal == identity.principal))).scalar_one_or_none()
    if business_id is None:
        raise HTTPException(404, "Подтверждение не найдено или относится к другому диалогу")
    conversation = await load_thread(db, business_id, identity.principal, create=False)
    if not conversation or conversation.action_id != action_id or not conversation.action:
        raise HTTPException(404, "Подтверждение уже заменено")
    return conversation


@router.post("/ai/confirm", response_model=AIChatOut)
async def ai_confirm(body: AIConfirmIn, identity=Depends(get_sales_identity), db=Depends(get_db)):
    conversation = await owned_action(db, identity, body.action_id)
    action = conversation.action
    if action["status"] == "completed":
        return AIChatOut.model_validate(action["result"])
    if action["status"] != "pending" or conversation.mode != "ai":
        raise HTTPException(409, "Это действие больше не ожидает подтверждения")
    if datetime.fromisoformat(action["expires_at"]) <= utc_now():
        raise HTTPException(410, "Подтверждение истекло. Запросите новую карточку записи")
    # Serialize permission revocation with booking commit.
    await db.execute(select(Business.id).where(Business.id == conversation.business_id).with_for_update())
    business = await active_business(db, conversation.business_id)
    await db.refresh(business)
    policy_for(business).require("create_booking")
    tools = ServGoTools(db, business, identity.user)
    ids, booking_url = await tools.book(action, body)
    reply = "Запись создана. " + action["description"] + ". Статус и дальнейшие шаги — на странице записи."
    conversation.stage = "booked"
    history = list(conversation.messages)
    history.append({"role": "assistant", "content": reply})
    conversation.messages = history
    conversation.action = {**action, "status": "completed"}
    conversation.updated_at = utc_now()
    result = response(conversation, reply, booking_ids=ids, booking_url=booking_url)
    conversation.action = {**conversation.action, "result": result.model_dump(mode="json")}
    # Booking INSERT and confirmation receipt commit together. Advisory/row locks
    # stay held throughout; retries and parallel double-clicks return this receipt.
    await db.commit()
    # Reuse ServGo's existing mandatory post-commit events; notification errors
    # cannot make the caller retry an already committed reservation.
    from ..routes.client import notify_created_visit
    bookings = (await db.execute(select(Booking).where(Booking.id.in_(ids)))).scalars().all()
    # Площадки и услуги — одна запись и одни уведомления, как со страницы записи.
    await notify_created_visit(bookings, db, business, identity.user.email if identity.user else None)
    return result


@router.post("/ai/reject")
async def ai_reject(body: AIConfirmIn, identity=Depends(get_sales_identity), db=Depends(get_db)):
    conversation = await owned_action(db, identity, body.action_id)
    action = conversation.action
    if action["status"] == "completed":
        raise HTTPException(409, "Запись уже создана; отменить её можно на странице записи")
    conversation.action = {**action, "status": "rejected"}
    conversation.messages = [*conversation.messages,
        {"role": "user", "content": "Я отказался от предложенной карточки записи."}]
    await db.commit()
    return {"message": "Действие отменено"}


@router.post("/ai/clear")
async def ai_clear(business_id: int = Query(ge=1), identity=Depends(get_sales_identity), db=Depends(get_db)):
    conversation = await load_thread(db, business_id, identity.principal, create=False)
    if conversation:
        if conversation.mode == "human":
            raise HTTPException(409, "Диалог передан оператору; дождитесь его ответа")
        conversation.messages, conversation.facts = [], {}
        conversation.action_id, conversation.action = None, None
        conversation.stage = "needs_analysis"
        await db.commit()
    return {"ok": True}


async def require_owner(db, business_id, user):
    business = await db.get(Business, business_id)
    membership = (await db.execute(select(BusinessOwner.id).where(
        BusinessOwner.business_id == business_id, BusinessOwner.user_id == user.id))).first()
    if not business or (business.owner_id != user.id and not membership):
        raise HTTPException(403, "Нет доступа к диалогам этой компании")


@router.get("/admin/ai/businesses/{business_id}/conversations")
async def operator_inbox(business_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    await require_owner(db, business_id, user)
    conversations = (await db.execute(select(SalesConversation).where(
        SalesConversation.business_id == business_id, SalesConversation.mode == "human")
        .order_by(SalesConversation.updated_at.desc()).limit(50))).scalars().all()
    return [{"id": c.id, "reason": c.handoff_reason, "messages": visible_history(c),
             "updated_at": c.updated_at.isoformat()} for c in conversations]


class OperatorMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


async def operator_thread(db, conversation_id, user):
    candidate = await db.get(SalesConversation, conversation_id)
    if not candidate:
        raise HTTPException(404, "Диалог не найден")
    await require_owner(db, candidate.business_id, user)
    return await load_thread(db, candidate.business_id, candidate.principal, create=False)


@router.post("/admin/ai/conversations/{conversation_id}/reply")
async def operator_reply(conversation_id: str, body: OperatorMessage,
                         user=Depends(get_current_user), db=Depends(get_db)):
    if not body.message.strip():
        raise HTTPException(422, "Введите сообщение")
    conversation = await operator_thread(db, conversation_id, user)
    if conversation.mode != "human":
        raise HTTPException(409, "Диалог уже возвращён ИИ")
    conversation.messages = [*conversation.messages, {"role": "assistant", "content": body.message.strip()}]
    conversation.updated_at = utc_now()
    await db.commit()
    return {"ok": True}


@router.post("/admin/ai/conversations/{conversation_id}/resume")
async def operator_resume(conversation_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    conversation = await operator_thread(db, conversation_id, user)
    conversation.mode, conversation.stage, conversation.handoff_reason = "ai", "needs_analysis", None
    await db.commit()
    return {"ok": True}


async def write_policy(db, business_id, body: PolicyUpdate):
    business = (await db.execute(select(Business).where(Business.id == business_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not business:
        raise HTTPException(404, "Компания не найдена")
    revision = (business.sales_agent_settings or {}).get("revision", 0)
    if body.expected_revision != revision:
        raise HTTPException(409, "Настройки уже изменены. Обновите страницу перед сохранением")
    business.sales_agent_settings = {"revision": revision + 1, "policy": body.policy.model_dump()}
    await db.commit()
    return policy_document(business)


@router.get("/admin/ai/businesses/{business_id}/settings")
async def owner_settings(business_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    await require_owner(db, business_id, user)
    return policy_document(await db.get(Business, business_id))


@router.put("/admin/ai/businesses/{business_id}/settings")
async def owner_update_settings(business_id: int, body: PolicyUpdate,
                                user=Depends(get_current_user), db=Depends(get_db)):
    await require_owner(db, business_id, user)
    return await write_policy(db, business_id, body)


def platform_scope(request: Request) -> set[int]:
    token = os.getenv("SERVGO_PLATFORM_API_TOKEN", "")
    if len(token) < 32 or not hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + token):
        raise HTTPException(401, "Интеграция платформы не авторизована")
    try:
        entries = os.getenv("SERVGO_PLATFORM_BUSINESS_IDS", "").split(",")
        allowed = {int(value.strip()) for value in entries if value.strip()}
        if any(value <= 0 for value in allowed):
            raise ValueError("Invalid scope")
        return allowed
    except ValueError:
        raise HTTPException(503, "Область доступа интеграции не настроена")


@router.get("/platform/sales/businesses")
async def platform_businesses(scope=Depends(platform_scope), db=Depends(get_db)):
    rows = (await db.execute(select(Business).where(Business.id.in_(scope), Business.is_active.is_(True))
        .order_by(Business.name))).scalars().all()
    return [{"id": b.id, "name": b.name} for b in rows]


@router.get("/platform/sales/businesses/{business_id}/settings")
async def platform_settings(business_id: int, scope=Depends(platform_scope), db=Depends(get_db)):
    if business_id not in scope:
        raise HTTPException(403, "Компания не включена в доступ интеграции")
    return policy_document(await active_business(db, business_id))


@router.put("/platform/sales/businesses/{business_id}/settings")
async def platform_update_settings(business_id: int, body: PolicyUpdate,
                                   scope=Depends(platform_scope), db=Depends(get_db)):
    if business_id not in scope:
        raise HTTPException(403, "Компания не включена в доступ интеграции")
    await active_business(db, business_id)
    return await write_policy(db, business_id, body)
