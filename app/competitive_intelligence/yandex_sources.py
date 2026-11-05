"""Automatic Yandex organization evidence for each business-intelligence run.

Company directories are not customer reviews, pricing, search volume or proof
of market share. Websites are fetched through the existing pinned-DNS fetcher.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from app.company_profile import load_company_profile
from app.integrations.yandex_organizations import YandexOrganizationsError, search
from app.intake.sources import PublicWebsiteFetcher
from app.workflows.research_contracts import ResearchSource, ResearchSourceBundle

_log = logging.getLogger(__name__)


async def collect_sources(product, request, products_root: Path, *, max_sources: int = 10):
    """Collect attributed, deduplicated sources for all named competitors.

    Each explicitly named competitor is searched separately; one result from
    each search is preferred to ten arbitrary results from only the first.
    A provider failure does not invalidate the remaining competitors.
    """
    if not os.getenv("API_YANDEX", "").strip():
        return None, "no_key"

    profile = load_company_profile(products_root, product.slug).profile
    identity = profile.identity if profile else None
    category = (identity.category or identity.industry or "").strip() if identity else ""
    city = (request.region or (
        identity.service_geography[0] if identity and identity.service_geography
        else identity.geography or "" if identity else ""
    )).strip()

    queries = list(dict.fromkeys(
        str(item).strip() for item in request.competitors if str(item).strip()
    )) if request.competitors else ([category] if category else [])
    if not queries or not 2 <= len(city) <= 80 or not 1 <= max_sources <= 50:
        return None, "insufficient_company_context"

    # Every requested name gets a lookup instead of silently ignoring it.
    # Use one result per explicit name; category lookup can return more.
    per_query = 1 if request.competitors else min(10, max_sources)
    found = []
    seen_ids = set()
    unavailable = 0
    for query in queries:
        if not 2 <= len(query) <= 100:
            continue
        try:
            result = await search(
                query, city, results=per_query,
                quota_path=Path(products_root) / "_integrations" / "yandex_ppo_usage.sqlite",
            )
        except YandexOrganizationsError as exc:
            unavailable += 1
            _log.warning(
                "intelligence_yandex_lookup_failed error_type=%s", type(exc).__name__
            )
            continue
        for item in result.get("organizations", []):
            oid = str(item.get("id") or "")
            if not oid or oid in seen_ids or not item.get("name"):
                continue
            seen_ids.add(oid)
            found.append(item)

    sources: list[ResearchSource] = []
    for item in found:
        uri = item.get("maps_url") or item.get("website")
        if not uri or not uri.startswith("https://"):
            continue
        excerpt = "; ".join(x for x in [
            "Справочник Яндекс Карты: " + item["name"],
            "Адрес: " + item["address"] if item.get("address") else "",
            "Категории: " + ", ".join(item["categories"][:5]) if item.get("categories") else "",
        ] if x)
        sources.append(ResearchSource(
            url=uri, title=item["name"], platform="Яндекс Карты",
            competitor=item["name"], excerpt=excerpt[:4000], source_type="website",
        ))
        if len(sources) >= max_sources:
            break

    # Enrich no more than two websites when the source budget has space.
    fetcher = PublicWebsiteFetcher()
    fetched = 0
    for item in found:
        if fetched >= 2 or len(sources) >= max_sources:
            break
        website = item.get("website") or ""
        if not website.startswith("https://"):
            continue
        fetched += 1
        try:
            page = await fetcher.fetch(website)
            if page.status in ("read", "truncated") and len(page.text.strip()) >= 30:
                sources.append(ResearchSource(
                    url=website, title="Сайт компании " + item["name"],
                    platform="Сайт компании", competitor=item["name"],
                    excerpt=page.text[:2000], source_type="website",
                ))
        except Exception as exc:
            _log.warning(
                "intelligence_company_website_failed error_type=%s", type(exc).__name__
            )

    if not sources:
        return None, "provider_unavailable" if unavailable else "no_matches"
    return ResearchSourceBundle(
        label="Яндекс Карты: автоматический поиск организаций и сайтов",
        sources=sources,
    ), "partial" if unavailable else "collected"
