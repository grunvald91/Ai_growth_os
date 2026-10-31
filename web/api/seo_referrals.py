"""Conservative service recommendation and signed SEO booking attribution.

No external models or guessed service IDs. Unconfigured signing is OFF;
bookings still function normally without SEO attribution.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Service

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORDS = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
STOP_WORDS = frozenset({"и", "в", "на", "для", "по", "с", "со", "из", "под", "от", "до", "или", "the", "of", "and", "for"})
TTL_SECONDS = 14 * 24 * 60 * 60


def _key() -> bytes | None:
    raw = os.getenv("SEO_ATTRIBUTION_SECRET", "")
    return raw.encode("utf-8") if len(raw.encode("utf-8")) >= 32 else None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_reference(business_id: int, slug: str, *, now: int | None = None) -> str | None:
    """Mint only after loading a published article for this active business."""
    key = _key()
    if not key or business_id <= 0 or len(slug) > 160 or not SLUG_RE.fullmatch(slug):
        return None
    payload = f"{business_id}:{slug}:{int(time.time() if now is None else now)}".encode("ascii")
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return _b64(payload) + "." + _b64(signature)


def read_reference(token: str | None, business_id: int, *, now: int | None = None) -> str | None:
    """Return verified article slug or None. Invalid/expired SEO token never blocks booking."""
    key = _key()
    if not key or not token or len(token) > 512 or business_id <= 0:
        return None
    try:
        encoded, signature = token.split(".")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, actual):
            return None
        raw_id, slug, timestamp = payload.decode("ascii").split(":")
        issued = int(timestamp)
        current = int(time.time() if now is None else now)
        if int(raw_id) != business_id or not SLUG_RE.fullmatch(slug) or len(slug) > 160:
            return None
        if issued > current + 60 or current - issued > TTL_SECONDS:
            return None
        return slug
    except (binascii.Error, ValueError, UnicodeError, OverflowError):
        return None


def _terms(value: str) -> set[str]:
    terms = {word.replace("ё", "е") for word in WORDS.findall(value.casefold())}
    return {term for term in terms if term not in STOP_WORDS and len(term) >= 4}


def _stem(word: str) -> str:
    # Conservative two-character ending tolerance, not a linguistic claim.
    return word[:-2] if len(word) >= 7 else word


def choose_service(article: dict, services: list[Service]) -> int | None:
    """All significant service-name terms must be in article title or primary search query.

    Prefer the most specific unique candidate. Ambiguous or weak matches fall
    back to the company's full booking catalogue.
    """
    source = " ".join(str(article.get(key) or "") for key in ("title", "primary_query"))
    source_terms = {_stem(word) for word in _terms(source)}
    matches: list[tuple[int, int]] = []
    for service in services:
        if not service.is_active or service.is_additional:
            continue
        terms = _terms(service.name or "")
        if terms and {_stem(word) for word in terms}.issubset(source_terms):
            matches.append((len(terms), service.id))
    if not matches:
        return None
    highest = max(n for n, _ in matches)
    top = [service_id for n, service_id in matches if n == highest]
    return top[0] if len(top) == 1 else None


async def article_booking_link(db: AsyncSession, business_id: int, article: dict) -> dict:
    """Single source of truth for HTML and React article CTAs."""
    slug = str(article.get("slug") or "")
    result = await db.scalars(select(Service).where(
        Service.business_id == business_id,
        Service.is_active.is_(True),
        Service.is_additional.is_(False),
    ))
    service_id = choose_service(article, list(result.all()))
    token = make_reference(business_id, slug)
    params = {}
    if service_id is not None:
        params["service"] = service_id
    if token is not None:
        params["seo_ref"] = token
    base = f"/book/{business_id}"
    company_base = f"/business/{business_id}"
    return {
        "service_id": service_id,
        "seo_ref": token,
        "cta_url": base + ("?" + urlencode(params) if params else ""),
        "company_url": company_base + ("?" + urlencode({"seo_ref": token}) if token else ""),
    }
