"""Browser requests a link; signed bot updates prove Telegram ownership; browser finishes."""

from datetime_utils import utc_now
import asyncio
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update, text

from database.models import EmailIdentity, TelegramLinkRequest, User, Business, BusinessOwner
from database.account_link import AccountLinkError, link_token_hash, merge_email_into_telegram
from ..database import get_db
from ..deps import get_current_user
from .email_auth import verify_password, token_for, rate_limit, digest

router = APIRouter()


def bot_username():
    name = os.getenv("BOT_USERNAME", "").lstrip("@")
    return name if os.getenv("BOT_TOKEN") and re.fullmatch(r"[A-Za-z0-9_]{5,32}", name) else None


class PasswordIn(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class FinishIn(PasswordIn):
    challenge_id: str = Field(min_length=36, max_length=36)


async def lock_user(db, user_id):
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": (0x53474C4B << 32) + user_id})


async def email_reauth(db, user, password):
    if len(os.getenv("SERVGO_EMAIL_AUTH_SECRET", "")) < 32:
        raise HTTPException(503, "Привязка пока не настроена")
    await rate_limit(db, "telegram-link-password:" + str(user.id), 10, 15)
    await db.commit()
    identity = await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == user.id))
    if identity is None or not await asyncio.to_thread(verify_password, password, identity.password_hash):
        raise HTTPException(401, "Подтвердите пароль своего email-аккаунта")
    return identity


async def own_request(db, user, request_id, *, lock=False):
    query = select(TelegramLinkRequest).where(TelegramLinkRequest.id == request_id, TelegramLinkRequest.user_id == user.id)
    if lock: query = query.with_for_update()
    request = await db.scalar(query.execution_options(populate_existing=True))
    if request is None:
        raise HTTPException(404, "Запрос не найден")
    return request


@router.post("/telegram-link/start")
async def start(body: PasswordIn, user=Depends(get_current_user), db=Depends(get_db)):
    name = bot_username()
    if name is None:
        raise HTTPException(503, "Привязка Telegram пока не настроена")
    if user.telegram_id > 0:
        raise HTTPException(409, "Telegram уже привязан к этому аккаунту")
    identity = await email_reauth(db, user, body.password)
    await lock_user(db, user.id)
    await db.execute(update(TelegramLinkRequest).where(TelegramLinkRequest.user_id == user.id).values(consumed=True))
    raw_token = secrets.token_urlsafe(24)
    request = TelegramLinkRequest(id=str(uuid.uuid4()), user_id=user.id, token_hash=link_token_hash(raw_token),
        email_session_version=identity.session_version, expires_at=utc_now() + timedelta(minutes=10))
    db.add(request); await db.commit()
    return {"challenge_id": request.id, "url": f"https://t.me/{name}?start=account_{raw_token}", "expires_at": request.expires_at}


@router.get("/telegram-link/{request_id}")
async def status(request_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    request = await own_request(db, user, request_id)
    state = "expired" if request.expires_at <= utc_now() else "cancelled" if request.consumed else "telegram_confirmed" if request.telegram_confirmed else "waiting"
    result = {"state": state, "telegram": None, "business_count": 0}
    if state == "telegram_confirmed":
        target = await db.get(User, request.telegram_user_id)
        result["telegram"] = {"id": target.telegram_id, "name": target.full_name, "username": target.username}
        owned = set((await db.execute(select(Business.id).where(Business.owner_id == target.id))).scalars())
        owned.update((await db.execute(select(BusinessOwner.business_id).where(BusinessOwner.user_id == target.id))).scalars())
        result["business_count"] = len(owned)
    return result


@router.post("/telegram-link/complete")
async def complete(body: FinishIn, user=Depends(get_current_user), db=Depends(get_db)):
    identity = await email_reauth(db, user, body.password)
    # Match password-reset lock ordering before touching pending email codes.
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest("verify:" + identity.email)[:16], 16) - 2**63})
    await lock_user(db, user.id)
    request = await own_request(db, user, body.challenge_id, lock=True)
    try:
        identity = await merge_email_into_telegram(db, request, user.id)
    except AccountLinkError as exc:
        raise HTTPException(409, str(exc)) from None
    await db.commit()
    return await token_for(db, identity)


@router.post("/telegram-link/{request_id}/cancel")
async def cancel(request_id: str, user=Depends(get_current_user), db=Depends(get_db)):
    await lock_user(db, user.id)
    request = await own_request(db, user, request_id, lock=True)
    request.consumed = True
    await db.commit()
    return {"ok": True}
