"""Verified email/password authentication, independent of Telegram and phone claims."""

from datetime_utils import utc_now
import asyncio
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.services.email_notifications import email_channel_configured, send_email
from database.models import EmailIdentity, EmailChallenge, AuthRateBucket, User, Business, BusinessOwner, YandexIdentity
from email_utils import normalize_optional_email
from .. import deps
from ..database import get_db
from ..schemas import TokenOut, UserOut
from ..studio_identity import set_identity

router = APIRouter()


def configured():
    return email_channel_configured() and len(os.getenv("SERVGO_EMAIL_AUTH_SECRET", "")) >= 32 and len(deps.SECRET_KEY) >= 32


def require_config():
    if not configured():
        raise HTTPException(503, "Вход по email пока не настроен")


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        email = normalize_optional_email(value)
        if not email:
            raise ValueError("Укажите email")
        return email


class Begin(Credentials):
    purpose: Literal["register", "link", "reset"]
    full_name: str = Field(default="", max_length=255)


class Verify(BaseModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    code: str = Field(pattern=r"^\d{6}$")


def hash_password(password):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024).hex()
    return "scrypt$32768$8$3$" + salt + "$" + value


def verify_password(password, encoded):
    _, n, r, p, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024).hex()
    return hmac.compare_digest(actual, expected)


def digest(value):
    return hmac.new(os.environ["SERVGO_EMAIL_AUTH_SECRET"].encode(), value.encode(), hashlib.sha256).hexdigest()


async def rate_limit(db, key, limit, minutes):
    """Persistent counters serialize across API workers; store no raw email/IP."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    key = digest(key)
    now = utc_now()
    await db.execute(insert(AuthRateBucket).values(id=key, started_at=now, count=0).on_conflict_do_nothing())
    bucket = (await db.execute(select(AuthRateBucket).where(AuthRateBucket.id == key).with_for_update())).scalar_one()
    if bucket.started_at <= now - timedelta(minutes=minutes):
        bucket.started_at, bucket.count = now, 0
    bucket.count += 1
    if bucket.count > limit:
        await db.commit()
        raise HTTPException(429, "Слишком много попыток. Попробуйте позже")


async def yandex_account_for_email(db, email):
    for user_id in (await db.execute(select(YandexIdentity.user_id).where(YandexIdentity.email == email)
                                     .order_by(YandexIdentity.created_at))).scalars():
        if await db.scalar(select(EmailIdentity.email).where(EmailIdentity.user_id == user_id)) is None:
            return user_id
    return None


async def token_for(db, identity):
    user = await db.get(User, identity.user_id)
    owns = await db.scalar(select(Business.id).where(Business.owner_id == user.id).limit(1))
    co_owns = await db.scalar(select(BusinessOwner.id).where(BusinessOwner.user_id == user.id).limit(1))
    role = "admin" if owns or co_owns else "client"
    return TokenOut(token=deps.create_token(user.id, role, email_version=identity.session_version),
                    user=UserOut.model_validate(user), role=role)


@router.get("/email/capabilities")
async def capabilities():
    return {"available": configured()}


@router.get("/email/account")
async def account(user=Depends(deps.get_current_user), db=Depends(get_db)):
    identity = await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == user.id))
    from .telegram_link import bot_username
    return {"email": identity.email if identity else None, "available": configured(),
            "telegram": {"id": user.telegram_id, "username": user.username} if user.telegram_id > 0 else None,
            "telegram_link_available": bot_username() is not None}


@router.post("/email/start")
async def begin(body: Begin, request: Request, user=Depends(deps.get_current_user_optional), db=Depends(get_db)):
    require_config()
    if body.purpose == "link" and user is None:
        raise HTTPException(401, "Сначала войдите в свой аккаунт")
    if body.purpose == "register" and not body.full_name.strip():
        raise HTTPException(422, "Укажите имя")
    await rate_limit(db, "email-send-ip:" + request.client.host, 30, 60)
    await rate_limit(db, "email-send:" + body.email, 5, 60)
    identity = await db.get(EmailIdentity, body.email)
    if body.purpose == "link":
        current = await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == user.id))
        if current:
            await db.commit()
            raise HTTPException(409, "Email уже привязан. Для смены пароля используйте восстановление")
    challenge_id, code = str(uuid.uuid4()), f"{secrets.randbelow(1000000):06d}"
    # A request cannot claim ownership by matching a contact email or phone.
    target = user.id if body.purpose == "link" else (identity.user_id if identity and body.purpose == "reset" else None)
    challenge = EmailChallenge(id=challenge_id, email=body.email, purpose=body.purpose,
        user_id=target, full_name=body.full_name.strip(), password_hash=await asyncio.to_thread(hash_password, body.password),
        code_hash=digest(challenge_id + ":" + code), expires_at=utc_now() + timedelta(minutes=15))
    db.add(challenge)
    await db.commit()
    # Uniform response for unknown reset addresses and existing registration addresses.
    sent = await send_email(to_email=body.email, subject="Код подтверждения ServGo",
        text_body=f"Код: {code}\nДействителен 15 минут. Используйте его только в ServGo.\nЕсли вы не запрашивали вход или смену пароля, проигнорируйте письмо.")
    if not sent:
        challenge.consumed = True
        await db.commit()
        raise HTTPException(503, "Не удалось отправить письмо. Попробуйте позже")
    return {"challenge_id": challenge_id, "message": "Если действие доступно для этого адреса, код отправлен на почту"}


@router.post("/email/verify", response_model=TokenOut)
async def verify(body: Verify, response: Response, user=Depends(deps.get_current_user_optional), db=Depends(get_db)):
    require_config()
    # Serialize by email before locking individual codes, including reset races.
    email = await db.scalar(select(EmailChallenge.email).where(EmailChallenge.id == body.challenge_id))
    if email and db.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        key = int(digest("verify:" + email)[:16], 16) - 2**63
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    challenge = (await db.execute(select(EmailChallenge).where(EmailChallenge.id == body.challenge_id).with_for_update())).scalar_one_or_none()
    if not challenge or challenge.consumed or challenge.expires_at <= utc_now() or challenge.attempts >= 5:
        raise HTTPException(400, "Код недействителен или истёк")
    if challenge.purpose == "link" and (not user or user.id != challenge.user_id):
        raise HTTPException(403, "Привязку должен завершить тот же пользователь")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, digest(challenge.id + ":" + body.code)):
        await db.commit()
        raise HTTPException(400, "Неверный код")
    identity = (await db.execute(select(EmailIdentity).where(EmailIdentity.email == challenge.email).with_for_update())).scalar_one_or_none()
    if challenge.purpose == "reset":
        if not identity or identity.user_id != challenge.user_id:
            raise HTTPException(400, "Код недействителен")
        identity.password_hash = challenge.password_hash
        identity.session_version += 1
    else:
        if identity:
            challenge.consumed = True
            await db.commit()
            raise HTTPException(409, "Адрес уже используется. Войдите или восстановите пароль")
        if challenge.purpose == "register":
            # The address is now confirmed by a code. If Yandex confirmed the same address for a ServGo account
            # without an email login, this email login goes to that account instead of a new one.
            yandex_user = await yandex_account_for_email(db, challenge.email)
            if yandex_user is not None:
                user_id = yandex_user
                owner = await db.get(User, user_id)
                if owner is not None and not owner.email:
                    owner.email = challenge.email
            else:
                new_user = User(telegram_id=-secrets.randbelow(2**62 - 1) - 1, full_name=challenge.full_name, email=challenge.email)
                db.add(new_user)
                await db.flush()
                user_id = new_user.id
        else:
            user_id = user.id
            user.email = challenge.email
        identity = EmailIdentity(email=challenge.email, user_id=user_id, password_hash=challenge.password_hash)
        db.add(identity)
    challenge.consumed = True
    # Invalidate other outstanding codes, preventing reset/link/register races.
    await db.execute(update(EmailChallenge).where(EmailChallenge.email == challenge.email).values(consumed=True))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Адрес или аккаунт уже привязан") from None
    out = await token_for(db, identity)
    set_identity(response, out.user.id)
    return out


@router.post("/email/login", response_model=TokenOut)
async def login(body: Credentials, request: Request, response: Response, db=Depends(get_db)):
    require_config()
    await rate_limit(db, "login-ip:" + request.client.host, 60, 15)
    await rate_limit(db, "login:" + body.email, 10, 15)
    await db.commit()
    identity = await db.get(EmailIdentity, body.email)
    # Spend equal password-hash work for non-existent identities.
    encoded = identity.password_hash if identity else "scrypt$32768$8$3$" + "00" * 16 + "$" + "00" * 64
    valid = await asyncio.to_thread(verify_password, body.password, encoded)
    if not identity or not valid:
        raise HTTPException(401, "Неверный email или пароль")
    out = await token_for(db, identity)
    set_identity(response, out.user.id)
    return out
