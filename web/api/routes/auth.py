"""
Authentication routes.
Legacy phone login sends a code through the linked Telegram account.
Standalone email authentication is provided by email_auth.py.
"""
import os
import json
import random
import logging
import hmac
import hashlib
import httpx
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database.account_link import AccountLinkError, merge_yandex_account, phones_match
from database.models import EmailIdentity, YandexIdentity
from ..database import get_db, User, Business, BusinessOwner
from ..deps import create_token, get_current_user, get_current_user_optional
from ..studio_identity import set_identity, ensure_identity, clear_identity
from ..schemas import (
    LoginPhoneIn,
    VerifyCodeIn,
    TelegramAuthIn,
    TokenOut,
    UserOut,
)
from servgo_auth import (
    auth_cookie_kwargs,
    auth_cookie_name,
    auth_dict_from_payload,
    create_or_update_user as shared_create_or_update_user,
    create_session as shared_create_session,
    get_session as shared_get_session,
    verify_telegram_login,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

logger = logging.getLogger(__name__)
router = APIRouter()


class TelegramLoginWidgetIn(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str

# In-memory verification codes: phone -> (code, timestamp)
_pending_codes: Dict[str, Tuple[str, float]] = {}
CODE_TTL_SECONDS = 300  # 5 minutes


def _normalize_phone(phone: str) -> str:
    """Strip spaces/dashes, ensure starts with +."""
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if phone.startswith("8") and len(phone) == 11:
        phone = "+7" + phone[1:]
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


def _full_name_from_telegram(
    telegram_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
) -> str:
    full_name = " ".join(
        part.strip()
        for part in (first_name or "", last_name or "")
        if part and part.strip()
    ).strip()
    return full_name or username or f"Telegram {telegram_id}"


async def _find_or_create_user_from_telegram(
    db: AsyncSession,
    telegram_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
) -> User:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    full_name = _full_name_from_telegram(telegram_id, first_name, last_name, username)

    if user:
        changed = False
        if username is not None and user.username != username:
            user.username = username
            changed = True
        if full_name and full_name != f"Telegram {telegram_id}" and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)
        return user

    user = User(
        telegram_id=telegram_id,
        full_name=full_name,
        username=username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _join_yandex_account(db: AsyncSession, signed_in: Optional[User], target: User) -> User:
    """The browser was signed in to an account made by Yandex sign-in and has just proven a Telegram account
    with the same phone. The Yandex account (its bookings, businesses, Yandex login) joins the Telegram one.
    Anything unusual leaves the accounts separate and the Telegram sign-in still works."""
    if signed_in is None or signed_in.id == target.id or signed_in.telegram_id > 0 or target.telegram_id <= 0:
        return target
    identity = await db.scalar(select(YandexIdentity).where(YandexIdentity.user_id == signed_in.id))
    # Join only when the data match: the phone Yandex confirmed is this Telegram account's phone.
    if identity is None or not phones_match(identity.phone, target.phone):
        return target
    if await db.scalar(select(EmailIdentity.user_id).where(EmailIdentity.user_id == signed_in.id)) is not None:
        return target
    try:
        await merge_yandex_account(db, signed_in.id, target.id)
        await db.commit()
    except AccountLinkError as exc:
        await db.rollback()
        logger.info("yandex_join_skipped: %s", exc)
    await db.refresh(target)
    return target


async def _role_for_user(db: AsyncSession, user: User) -> str:
    """Determine role from business ownership.
    Checks legacy Business.owner_id and BusinessOwner junction.
    """
    result = await db.execute(
        select(Business).where(Business.owner_id == user.id).limit(1)
    )
    if result.scalar_one_or_none():
        return "admin"

    owner_result = await db.execute(
        select(BusinessOwner).where(BusinessOwner.user_id == user.id).limit(1)
    )
    if owner_result.scalar_one_or_none():
        return "admin"

    return "client"


async def _token_for_telegram_user(
    db: AsyncSession,
    telegram_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    signed_in: Optional[User] = None,
) -> TokenOut:
    user = await _find_or_create_user_from_telegram(
        db,
        telegram_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    user = await _join_yandex_account(db, signed_in, user)
    role = await _role_for_user(db, user)
    token = create_token(user.id, role=role)
    return TokenOut(
        token=token,
        user=UserOut.model_validate(user),
        role=role,
    )


async def _user_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
    """Several users may share a phone; prefer a real Telegram account, then the oldest."""
    result = await db.execute(
        select(User)
        .where(User.phone == phone)
        .order_by(case((User.telegram_id > 0, 0), else_=1), User.id)
        .limit(1)
    )
    return result.scalars().first()


# ---------- POST /auth/phone ----------

async def _send_code_via_telegram(phone: str, code: str, db: AsyncSession) -> bool:
    """Try to send verification code via Telegram bot. Returns True if sent."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not configured; Telegram OTP delivery is unavailable")
        return False
    try:
        user = await _user_by_phone(db, phone)
        if not user or not user.telegram_id or user.telegram_id < 0:
            return False
        text = f"🔐 Ваш код для входа на SochiService: <b>{code}</b>\n\nКод действителен 5 минут."
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": user.telegram_id,
                "text": text,
                "parse_mode": "HTML",
            })
            return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception as e:
        logger.warning("Failed to send code via Telegram: %s", e)
        return False


@router.post("/phone")
async def request_code(body: LoginPhoneIn, db: AsyncSession = Depends(get_db)):
    """Generate a 6-digit code and send via Telegram bot."""
    import time as time_module
    phone = _normalize_phone(body.phone)
    code = f"{random.randint(100000, 999999)}"
    _pending_codes[phone] = (code, time_module.time())
    sent = await _send_code_via_telegram(phone, code, db)
    if sent:
        return {"ok": True, "message": "Код отправлен в Telegram"}
    else:
        _pending_codes.pop(phone, None)
        raise HTTPException(503, "Код не доставлен в Telegram. Используйте вход по email")


# ---------- POST /auth/verify ----------

@router.post("/verify", response_model=TokenOut)
async def verify_code(
    body: VerifyCodeIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Verify the code, find-or-create user, return JWT with role from ownership."""
    import time as time_module
    phone = _normalize_phone(body.phone)
    entry = _pending_codes.get(phone)

    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    expected_code, created_at = entry
    if time_module.time() - created_at > CODE_TTL_SECONDS:
        _pending_codes.pop(phone, None)
        raise HTTPException(status_code=400, detail="Code expired. Request a new one.")

    if expected_code != body.code:
        raise HTTPException(status_code=400, detail="Invalid code")

    # Code used — remove it
    _pending_codes.pop(phone, None)

    # Find user by phone
    user = await _user_by_phone(db, phone)

    if not user:
        user = User(
            telegram_id=-int(time_module.time() * 1000) % 2_000_000_000,
            full_name=phone,
            phone=phone,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        # The code went to this account's Telegram: the person proved it.
        user = await _join_yandex_account(db, await get_current_user_optional(authorization, db), user)

    role = await _role_for_user(db, user)
    token = create_token(user.id, role=role)
    set_identity(response, user.id)
    return TokenOut(
        token=token,
        user=UserOut.model_validate(user),
        role=role,
    )


# ---------- POST /auth/telegram ----------

def _validate_init_data(init_data: str) -> Optional[dict]:
    """Validate Telegram WebApp initData and return user dict if valid."""
    try:
        parsed = parse_qs(init_data)
        data_check = {}
        for k, v in parsed.items():
            data_check[k] = v[0]

        check_hash = data_check.pop("hash", None)
        if not check_hash:
            return None

        sorted_items = sorted(data_check.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if computed != check_hash:
            logger.warning("initData hash mismatch")
            return None

        user_json = data_check.get("user")
        if user_json:
            return json.loads(user_json)
        return None
    except Exception as e:
        logger.warning("Failed to validate initData: %s", e)
        return None


def _shared_auth_from_init_data(init_data: str, tg_user: dict) -> Optional[dict]:
    parsed = parse_qs(init_data)
    flat = {key: value[0] for key, value in parsed.items() if value}
    if not flat.get("hash") or not flat.get("auth_date"):
        return None

    return auth_dict_from_payload(
        {
            "id": tg_user.get("id"),
            "first_name": tg_user.get("first_name"),
            "last_name": tg_user.get("last_name"),
            "username": tg_user.get("username"),
            "photo_url": tg_user.get("photo_url"),
            "auth_date": flat.get("auth_date"),
            "hash": flat.get("hash"),
        }
    )


async def _set_shared_auth_cookie(response: Response, auth_dict: dict) -> None:
    await run_in_threadpool(lambda: shared_create_or_update_user(auth_dict))
    shared_token = await run_in_threadpool(
        lambda: shared_create_session(int(auth_dict["id"]), source_service="booking")
    )
    response.set_cookie(auth_cookie_name(), shared_token, **auth_cookie_kwargs())


@router.post("/telegram-login", response_model=TokenOut)
async def telegram_login_widget(
    body: TelegramLoginWidgetIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Authenticate via Telegram Login Widget and issue both JWT and shared cookie."""
    auth_payload = body.model_dump(exclude_none=True)
    if not verify_telegram_login(auth_payload, [BOT_TOKEN]):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth payload")

    shared_auth = auth_dict_from_payload(auth_payload)
    token_out = await _token_for_telegram_user(
        db,
        int(shared_auth["id"]),
        first_name=shared_auth.get("first_name"),
        last_name=shared_auth.get("last_name"),
        username=shared_auth.get("username"),
        signed_in=await get_current_user_optional(authorization, db),
    )
    await _set_shared_auth_cookie(response, shared_auth)
    set_identity(response, token_out.user.id)
    return token_out


@router.get("/session", response_model=TokenOut)
async def session_from_shared_cookie(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid shared auth cookie for this SPA's JWT."""
    raw_token = request.cookies.get(auth_cookie_name())
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    shared_session = await run_in_threadpool(lambda: shared_get_session(raw_token))
    if not shared_session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    out = await _token_for_telegram_user(
        db,
        int(shared_session["telegram_id"]),
        first_name=shared_session.get("first_name"),
        last_name=shared_session.get("last_name"),
        username=shared_session.get("username"),
    )
    ensure_identity(response, out.user.id, request.cookies.get("sg_identity"))
    return out


@router.post("/telegram", response_model=TokenOut)
async def telegram_auth(
    body: TelegramAuthIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Authenticate via Telegram WebApp initData. Auto-creates user if needed."""
    tg_user = _validate_init_data(body.init_data)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")

    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="No user id in initData")

    token_out = await _token_for_telegram_user(
        db,
        int(telegram_id),
        first_name=tg_user.get("first_name"),
        last_name=tg_user.get("last_name"),
        username=tg_user.get("username"),
        signed_in=await get_current_user_optional(authorization, db),
    )
    shared_auth = _shared_auth_from_init_data(body.init_data, tg_user)
    if shared_auth:
        await _set_shared_auth_cookie(response, shared_auth)
    set_identity(response, token_out.user.id)
    return token_out


# ---------- POST /auth/logout ----------

@router.post("/logout")
async def logout(response: Response):
    """End the browser's shared Telegram session as well as its local JWT."""
    cookie_options = auth_cookie_kwargs()
    response.delete_cookie(
        auth_cookie_name(),
        path=cookie_options.get("path", "/"),
        domain=cookie_options.get("domain"),
    )
    clear_identity(response)
    return {"ok": True}


# ---------- GET /auth/me ----------

@router.get("/me", response_model=UserOut)
async def me(request: Request, response: Response, user: User = Depends(get_current_user)):
    """Return the current authenticated user and hydrate an older active login."""
    ensure_identity(response, user.id, request.cookies.get("sg_identity"))
    return UserOut.model_validate(user)
