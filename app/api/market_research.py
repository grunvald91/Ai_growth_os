"""Owner-only Wordstat v2 integration; production relies on authenticated edge + loopback bind."""
import hashlib
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Product

router = APIRouter()
BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
CACHE_SECONDS = 12 * 60 * 60


class MarketQuery(BaseModel):
    phrase: str = Field(min_length=2, max_length=200)
    region_id: int = Field(default=225, ge=1, le=999999)
    num_phrases: int = Field(default=30, ge=1, le=80)
    include_dynamics: bool = False


def _month_window():
    """Last 12 fully completed months as RFC3339 range."""
    month_start = datetime.now(timezone.utc).date().replace(day=1)
    end = month_start - timedelta(days=1)
    start_year = month_start.year - (month_start.month == 1)
    start_month = 12 if month_start.month == 1 else month_start.month - 1
    return (
        date(start_year - (start_month == 12), start_month % 12 + 1, 1).isoformat() + "T00:00:00Z",
        end.isoformat() + "T23:59:59Z",
    )


class DurableCache:
    """Daily call budget and cache stored on the existing persistent products volume."""
    def __init__(self, path: Path, limit: int):
        self.path, self.limit = path, limit
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget(day TEXT PRIMARY KEY, used INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, saved INTEGER NOT NULL, payload TEXT NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def cached(self, key: str):
        with self.connect() as db:
            row = db.execute("SELECT saved,payload FROM cache WHERE key=?", (key,)).fetchone()
        if row and int(time.time()) - row[0] < CACHE_SECONDS:
            return json.loads(row[1])
        return None

    def reserve(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT used FROM budget WHERE day=?", (today,)).fetchone()
            if row and row[0] >= self.limit:
                raise HTTPException(429, "Дневной лимит платных вызовов Wordstat исчерпан.")
            db.execute(
                "INSERT INTO budget(day,used) VALUES(?,1) "
                "ON CONFLICT(day) DO UPDATE SET used=used+1", (today,),
            )

    def remaining(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            row = db.execute("SELECT used FROM budget WHERE day=?", (today,)).fetchone()
        return max(0, self.limit - (row[0] if row else 0))

    def save(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT INTO cache(key,saved,payload) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET saved=excluded.saved,payload=excluded.payload",
                (key, int(time.time()), json.dumps(value, ensure_ascii=False)),
            )


class Wordstat:
    def __init__(self, key: str, folder: str, cache: DurableCache, transport=None):
        self.key, self.folder, self.cache, self.transport = key, folder, cache, transport

    async def request(self, method: str, fields: dict):
        if method not in {"topRequests", "dynamics"}:
            raise ValueError("Invalid Wordstat method")
        payload = {**fields, "folderId": self.folder}
        fingerprint = hashlib.sha256(json.dumps(
            {"method": method, "payload": payload}, sort_keys=True
        ).encode()).hexdigest()
        cached = self.cache.cached(fingerprint)
        if cached is not None:
            return cached, True
        # Reserve first; network failure must not permit unbounded retries.
        self.cache.reserve()
        try:
            async with httpx.AsyncClient(timeout=12, trust_env=False, transport=self.transport) as client:
                result = await client.post(
                    BASE_URL + "/" + method, json=payload,
                    headers={"Authorization": "Api-Key " + self.key},
                )
                result.raise_for_status()
                body = result.json()
                if not isinstance(body, dict):
                    raise ValueError("Non-object response")
        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            if status in (401, 403):
                raise HTTPException(503, "Нет доступа к Wordstat: проверьте ключ и роль search-api.webSearch.user.") from None
            if status == 429:
                raise HTTPException(503, "Лимит на стороне Яндекса, повторите позднее.") from None
            raise HTTPException(502, "Ошибка Wordstat HTTP " + str(status)) from None
        except (httpx.RequestError, ValueError):
            raise HTTPException(502, "Не удалось прочитать ответ Wordstat.") from None
        self.cache.save(fingerprint, body)
        return body, False


def wordstat_client():
    key = os.environ.get("WORDSTAT_API_KEY", "").strip()
    folder = os.environ.get("WORDSTAT_FOLDER_ID", "").strip()
    if not key or not folder:
        raise HTTPException(503, "Для Wordstat нужны WORDSTAT_API_KEY и WORDSTAT_FOLDER_ID.")
    try:
        limit = int(os.environ.get("WORDSTAT_DAILY_LIMIT", "40"))
        if not 1 <= limit <= 200:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "Некорректный WORDSTAT_DAILY_LIMIT.") from None
    path = get_settings().products_root / ".wordstat" / "cache.sqlite3"
    return Wordstat(key, folder, DurableCache(path, limit))


def _phrases(rows):
    return [{"phrase": str(v["phrase"]), "count": int(v["count"])}
            for v in rows[:80] if isinstance(v, dict) and "phrase" in v and "count" in v]


@router.get("/market-research/status")
def market_status():
    configured = bool(os.getenv("WORDSTAT_API_KEY") and os.getenv("WORDSTAT_FOLDER_ID"))
    return {"configured": configured,
            "remaining_calls_today": wordstat_client().cache.remaining() if configured else None}


@router.post("/market-research/analyze")
async def market_analyze(query: MarketQuery):
    wordstat = wordstat_client()
    phrase = query.phrase.strip()
    if len(phrase) < 2:
        raise HTTPException(422, "Введите поисковую фразу.")
    regions = [str(query.region_id)]
    raw, top_cached = await wordstat.request("topRequests", {
        "phrase": phrase, "regions": regions, "numPhrases": str(query.num_phrases),
        "devices": ["DEVICE_ALL"],
    })
    dynamics, dynamics_cached = [], None
    if query.include_dynamics:
        date_from, date_to = _month_window()
        trend, dynamics_cached = await wordstat.request("dynamics", {
            "phrase": phrase, "regions": regions, "period": "PERIOD_MONTHLY",
            "fromDate": date_from, "toDate": date_to, "devices": ["DEVICE_ALL"],
        })
        dynamics = [{"date": str(x["date"])[:10], "count": int(x["count"])}
                    for x in trend.get("results", []) if "date" in x and "count" in x]
    return {
        "source": "Yandex Cloud Wordstat API v2",
        "phrase": phrase, "region_id": query.region_id,
        "total_count_last_30_days": int(raw.get("totalCount", 0)),
        "results": _phrases(raw.get("results", [])),
        "associations": _phrases(raw.get("associations", [])),
        "monthly_dynamics": dynamics,
        "cached": {"top": top_cached, "dynamics": dynamics_cached},
        "remaining_calls_today": wordstat.cache.remaining(),
        "notice": "Поисковые запросы не равны покупкам и платёжеспособному спросу.",
    }


@router.get("/market-research", response_class=HTMLResponse)
def market_page(product_id: str | None = None, db: Session = Depends(get_db)):
    from app.api.content_studio import studio_page_shell
    from app.api.market_research_ui import PAGE
    products = list(db.scalars(select(Product).order_by(Product.name)))
    if not products:
        return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><h1>Анализ рынка</h1><p>Создайте компанию в Growth Studio.</p><a href="/studio">Проекты</a></html>')
    selected = next((p for p in products if str(p.id) == product_id), products[0])
    return HTMLResponse(studio_page_shell(
        selected, active="market_research", title="Анализ рынка",
        subtitle="Wordstat: поисковый спрос по России и регионам", body=PAGE,
    ))


@router.get("/market-research/assets/market.js")
def market_js():
    from app.api.market_research_ui import SCRIPT
    return Response(SCRIPT, media_type="application/javascript")
