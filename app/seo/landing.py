"""Conservative Programmatic SEO landing opportunities from real collected queries.

This is a review-only page map, not a live route or publication endpoint.
A confirmed offer does not prove device compatibility or local page value.
"""
from __future__ import annotations

import hashlib
import re
from math import ceil
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.company_profile import CompanyProfile, ServiceOffer, load_company_profile
from app.models.domain import Artifact, Product, Task
from app.seo.activation import require_enabled
from app.seo.clustering import normalize
from app.seo.contracts import SemanticCore
from app.seo.planner import planned_queries
from app.seo.publishing import slugify
from app.services.artifacts import ArtifactService

KIND = "seo_landing_opportunities"
MAX_PAGES = 24

_DEVICES = (
    "iphone", "ipad", "android", "ios", "macos", "macbook", "windows",
    "linux", "smart tv", "smart-tv", "смарт тв", "телевизор", "роутер",
)
# Never turn an arbitrary query about "в приложении" or "в салоне" into a city.
_NON_GEOGRAPHIC = frozenset(
    "салоне салонах студии студиях приложении приложениях программе программах "
    "системе системах браузере браузерах бизнесе бизнесах интернете сети "
    "кабинете кабинeтах магазине магазинах каталоге таблице crm".split()
)
# Recognize common unlabelled cities even without a preposition. For other
# places, explicit "в/из/город ..." cues fail closed rather than proving scope.
_CITY_STEMS = frozenset(
    "моск казан сочи крас ново екат самар рост волг перм омск уфим тюме "
    "воро кали питер петер калуг ижев томск влад сург челя".split()
)
_STOPWORDS = frozenset(
    "для при как что где цена цены стоимость отзывы купить заказать сервис услуга услуги "
    "система платформа программа программы в на по под без через бизнеса бизнес "
    "подключить подключение настройка настроить лучший лучшие".split()
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
    device: str | None = None
    engines: list[Literal["google", "yandex"]]
    status: Literal["review", "blocked"]
    reason_code: Literal[
        "unique_content_required", "offer_evidence_required",
        "local_value_required", "unverified_geography",
        "device_evidence_required", "already_covered"
    ]
    revision: int = Field(default=1, ge=1)


class LandingOpportunities(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["draft_only"] = "draft_only"
    demand_data: Literal["none"] = "none"
    # Each new run records only NEW or substantively CHANGED candidates.
    candidates: list[LandingCandidate] = Field(default_factory=list, max_length=MAX_PAGES)


def canonical_query(value: str) -> str:
    """Only orthographic equivalents, never guessed semantic synonyms."""
    return " ".join(normalize(value).replace("-", " ").split())


def _stem(word: str) -> str:
    if len(word) == 5 and word[-1] in "аеёиоуыюя":
        return word[:-1]  # бруса -> брус
    return word[:5]


def _tokens(value: str, *, exclude: frozenset[str] = frozenset()) -> frozenset[str]:
    return frozenset(
        _stem(word) for word in canonical_query(value).split()
        if len(word) >= 3 and word not in _STOPWORDS and _stem(word) not in exclude
    )


def _offer_for(query: str, profile: CompanyProfile | None) -> ServiceOffer | None:
    """Do not label a page verified on the basis of one generic shared word."""
    if profile is None:
        return None
    query_tokens = _tokens(query)
    brand_tokens = _tokens(profile.identity.name or "")
    matches: list[tuple[int, int, ServiceOffer]] = []
    for offer in profile.offer.products_services:
        tokens = _tokens(offer.name, exclude=brand_tokens)
        if not tokens:
            continue
        overlap = len(tokens & query_tokens)
        # Short product labels must match in full, longer descriptive labels
        # need at least 75% of their distinctive content.
        required = len(tokens) if len(tokens) <= 3 else ceil(len(tokens) * 0.75)
        if overlap >= required:
            matches.append((overlap, len(tokens), offer))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    best = matches[0]
    # Ambiguous offers must be resolved by an editor, not random list order.
    if len(matches) > 1 and matches[1][:2] == best[:2]:
        return None
    return best[2]


def _find_device(query: str) -> str | None:
    text = f" {canonical_query(query)} "
    for device in _DEVICES:
        if f" {canonical_query(device)} " in text:
            return canonical_query(device)
    return None


def _geo_hint(query: str, profile: CompanyProfile | None) -> tuple[bool, str | None]:
    """A geographic cue is NOT evidence that the company actually serves it."""
    text = canonical_query(query)
    if profile is not None:
        for area in profile.identity.service_geography:
            normalized = canonical_query(area)
            if len(normalized) >= 3 and f" {normalized} " in f" {text} ":
                return True, area
    words = text.split()
    for index, word in enumerate(words):
        if word in {"город", "городе", "города", "г"} and index + 1 < len(words):
            return True, None
        if word in {"в", "во", "из", "по"} and index + 1 < len(words):
            next_word = words[index + 1]
            if next_word == "городе":
                return True, None
            if next_word not in _NON_GEOGRAPHIC:
                # Common locative phrases that are not place names are
                # deliberately blocked for review when ambiguous.
                if profile is not None:
                    for area in profile.identity.service_geography:
                        if _stem(canonical_query(area).split()[0])[:4] == _stem(next_word)[:4]:
                            return True, area
                return True, None
    if any(_stem(word)[:4] in {city[:4] for city in _CITY_STEMS}
           for word in words if len(word) >= 4):
        return True, None
    return False, None


def _path_for(query: str) -> str:
    """Stable across runs; a suffix avoids silent 80-character slug collisions."""
    key = canonical_query(query)
    base = slugify(key)[:62].rstrip("-") or "topic"
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"/solutions/{base}-{suffix}"


def _candidate_fingerprint(item: LandingCandidate) -> tuple:
    # Search engine source differences / cluster ID changes are not new pages.
    return (canonical_query(item.primary_query), item.intent, item.page_type,
            item.proposed_path, item.offer_name, item.verified_offer,
            item.geography, item.device, item.status, item.reason_code)


def make_landing_opportunities(
    core: SemanticCore,
    profile: CompanyProfile | None,
    *,
    already_covered: set[str] | None = None,
    prior_candidates: dict[str, LandingCandidate] | None = None,
    limit: int = MAX_PAGES,
) -> LandingOpportunities:
    """Only new or changed suggestions; re-evaluate earlier blocked candidates."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_PAGES:
        raise ValueError("Invalid landing opportunity limit")
    if limit == 0:
        return LandingOpportunities()
    covered = {canonical_query(q) for q in (already_covered or ())}
    previous = prior_candidates or {}
    candidates: list[LandingCandidate] = []
    seen_queries: set[str] = set()
    seen_paths: set[str] = set()
    ordered = sorted(
        (c for c in core.clusters if c.intent in ("commercial", "transactional")),
        key=lambda c: (c.intent != "transactional", -len(c.engines), c.id),
    )
    for cluster in ordered:
        # One landing opportunity per search-intent cluster; creating a page
        # for every autocomplete variant would generate doorway-like copies.
        for query in (cluster.head_query,):
            key = canonical_query(query)
            if key in seen_queries or not key:
                continue
            seen_queries.add(key)
            offer = _offer_for(query, profile)
            verified = bool(
                offer and offer.release_status in {"live", "limited"}
                and any(ref.strip() for ref in offer.evidence_refs)
            )
            is_geo, area = _geo_hint(query, profile)
            device = _find_device(query)
            page_type = "location" if is_geo else "device" if device else "offer"
            path = _path_for(query)
            if path in seen_paths:
                # A true SHA collision must not silently omit an opportunity.
                # Full canonical input remains in the hash, not a truncated slug.
                path = f"/solutions/topic-{hashlib.sha256(key.encode()).hexdigest()}"
            seen_paths.add(path)
            if key in covered:
                reason, status = "already_covered", "blocked"
            elif is_geo:
                reason, status = (
                    ("local_value_required" if area else "unverified_geography"), "blocked"
                )
            elif not verified:
                reason, status = "offer_evidence_required", "blocked"
            elif device and not (
                offer and any(canonical_query(name) == device and ref.strip()
                              for name, ref in offer.supported_devices_evidence.items())
            ):
                reason, status = "device_evidence_required", "blocked"
            else:
                reason, status = "unique_content_required", "review"
            candidate = LandingCandidate(
                cluster_id=cluster.id,
                primary_query=query,
                intent=cluster.intent,
                page_type=page_type,
                proposed_path=path,
                offer_name=offer.name if verified else None,
                verified_offer=verified,
                geography=area,
                device=device,
                engines=cluster.engines,
                status=status,
                reason_code=reason,
            )
            prior = previous.get(key)
            if prior is not None:
                if _candidate_fingerprint(candidate) == _candidate_fingerprint(prior):
                    continue
                candidate.revision = prior.revision + 1
            candidates.append(candidate)
            if len(candidates) >= limit:
                return LandingOpportunities(candidates=candidates)
    return LandingOpportunities(candidates=candidates)


def _latest_prior_candidates(db: Session, product_id: UUID, task_id: UUID) -> dict[str, LandingCandidate]:
    result: dict[str, LandingCandidate] = {}
    rows = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == KIND,
        Artifact.task_id != task_id,
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()))
    for row in rows:
        try:
            plan = LandingOpportunities.model_validate_json(row.content)
        except ValueError:
            continue
        for candidate in plan.candidates:
            result.setdefault(canonical_query(candidate.primary_query), candidate)
    return result


def store_landing_opportunities(
    db: Session,
    product_id: UUID,
    task_id: UUID,
    core: SemanticCore,
    *,
    products_root: Path,
) -> Artifact:
    """Persist tenant-scoped, versioned differences; never publish a new URL."""
    require_enabled(db, product_id)
    # Task and Artifact each have a different FK, without a DB constraint
    # enforcing the same company. Fail closed before reading/reusing artifacts.
    if db.scalar(select(Task.id).where(
        Task.id == task_id, Task.product_id == product_id,
        Task.assigned_agent == "seo_factory",
    ).limit(1)) is None:
        raise LookupError("SEO factory run not found for company")
    if db.scalar(select(Product.id).where(
        Product.id == product_id).with_for_update()) is None:
        raise LookupError("Company not found")
    require_enabled(db, product_id)  # Re-check after the owner opt-out lock.
    previous = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == KIND,
    ).limit(1))
    if previous is not None:
        return previous
    product = db.get(Product, product_id)
    loaded = load_company_profile(products_root, product.slug)
    plan = make_landing_opportunities(
        core, loaded.profile, already_covered=planned_queries(db, product_id),
        prior_candidates=_latest_prior_candidates(db, product_id, task_id),
    )
    artifact = ArtifactService(db).create(
        product_id=product_id, task_id=task_id, kind=KIND,
        title=f"Programmatic SEO: {len(plan.candidates)} новых/изменённых кандидатов",
        content=plan.model_dump_json(),
    )
    db.commit()
    return artifact
