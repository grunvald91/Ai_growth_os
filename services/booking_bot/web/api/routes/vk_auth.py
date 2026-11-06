"""Unified VK ID login for servgo.ru and the existing Studio SSO.

OAuth code and access token remain server-side. The one-time ticket is
browser-bound and contains only verified VK subject and display name.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.account_link import AccountLinkError, merge_vk_account
from database.models import User, VkIdentity, VkLoginTicket
from datetime_utils import utc_now
from .. import deps
from ..database import get_db
from ..schemas import TokenOut, UserOut
from ..studio_identity import set_identity
from .auth import _role_for_user

router = APIRouter()
logger = logging.getLogger(__name__)

VK_AUTHORIZE = "https://id.vk.ru/authorize"
VK_EXCHANGE = "https://id.vk.ru/oauth2/auth"
VK_INFO = "https://id.vk.ru/oauth2/user_info"
REDIRECT = "https://servgo.ru/api/auth/vk/callback"
FLOW_COOKIE = "servgo_vk_flow"
TICKET_COOKIE = "servgo_vk_ticket"
COOKIE_PATH = "/api/auth/vk"
FLOW_SECONDS = 600


def app_id() -> str:
    return os.getenv("SERVGO_VK_ID_APP_ID", "").strip()


def configured() -> bool:
    return (bool(re.fullmatch(r"[1-9]\d{3,20}", app_id()))
            and len(deps.SECRET_KEY) >= 32
            and os.getenv("SERVGO_VK_ID_REDIRECT_URI", REDIRECT).strip() == REDIRECT)


def _mac(data: str) -> str:
    return hmac.new(("servgo-vk-login:" + deps.SECRET_KEY).encode(),
                    data.encode(), hashlib.sha256).hexdigest()


def _seal(data: dict) -> str:
    raw = base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode("utf-8")).decode().rstrip("=")
    return raw + "." + _mac(raw)


def _unseal(cookie: str | None) -> dict | None:
    if not cookie or cookie.count(".") != 1 or len(cookie) > 2048:
        return None
    raw, signature = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_mac(raw), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, UnicodeError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), (int, float)):
        return None
    return data if data["exp"] > utc_now().timestamp() else None


def _ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def start_flow(response: Response, link_user_id: int | None = None) -> str:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response.set_cookie(FLOW_COOKIE, _seal({
        "state": state, "verifier": verifier, "user_id": link_user_id,
        "exp": int(utc_now().timestamp()) + FLOW_SECONDS,
    }), max_age=FLOW_SECONDS, path=COOKIE_PATH, httponly=True,
        secure=True, samesite="lax")
    return VK_AUTHORIZE + "?" + urlencode({
        "client_id": app_id(), "app_id": app_id(),
        "response_type": "code", "redirect_uri": REDIRECT,
        "state": state, "code_challenge": challenge,
        "code_challenge_method": "s256", "scope": "email phone",
    })


def _back(destination: str) -> RedirectResponse:
    response = RedirectResponse(destination, status_code=302)
    response.delete_cookie(FLOW_COOKIE, path=COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def fetch_vk_profile(code: str, device_id: str, state: str, verifier: str) -> dict:
    """Follow VK ID SDK's official OAuth2 exchange and user_info protocol."""
    if (not code or len(code) > 4000
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", device_id)):
        raise ValueError("VK ID не вернул корректный код и device_id")
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        token = await client.post(VK_EXCHANGE, params={
            "grant_type": "authorization_code", "client_id": app_id(),
            "redirect_uri": REDIRECT, "code_verifier": verifier,
            "state": state, "device_id": device_id,
        }, data={"code": code})
        token.raise_for_status()
        result = token.json()
        if not isinstance(result, dict) or result.get("state") != state:
            raise ValueError("VK ID вернул неподтверждённый state")
        access = result.get("access_token")
        if not isinstance(access, str) or not access:
            raise ValueError("VK ID не предоставил access_token")
        info = await client.post(VK_INFO, params={"client_id": app_id()},
                                 data={"access_token": access})
        info.raise_for_status()
        data = info.json()
    if not isinstance(data, dict) or not isinstance(data.get("user"), dict):
        raise ValueError("VK ID не вернул данные пользователя")
    user = data["user"]
    subject = str(user.get("user_id") or "")
    if not re.fullmatch(r"[1-9]\d{0,39}", subject):
        raise ValueError("VK ID не предоставил корректный идентификатор пользователя")
    first = user.get("first_name") if isinstance(user.get("first_name"), str) else ""
    last = user.get("last_name") if isinstance(user.get("last_name"), str) else ""
    name = " ".join(part.strip() for part in (first, last) if part.strip())[:255]
    # VK email / phone are NOT used to grant access to an existing ServGo account.
    return {"id": subject, "name": name or "Пользователь VK"}


@router.get("/vk/capabilities")
async def capabilities():
    return {"available": configured()}


@router.get("/vk/start")
async def start():
    if not configured():
        return RedirectResponse("/login?vk=unavailable", status_code=302)
    response = RedirectResponse("/", status_code=302)
    response.headers["Location"] = start_flow(response)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/vk/link/start")
async def link_start(response: Response, user: User = Depends(deps.get_current_user)):
    if not configured():
        raise HTTPException(503, "VK ID пока не настроен")
    return {"url": start_flow(response, user.id)}


@router.get("/vk/account")
async def account(user: User = Depends(deps.get_current_user),
                  db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(VkIdentity).where(VkIdentity.user_id == user.id))
    return {"available": configured(), "linked": bool(row),
            "name": row.display_name if row else None}


@router.get("/vk/callback")
async def callback(request: Request, code: str = "", device_id: str = "",
                   state: str = "", error: str = "", db: AsyncSession = Depends(get_db)):
    flow = _unseal(request.cookies.get(FLOW_COOKIE))
    if not configured() or not flow or not state or not hmac.compare_digest(
            str(flow.get("state", "")), state):
        return _back("/login?vk=expired")
    if error or not code:
        return _back("/login?vk=denied")
    try:
        profile = await fetch_vk_profile(code, device_id, state, str(flow["verifier"]))
    except Exception as exc:
        logger.warning("vk_id_login_failed: %s", type(exc).__name__)
        return _back("/login?vk=failed")
    await db.execute(delete(VkLoginTicket).where(VkLoginTicket.expires_at < utc_now()))
    ticket = secrets.token_urlsafe(32)
    db.add(VkLoginTicket(id=_ticket_hash(ticket),
                         payload=json.dumps(profile, ensure_ascii=False),
                         link_user_id=flow.get("user_id"),
                         expires_at=utc_now() + timedelta(seconds=FLOW_SECONDS)))
    await db.commit()
    response = _back("/login/vk#ticket=" + ticket)
    response.set_cookie(TICKET_COOKIE, _mac("browser:" + _ticket_hash(ticket)),
                        max_age=FLOW_SECONDS, path=COOKIE_PATH,
                        httponly=True, secure=True, samesite="strict")
    return response


class ExchangeInput(BaseModel):
    ticket: str = Field(min_length=20, max_length=80)
    merge: bool = False


async def _issue(db: AsyncSession, user: User, response: Response) -> dict:
    role = await _role_for_user(db, user)
    token = TokenOut(token=deps.create_token(user.id, role=role),
                     user=UserOut.model_validate(user), role=role)
    set_identity(response, user.id)
    return {"status": "ok", **token.model_dump()}


async def _only_vk(db: AsyncSession, user: User) -> bool:
    from database.models import EmailIdentity, YandexIdentity
    if user.telegram_id > 0:
        return False
    if await db.scalar(select(EmailIdentity.user_id).where(EmailIdentity.user_id == user.id)):
        return False
    if await db.scalar(select(YandexIdentity.user_id).where(YandexIdentity.user_id == user.id)):
        return False
    return await db.scalar(select(VkIdentity.vk_user_id).where(VkIdentity.user_id == user.id)) is not None


@router.post("/vk/exchange")
async def exchange(body: ExchangeInput, request: Request, response: Response,
                   user: Optional[User] = Depends(deps.get_current_user_optional),
                   db: AsyncSession = Depends(get_db)):
    expected = _mac("browser:" + _ticket_hash(body.ticket))
    if not hmac.compare_digest(request.cookies.get(TICKET_COOKIE) or "", expected):
        raise HTTPException(400, "VK-вход открыт в другом браузере. Начните заново")
    ticket = await db.scalar(select(VkLoginTicket).where(
        VkLoginTicket.id == _ticket_hash(body.ticket)).with_for_update())
    if ticket is None or ticket.consumed or ticket.expires_at <= utc_now():
        raise HTTPException(400, "Одноразовая ссылка VK ID уже использована или устарела")
    profile = json.loads(ticket.payload)
    existing = await db.scalar(select(VkIdentity).where(VkIdentity.vk_user_id == profile["id"]))
    # Linking is bound to the signed-in ServGo user from the original /link/start.
    if ticket.link_user_id is not None:
        if user is None or user.id != ticket.link_user_id:
            raise HTTPException(401, "Сначала войдите в тот аккаунт ServGo, где начали привязку")
        own = await db.scalar(select(VkIdentity.vk_user_id).where(VkIdentity.user_id == user.id))
        if existing and existing.user_id == user.id:
            raise HTTPException(409, "Этот VK ID уже привязан")
        if own is not None:
            raise HTTPException(409, "К вашему аккаунту уже привязан другой VK ID")
        if existing is not None:
            source = await db.get(User, existing.user_id)
            if source is None or not await _only_vk(db, source):
                raise HTTPException(409, "VK ID уже связан с другим аккаунтом с дополнительными способами входа")
            if not body.merge:
                return {"status": "merge_required"}
            try:
                await merge_vk_account(db, source.id, user.id)
            except AccountLinkError as exc:
                await db.rollback()
                raise HTTPException(409, str(exc)) from None
            ticket.consumed = True
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise HTTPException(409, "Привязка уже изменилась. Начните снова") from None
            return await _issue(db, user, response)
        db.add(VkIdentity(vk_user_id=profile["id"], user_id=user.id,
                          display_name=profile["name"]))
        ticket.consumed = True
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "VK ID уже привязан в другом окне") from None
        return {"status": "linked"}

    if existing is not None:
        owner = await db.get(User, existing.user_id)
        if owner is None:
            raise HTTPException(404, "Аккаунт ServGo не найден")
        existing.display_name = profile["name"]
        ticket.consumed = True
        await db.commit()
        return await _issue(db, owner, response)

    # No silent identity merge based on names, email addresses or phone fields.
    account = User(telegram_id=-secrets.randbelow(2**62 - 1) - 1,
                   full_name=profile["name"])
    db.add(account)
    await db.flush()
    db.add(VkIdentity(vk_user_id=profile["id"], user_id=account.id,
                      display_name=profile["name"]))
    ticket.consumed = True
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Вход уже выполнен в другом окне. Начните снова") from None
    return await _issue(db, account, response)
