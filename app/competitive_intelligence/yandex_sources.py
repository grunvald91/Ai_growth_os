"""Automatic Yandex directory discovery for each business-intelligence run.

Directory listings are never labelled as reviews, prices or search demand.
Website text comes only from the project's pinned-DNS public page fetcher.
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
    """Return (optional attributed bundle, operator-readable discovery status)."""
    if not os.getenv("API_YANDEX", "").strip():
        return None, "no_key"
    profile = load_company_profile(products_root, product.slug).profile
    identity = profile.identity if profile else None
    niche = ((identity.category or identity.industry or "").strip() if identity else "")
    if request.competitors:
        # Exact named competitors are stronger than a generic category.
        niche = request.competitors[0].strip()
    city = (request.region or ((identity.service_geography[0] if identity.service_geography
             else identity.geography or "") if identity else "")).strip()
    if not 2 <= len(niche) <= 100 or not 2 <= len(city) <= 80:
        return None, "insufficient_company_context"
    try:
        data = await search(niche, city, results=min(10, max_sources),
                            quota_path=Path(products_root) /
                            "_integrations" / "yandex_ppo_usage.sqlite")
    except YandexOrganizationsError as exc:
        _log.warning("intelligence_yandex_lookup_failed reason=%s", type(exc).__name__)
        return None, "provider_unavailable"
    sources: list[ResearchSource] = []
    seen: set[str] = set()
    fetcher = PublicWebsiteFetcher()
    websites_fetched = 0
    for item in data.get("organizations", []):
        org_id = str(item.get("id") or "")
        if not org_id or org_id in seen or not item.get("name"):
            continue
        seen.add(org_id)
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
            competitor=item["name"], excerpt=excerpt[:4000], source_type="website"))
        website = item.get("website") or ""
        if websites_fetched >= 2 or not website.startswith("https://") or len(sources) >= max_sources:
            continue
        websites_fetched += 1
        try:
            # Pinned DNS, no redirects and public IP only: no uncontrolled SSRF.
            page = await fetcher.fetch(website)
            if page.status in ("read", "truncated") and len(page.text.strip()) >= 30:
                sources.append(ResearchSource(
                    url=website, title="Сайт компании " + item["name"],
                    platform="Сайт компании", competitor=item["name"],
                    excerpt=page.text[:2000], source_type="website"))
        except Exception as exc:
            _log.warning("intelligence_company_website_failed error_type=%s", type(exc).__name__)
    if not sources:
        return None, "no_matches"
    return ResearchSourceBundle(
        label="Яндекс Карты: автоматический поиск организаций и сайтов",
        sources=sources[:max_sources]), "collected"
