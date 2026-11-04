"""Bounded, server-side Yandex Maps *organization* lookup.

No response data is persisted here. The key stays in the Growth service. This is
not Yandex Cloud Search API or a source of business email addresses.

The owner's server-side API key enables lookup; no unrelated authorization flags.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

ENDPOINT = "https://search-maps.yandex.ru/v1/"

class YandexOrganizationsError(Exception):
    """Sanitized provider failure. Never attach request URLs containing apikey."""


def capability() -> dict:
    """Operational availability, not contract or commercial entitlement inference."""
    active = bool(os.getenv("API_YANDEX", "").strip())
    return {"key_configured": active, "enabled": active, "daily_budget": budget()}


def budget() -> int | None:
    """An optional owner-specified local ceiling; None leaves quota control to Yandex."""
    value = os.getenv("SERVGO_YANDEX_PPO_DAILY_BUDGET", "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def _reserve_request(quota_path: Path) -> None:
    """Atomic shared-host budget. Counts attempts, not just successful responses."""
    quota_path.parent.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        with sqlite3.connect(quota_path, timeout=15) as db:
            db.execute("CREATE TABLE IF NOT EXISTS yandex_ppo_usage (day TEXT PRIMARY KEY, used INTEGER NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO yandex_ppo_usage (day,used) VALUES (?,0)", (day,))
            ceiling = budget()
            if ceiling is None:
                changed = db.execute(
                    "UPDATE yandex_ppo_usage SET used=used+1 WHERE day=?", (day,)).rowcount
            else:
                changed = db.execute(
                    "UPDATE yandex_ppo_usage SET used=used+1 WHERE day=? AND used<?",
                    (day, ceiling)).rowcount
            if not changed:
                raise YandexOrganizationsError("Суточный бюджет запросов Яндекса исчерпан")
            db.execute("DELETE FROM yandex_ppo_usage WHERE day<?", (day,))
    except sqlite3.Error:
        raise YandexOrganizationsError("Не удалось проверить бюджет Яндекс API") from None


def _website(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    try:
        p = urlsplit(raw.strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        if p.port not in (None, 80, 443):
            return ""
        # Public website checks within ServGo use HTTPS. Yandex often lists
        # historical http:// URLs; convert before handing them to the crawler.
        return urlunsplit(("https", p.hostname, p.path, p.query, ""))[:500]
    except ValueError:
        return ""


def normalize(payload: dict) -> dict:
    """Use only the documented CompanyMetaData fields; email is not provided."""
    result = []
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise YandexOrganizationsError("Яндекс вернул неожиданный формат ответа")
    for feature in payload["features"]:
        meta = (feature.get("properties") or {}).get("CompanyMetaData") if isinstance(feature, dict) else None
        if not isinstance(meta, dict) or not meta.get("name") or not meta.get("id"):
            continue
        categories = meta.get("Categories") or []
        phones = meta.get("Phones") or []
        org_id = str(meta["id"])[:80]
        result.append({
            "id": org_id,
            "maps_url": "https://yandex.ru/maps/org/" + org_id if re.fullmatch(r"\d{1,25}", org_id) else "",
            "name": str(meta["name"])[:200],
            "address": str(meta.get("address") or "")[:300],
            "website": _website(meta.get("url")),
            "categories": [str(c["name"])[:100] for c in categories
                           if isinstance(c, dict) and isinstance(c.get("name"), str)][:20],
            "phones": [str(p["formatted"])[:80] for p in phones
                       if isinstance(p, dict) and isinstance(p.get("formatted"), str)][:10],
            "email": None,
            "source": "Яндекс Карты",
        })
    return {"organizations": result, "source": "Яндекс Карты"}


async def search(query: str, city: str, *, quota_path: Path, results: int = 25,
                 skip: int = 0, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    config = capability()
    if not config["enabled"]:
        raise YandexOrganizationsError("API_YANDEX не передан в окружение сервиса Growth")
    if not 2 <= len(query.strip()) <= 100 or not 2 <= len(city.strip()) <= 80:
        raise YandexOrganizationsError("Укажите нишу и город")
    if not 1 <= results <= 50 or not 0 <= skip <= 1000 or skip % results != 0:
        raise YandexOrganizationsError("Неверные параметры страницы")
    _reserve_request(quota_path)
    params = {"apikey": os.environ["API_YANDEX"].strip(), "text": f"{query.strip()} {city.strip()}",
              "lang": "ru_RU", "type": "biz", "results": results, "skip": skip}
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=False, transport=transport) as client:
            response = await client.get(ENDPOINT, params=params)
            if response.status_code != 200:
                raise YandexOrganizationsError(f"Яндекс API: HTTP {response.status_code}")
            data = response.json()
    except (httpx.HTTPError, ValueError):
        # httpx exceptions may include the complete URL with the credential.
        raise YandexOrganizationsError("Запрос к Яндекс API не выполнен") from None
    value = normalize(data)
    value["page_size"] = results
    value["skip"] = skip
    return value
