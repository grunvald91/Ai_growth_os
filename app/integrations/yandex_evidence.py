"""Short-lived, company-scoped proof that a directory card came from Yandex.

Signing the lookup result avoids trusting a hidden form field and avoids
repeating a paid API request when the owner selects a result.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import get_settings

_DEV_SIGNING_KEY = secrets.token_bytes(32)
_TTL_SECONDS = 30 * 60
_SAFE_FIELDS = ("id", "name", "address", "website", "categories", "phones", "maps_url")


def _key() -> bytes:
    settings = get_settings()
    secret = settings.studio_session_secret or settings.servgo_internal_key
    if secret:
        return secret.encode("utf-8")
    # Developer loopback only; production should always have SSO secrets.
    if settings.app_env.casefold() == "production":
        raise ValueError("Yandex selection signing secret is not configured")
    return _DEV_SIGNING_KEY


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_organization(item: dict, *, scope: str, query: str, city: str) -> str:
    organization = {name: item.get(name) for name in _SAFE_FIELDS}
    raw = json.dumps({
        "v": 1, "scope": scope, "query": query.strip(), "city": city.strip(),
        "iat": int(time.time()), "org": organization,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = _b64(raw)
    signature = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    return payload + "." + signature


def verify_organization(token: str, *, scope: str, query: str | None = None,
                        city: str | None = None) -> dict:
    """Only return canonical provider fields after full HMAC and scope validation."""
    try:
        if not isinstance(token, str) or not 10 < len(token) <= 12000:
            raise ValueError()
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        content = json.loads(_decode(payload))
        now = int(time.time())
        if not isinstance(content, dict) or content.get("v") != 1 or content.get("scope") != scope:
            raise ValueError()
        issued = content.get("iat")
        if type(issued) is not int or issued > now + 30 or issued < now - _TTL_SECONDS:
            raise ValueError()
        if query is not None and content.get("query") != query.strip():
            raise ValueError()
        if city is not None and content.get("city") != city.strip():
            raise ValueError()
        org = content.get("org")
        if not isinstance(org, dict) or not isinstance(org.get("name"), str):
            raise ValueError()
        if not isinstance(org.get("id"), str) or not org["id"].isdigit():
            raise ValueError()
        return org
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Карточка Яндекса не подтверждена или срок её выбора истёк. Повторите поиск.") from None
