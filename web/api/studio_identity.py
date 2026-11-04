"""Host-only browser identity proof shared by ServGo and Studio on servgo.ru.

This is NOT a replacement for the API bearer token. The signed, HttpOnly cookie
only binds an existing Studio SSO cookie to the browser's current ServGo login.
Every successful login rotates it; logout clears it. Never accept this cookie as
an API authentication credential.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Response, HTTPException

COOKIE = "sg_identity"
MAX_AGE = 8 * 3600
PURPOSE = b"servgo-browser-identity-v1:"


def _key() -> bytes:
    secret = os.getenv("STUDIO_SSO_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Studio sign-in is not configured")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue(user_id: int, *, now: int | None = None) -> str:
    current = int(time.time()) if now is None else now
    body = _b64(json.dumps({"v": 1, "u": user_id, "n": secrets.token_urlsafe(24),
                            "e": current + MAX_AGE}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), PURPOSE + body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def read(token: str | None, *, now: int | None = None) -> int | None:
    if not token or len(token) > 1024 or token.count(".") != 1:
        return None
    try:
        body, signature = token.split(".")
        valid = _b64(hmac.new(_key(), PURPOSE + body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, valid):
            return None
        data = json.loads(_unb64(body))
        current = int(time.time()) if now is None else now
        if (type(data.get("v")) is not int or data["v"] != 1 or
            type(data.get("u")) is not int or data["u"] <= 0 or
            type(data.get("e")) is not int or data["e"] < current or
            data["e"] > current + MAX_AGE or
            not isinstance(data.get("n"), str) or len(data["n"]) < 24):
            return None
        return data["u"]
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, AttributeError):
        return None


def _cookie_kwargs() -> dict:
    return {"path": "/", "httponly": True, "secure": True, "samesite": "lax"}


def set_identity(response: Response, user_id: int) -> None:
    # Authentication still works when Growth SSO is not configured.
    if len(os.getenv("STUDIO_SSO_SECRET", "")) < 32:
        return
    response.set_cookie(COOKIE, issue(int(user_id)), max_age=MAX_AGE, **_cookie_kwargs())


def ensure_identity(response: Response, user_id: int, existing: str | None) -> None:
    if len(os.getenv("STUDIO_SSO_SECRET", "")) < 32:
        return
    if read(existing) != user_id:
        set_identity(response, user_id)


def clear_identity(response: Response) -> None:
    response.delete_cookie(COOKIE, **_cookie_kwargs())
