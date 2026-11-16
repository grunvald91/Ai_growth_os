"""Conservative Programmatic SEO opportunities from collected, source-backed search queries.

This is a page MAP, not an article factory or a publishing endpoint. No landing
page is released automatically: an editor must establish unique page value,
facts, canonical URL and deployment support before creating a public page.
"""
from __future__ import annotations

from typing import Literal
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.company_profile import CompanyProfile, load_company_profile
from app.models.domain import Artifact, Product
from app.seo.activation import require_enabled
from app.seo.clustering import normalize
from app.seo.contracts import SemanticCore
from app.seo.planner import planned_queries
from app.seo.publishing import slugify
from app.services.artifacts import ArtifactService

KIND = "seo_landing_opportunities"
MAX_PAGES = 24

# Intent and source signals are present in autocomplete; search VOLUME is not.
_DEVICES = ("iphone", "ipad", "android", "ios", "macos", "macbook", "windows",
            "linux", "smart tv", "smart-tv", "смарт тв", "телевизор", "роутер")
_STOPWORDS = frozenset(
    "для при как что где цена цены отзывы купить заказать сервис услуга услуги "
    "система платформа программа программы онлайн в на по под без через "
    "бизнеса бизнес подключить подключение настройка настроить лучший лучшие".split()
)


class LandingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cluster_id: str
    primary_query: str = Field(min_length=2, max_length=300)
    intent: Literal["commercial", "transactional"]
    page_type: Literal["offer", "device", "location"]
    proposed_path: str
    offer_name: str | None = None
    verified_offer: bool = False
    geography: str | None = None
    engines: list[Literal["google", "yandex"]]
    status: Literal["review", "blocked"]
    reason_code: Literal[
        "unique_content_required", "offer_evidence_required",
        "local_value_required", "already_covered"
    ]


class LandingOpportunities(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["draft_only"] = "draft_only"
    demand_data: Literal["none"] = "none"
    candidates: list[LandingCandidate] = Field(default_factory=list, max_length=MAX_PAGES)


def _tokens(value: str) -> set[str]:
    # Prefixes bridge simple inflections (including short names like DNS, брус).
    # They do not invent query synonyms.
    return {w[:5] for w in normalize(value).split()
            if len(w) >= 3 and w not in _STOPWORDS}


def _offer_for(query: str, profile: CompanyProfile | None):
    if profile is None:
        return None
    query_tokens = _tokens(query)
    best, best_overlap = None, 0
    for offer in profile.offer.products_services:
        match_tokens = _tokens(" ".join(filter(None, (offer.name, offer.product_family))))
        overlap = len(query_tokens & match_tokens)
        if overlap > best_overlap:
            best, best_overlap = offer, overlap
    return best


def _geography_for(query: str, profile: CompanyProfile | None) -> str | None:
    if profile is None:
        return None
    normalized = f" {normalize(query)} "
    # Only explicitly declared service geographies can be identified here.
    # A vague "in <city>" phrase must NOT be mistaken for proof of coverage.
    for area in profile.identity.service_geography:
        name = normalize(area)
        if len(name) >= 3 and f" {name} " in normalized:
            return area
    return None


def make_landing_opportunities(
    core: SemanticCore,
    profile: CompanyProfile | None,
    *,
    already_covered: set[str] | None = None,
    limit: int = MAX_PAGES,
) -> LandingOpportunities:
    """Suggest only real collected commercial queries, never location permutations.

    A candidate with an evidenced live offer remains a *review* item, not an
    approval to publish. Geographic pages always require proof of distinct local
    value, not just a city substitution in the title.
    """
    if not 0 <= limit <= MAX_PAGES:
        raise ValueError("Invalid landing opportunity limit")
    if limit == 0:
        return LandingOpportunities()
    covered = already_covered or set()
    candidates: list[LandingCandidate] = []
    seen_queries: set[str] = set()
    seen_paths: set[str] = set()
    ordered = sorted(
        (c for c in core.clusters if c.intent in ("commercial", "transactional")),
        key=lambda c: (c.intent != "transactional", -len(c.engines), c.id),
    )
    for cluster in ordered:
        query = cluster.head_query
        key = normalize(query)
        if key in seen_queries or not query.strip():
            continue
        seen_queries.add(key)
        offer = _offer_for(query, profile)
        verified = bool(
            offer and offer.release_status in {"live", "limited"}
            and any(ref.strip() for ref in offer.evidence_refs)
        )
        area = _geography_for(query, profile)
        device = any(f" {normalize(d)} " in f" {key} " for d in _DEVICES)
        page_type = "location" if area else "device" if device else "offer"
        slug = slugify(query)
        path = f"/solutions/{slug}"
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if key in covered:
            reason, status = "already_covered", "blocked"
        elif area:
            reason, status = "local_value_required", "blocked"
        elif not verified:
            reason, status = "offer_evidence_required", "blocked"
        else:
            reason, status = "unique_content_required", "review"
        candidates.append(LandingCandidate(
            cluster_id=cluster.id,
            primary_query=query,
            intent=cluster.intent,
            page_type=page_type,
            proposed_path=path,
            offer_name=offer.name if verified else None,
            verified_offer=verified,
            geography=area,
            engines=cluster.engines,
            status=status,
            reason_code=reason,
        ))
        if len(candidates) >= limit:
            break
    return LandingOpportunities(candidates=candidates)


def store_landing_opportunities(
    db: Session,
    product_id: UUID,
    task_id: UUID,
    core: SemanticCore,
    *,
    products_root: Path,
) -> Artifact:
    """Persist per-tenant drafts idempotently without new LLM calls or tasks."""
    require_enabled(db, product_id)
    previous = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == KIND,
    ).limit(1))
    if previous is not None:
        return previous
    product = db.get(Product, product_id)
    if product is None:
        raise LookupError("Company not found")
    loaded = load_company_profile(products_root, product.slug)
    plan = make_landing_opportunities(
        core, loaded.profile, already_covered=planned_queries(db, product_id))
    artifact = ArtifactService(db).create(
        product_id=product_id, task_id=task_id, kind=KIND,
        title=f"Programmatic SEO: {len(plan.candidates)} кандидатов страниц",
        content=plan.model_dump_json(),
    )
    db.commit()
    return artifact
