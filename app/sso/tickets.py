"""Verify the one-time link from the ServGo admin and keep the studio session it opens.

The link is signed by ServGo with a shared secret, lives 60 seconds and works once. The studio
session is a separate signed cookie that carries only who the person is and which company it
opens; whether they still have access is asked from ServGo on every request (see access.py).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

COOKIE = "sg_sso"
MAX_AGE = 8 * 3600
AUDIENCE = "growth-studio"
MIN_SECRET_LENGTH = 32
MAX_TICKET_LIFETIME = 120  # ServGo issues 60 seconds; anything much longer is not from ServGo.
ROLES = {"owner", "co_owner", "administrator"}


class SsoError(Exception):
    """The link or the session is not valid. The message is safe to log, never to show secrets."""


@dataclass(frozen=True)
class Claims:
    user_id: int
    business_id: int
    role: str
    name: str
    jti: str
    expires: int


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def configured(settings) -> bool:
    return len(settings.servgo_sso_secret or "") >= MIN_SECRET_LENGTH and len(settings.servgo_internal_key or "") >= MIN_SECRET_LENGTH


def verify_ticket(settings, token: str | None, *, now: float | None = None) -> Claims:
    secret = settings.servgo_sso_secret or ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise SsoError("not configured")
    if not token or token.count(".") != 1 or len(token) > 2000:
        raise SsoError("malformed")
    payload, signature = token.split(".")
    expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise SsoError("bad signature")
    current = now if now is not None else time.time()
    try:
        data = json.loads(_unb64(payload))
        if data["v"] != 1 or data["aud"] != AUDIENCE:
            raise SsoError("wrong audience")
        claims = Claims(user_id=int(data["uid"]), business_id=int(data["biz"]), role=str(data["role"]),
                        name=str(data.get("name", ""))[:120], jti=str(data["jti"]), expires=int(data["exp"]))
    except SsoError:
        raise
    except (ValueError, KeyError, TypeError):
        raise SsoError("malformed") from None
    if claims.expires < current:
        raise SsoError("expired")
    if claims.expires - current > MAX_TICKET_LIFETIME:
        raise SsoError("lifetime too long")
    if claims.role not in ROLES or not 8 <= len(claims.jti) <= 64:
        raise SsoError("malformed")
    return claims


@dataclass(frozen=True)
class Session:
    user_id: int
    business_id: int
    product_id: str
    role: str


def _sign(settings, payload: str) -> str:
    from app.core.studio_sessions import signing_key
    # A prefix keeps these signatures different from the ordinary sign-in cookie signed with the same key.
    return _b64(hmac.new(signing_key(settings), b"sso:" + payload.encode(), hashlib.sha256).digest())


def issue_session(settings, claims: Claims, product_id: str, *, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": claims.user_id, "b": claims.business_id, "p": product_id, "r": claims.role,
                               "e": int((now if now is not None else time.time()) + MAX_AGE)},
                              separators=(",", ":")).encode())
    return f"{payload}.{_sign(settings, payload)}"


def read_session(settings, token: str | None, *, now: float | None = None) -> Session | None:
    if not token or token.count(".") != 1 or len(token) > 1000:
        return None
    payload, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(settings, payload)):
        return None
    try:
        data = json.loads(_unb64(payload))
        session = Session(int(data["u"]), int(data["b"]), str(data["p"]), str(data["r"]))
        expires = int(data["e"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires < (now if now is not None else time.time()) or session.role not in ROLES:
        return None
    return session
