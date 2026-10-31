"""Read-only per-company search metrics from official Google and Yandex APIs.

Never request a company-wide root path on shared servgo.ru, never report
other tenants' URLs, and never mistake missing integration for zero traffic.
Credentials are server-side only and never written to the database/logs.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Publication, Product
from app.sso.models import StudioBusinessLink

ORIGIN = "https://servgo.ru"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def article_urls(db: Session, product_id: UUID) -> list[str]:
    """Only URLs that THIS tenant currently publishes; no guessed company IDs."""
    business_id = db.scalar(select(StudioBusinessLink.servgo_business_id).where(
        StudioBusinessLink.product_id == product_id).limit(1))
    product_slug = db.scalar(select(Product.slug).where(Product.id == product_id))
    if business_id is None and product_slug != "servgo":
        return []
    prefix = (f"{ORIGIN}/business/{business_id}/blog/"
              if business_id is not None else f"{ORIGIN}/blog/")
    refs = db.scalars(select(Publication.external_reference)
        .join(Artifact, Artifact.id == Publication.artifact_id)
        .where(Publication.product_id == product_id,
               Artifact.product_id == product_id,
               Publication.status == "published",
               Publication.channel == "own_site",
               Artifact.kind == "article",
               Artifact.status == ArtifactStatus.PUBLISHED)).all()
    urls = set()
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith("/blog/"):
            continue
        slug = ref.removeprefix("/blog/")
        if SLUG.fullmatch(slug):
            urls.add(prefix + slug)
    return sorted(urls)


def _empty(status: str, *, period_days: int) -> dict:
    return {"status": status, "period_days": period_days, "clicks": None,
            "impressions": None, "pages": [], "coverage": "not_available"}


def _number(raw) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        return 0.0
    value = float(raw)
    return value if 0 <= value < 1e15 else 0.0


def _rows_permitted(rows: list[dict], urls: list[str]) -> tuple[list[dict], float, float]:
    allowed = set(urls)
    found = []
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("url") not in allowed:
            continue
        clicks, impressions = _number(raw.get("clicks")), _number(raw.get("impressions"))
        found.append({"url": raw["url"], "clicks": clicks, "impressions": impressions})
    return found, sum(row["clicks"] for row in found), sum(row["impressions"] for row in found)


async def google_metrics(urls: list[str], *, days: int, client: httpx.AsyncClient,
                         env: dict | None = None) -> dict:
    variables = os.environ if env is None else env
    token = variables.get("SERVGO_GSC_READONLY_TOKEN", "")
    property_url = variables.get("SERVGO_GSC_SITE_URL", "")
    if not token or property_url not in (ORIGIN + "/", "sc-domain:servgo.ru"):
        return _empty("not_configured", period_days=days)
    if not urls:
        return _empty("no_public_articles", period_days=days)
    # The final Search Console dataset lags current time; do not label today as final.
    end = datetime.now(timezone.utc).date() - timedelta(days=3)
    start = end - timedelta(days=days - 1)
    prefix = urls[0].rsplit("/", 1)[0] + "/"
    request = {
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": ["page"], "type": "web", "dataState": "final",
        "dimensionFilterGroups": [{"groupType": "and", "filters": [
            {"dimension": "page", "operator": "contains", "expression": prefix}]}],
        "rowLimit": 1000, "startRow": 0,
    }
    try:
        response = await client.post(
            "https://www.googleapis.com/webmasters/v3/sites/"
            + quote(property_url, safe="") + "/searchAnalytics/query",
            headers={"Authorization": "Bearer " + token}, json=request, timeout=8)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("rows", []), list):
            raise ValueError("Invalid GSC result")
        values = []
        for row in data.get("rows", []):
            if isinstance(row, dict) and isinstance(row.get("keys"), list) and row["keys"]:
                values.append({"url": row["keys"][0], "clicks": row.get("clicks"),
                               "impressions": row.get("impressions")})
        pages, clicks, impressions = _rows_permitted(values, urls)
        return {"status": "connected", "period_days": days,
                "date_from": start.isoformat(), "date_to": end.isoformat(),
                "clicks": clicks, "impressions": impressions, "pages": pages,
                "coverage": "top_1000_page_rows_not_complete_census"}
    except (httpx.HTTPError, ValueError, TypeError):
        return _empty("provider_unavailable", period_days=days)


async def yandex_metrics(urls: list[str], *, days: int, client: httpx.AsyncClient,
                         env: dict | None = None) -> dict:
    variables = os.environ if env is None else env
    token = variables.get("SERVGO_YANDEX_WEBMASTER_TOKEN", "")
    uid = variables.get("SERVGO_YANDEX_WEBMASTER_USER_ID", "")
    host = variables.get("SERVGO_YANDEX_WEBMASTER_HOST_ID", "")
    period = min(days, 14)  # URL query analytics only exposes the last 2 weeks.
    if not token or not uid.isdecimal() or not host or len(host) > 150:
        return _empty("not_configured", period_days=period)
    if not urls:
        return _empty("no_public_articles", period_days=period)
    prefix = urls[0].rsplit("/", 1)[0] + "/"
    payload = {
        "offset": 0, "limit": 500, "device_type_indicator": "ALL",
        "search_location": "WEB_LOCATION", "text_indicator": "URL",
        "filters": {"text_filters": [
            {"text_indicator": "URL", "operation": "TEXT_CONTAINS", "value": prefix}]},
    }
    try:
        response = await client.post(
            "https://api.webmaster.yandex.net/v4/user/" + uid
            + "/hosts/" + quote(host, safe="") + "/query-analytics/list",
            headers={"Authorization": "OAuth " + token}, json=payload, timeout=8)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("text_indicator_to_statistics"), list):
            raise ValueError("Invalid Yandex result")
        # Only metrics for URLs explicitly returned from our own public publications.
        observations = []
        start = datetime.now(timezone.utc).date() - timedelta(days=period - 1)
        for record in data["text_indicator_to_statistics"]:
            if not isinstance(record, dict):
                continue
            indicator = record.get("text_indicator")
            if not isinstance(indicator, dict) or indicator.get("type") != "URL":
                continue
            row = {"url": indicator.get("value"), "clicks": 0.0, "impressions": 0.0}
            for stat in record.get("statistics", []):
                if not isinstance(stat, dict):
                    continue
                date = stat.get("date", "")
                if not isinstance(date, str) or not (start.isoformat() <= date[:10] <= datetime.now(timezone.utc).date().isoformat()):
                    continue
                if stat.get("field") == "CLICKS":
                    row["clicks"] += _number(stat.get("value"))
                elif stat.get("field") == "IMPRESSIONS":
                    row["impressions"] += _number(stat.get("value"))
            observations.append(row)
        pages, clicks, impressions = _rows_permitted(observations, urls)
        return {"status": "connected", "period_days": period, "clicks": clicks,
                "impressions": impressions, "pages": pages,
                "coverage": "first_500_url_rows_14_day_limit"}
    except (httpx.HTTPError, ValueError, TypeError):
        return _empty("provider_unavailable", period_days=period)


async def inspect_google_article(db: Session, product_id: UUID, *,
                                 url: str, client: httpx.AsyncClient,
                                 env: dict | None = None) -> dict:
    """Explicit inspection of ONE published tenant page, not a live URL test.

    Google URL Inspection reads the version known to Google; successful
    API access does not imply indexing, impressions or AI citation.
    """
    if url not in article_urls(db, product_id):
        raise LookupError("Опубликованная статья этого бизнеса не найдена")
    variables = os.environ if env is None else env
    token = variables.get("SERVGO_GSC_READONLY_TOKEN", "")
    property_url = variables.get("SERVGO_GSC_SITE_URL", "")
    if not token or property_url not in (ORIGIN + "/", "sc-domain:servgo.ru"):
        return {"url": url, "status": "not_configured", "indexing": None}
    try:
        response = await client.post(
            "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
            headers={"Authorization": "Bearer " + token},
            json={"inspectionUrl": url, "siteUrl": property_url, "languageCode": "ru-RU"},
            timeout=8)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("inspectionResult") if isinstance(payload, dict) else None
        index = result.get("indexStatusResult") if isinstance(result, dict) else None
        if not isinstance(index, dict):
            raise ValueError("Invalid inspection result")
        return {"url": url, "status": "connected",
                "indexing": {key: index.get(key) for key in (
                    "verdict", "coverageState", "lastCrawlTime", "robotsTxtState",
                    "indexingState", "pageFetchState", "googleCanonical",
                    "userCanonical")}}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"url": url, "status": "provider_unavailable", "indexing": None}


async def company_search_metrics(db: Session, product_id: UUID, *,
                                 days: int = 28,
                                 client: httpx.AsyncClient | None = None,
                                 env: dict | None = None) -> dict:
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    urls = article_urls(db, product_id)
    if client is not None:
        google = await google_metrics(urls, days=days, client=client, env=env)
        yandex = await yandex_metrics(urls, days=days, client=client, env=env)
    else:
        async with httpx.AsyncClient(follow_redirects=False) as c:
            google = await google_metrics(urls, days=days, client=c, env=env)
            yandex = await yandex_metrics(urls, days=days, client=c, env=env)
    return {"product_id": str(product_id), "published_url_count": len(urls),
            "google_search_console": google, "yandex_webmaster": yandex,
            "bing_ai_performance": {"status": "no_verified_api_integration",
                                    "citations": None},
            "chatgpt_search": {"status": "no_verified_citation_feed",
                              "citations": None}}
