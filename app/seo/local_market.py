"""Local competitor context for SEO plans; unrelated to search-query frequency."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.company_profile import load_company_profile
from app.integrations.yandex_organizations import YandexOrganizationsError, search
from app.models.domain import Artifact, Product
from app.services.artifacts import ArtifactService

KIND = "seo_local_market"


async def collect_local_market(db, product_id: UUID, task_id: UUID, products_root: Path) -> dict | None:
    """One Yandex request for the selected company's geography and sector.

    Provider/network errors must not break existing SEO semantic collection.
    """
    if not os.getenv("API_YANDEX", "").strip():
        return None
    product = db.get(Product, product_id)
    if product is None:
        return None
    loaded = load_company_profile(products_root, product.slug)
    profile = loaded.profile
    if not profile:
        return None
    identity = profile.identity
    city = (identity.service_geography[0] if identity.service_geography
            else identity.geography or "").strip()
    niche = (identity.category or identity.industry or "").strip()
    if not 2 <= len(city) <= 80 or not 2 <= len(niche) <= 100:
        return None
    try:
        result = await search(niche, city, results=10, quota_path=Path(products_root) /
                              "_integrations" / "yandex_ppo_usage.sqlite")
    except YandexOrganizationsError:
        return None
    organizations = result.get("organizations", [])[:10]
    category_counts = Counter(c for item in organizations for c in item.get("categories", []))
    snapshot = {
        "source": "Яндекс Карты: API Поиска по организациям",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "city_query": city, "industry_query": niche,
        "result_count": len(organizations),
        "categories": dict(category_counts.most_common(10)),
        "organizations": [
            {"name": item["name"], "address": item["address"],
             "categories": item["categories"], "source_url": item.get("maps_url") or item.get("website") or ""}
            for item in organizations
        ],
        "note": "Это справочная выборка организаций, а не статистика частотности или долей рынка.",
    }
    ArtifactService(db).create(
        product_id=product.id, task_id=task_id, kind=KIND,
        title="Локальные конкуренты · Яндекс Карты", content=json.dumps(snapshot, ensure_ascii=False))
    db.commit()
    return snapshot


def snapshot_for_plan(db, product_id: UUID, task_id: UUID | None = None) -> dict | None:
    predicate = [Artifact.product_id == product_id, Artifact.kind == KIND]
    if task_id is not None:
        predicate.append(Artifact.task_id == task_id)
    row = db.scalar(select(Artifact).where(*predicate)
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    if row is None:
        return None
    try:
        data = json.loads(row.content)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None
