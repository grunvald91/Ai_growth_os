"""What the ServGo mail needs from the studio: the mail agent's model and the company it works for.

ServGo's mail runs in another process on another codebase, so it cannot read the studio's router
file or company profiles. It asks here instead, with the shared key the two applications already use
for each other (X-Studio-Key = SERVGO_INTERNAL_KEY). The paths sit under /studio so the existing nginx
rule for growth.servgo.ru already forwards them. Nothing here returns clients' personal data.
"""
from __future__ import annotations

import logging
import secrets
from collections import Counter
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from app.company_profile import load_company_profile, sanitized_profile
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.llm.router_store import RouterStoreError, read_state, validate_configuration
from app.models.domain import Product

router = APIRouter()
_logger = logging.getLogger(__name__)

# Only roles whose work happens in ServGo are shared; everything else stays internal.
SHARED_ROLES = frozenset({"mail_agent"})
BOOKING_DAYS = 90


def require_servgo(x_studio_key: str = Header(default=""), settings: Settings = Depends(get_settings)) -> Settings:
    key = settings.servgo_internal_key or ""
    if len(key) < 32 or not secrets.compare_digest(x_studio_key.encode(), key.encode()):
        raise HTTPException(401, "Нет доступа")
    return settings


@router.get("/studio/internal/model/{role}")
def model_for_role(role: str, settings: Settings = Depends(require_servgo)):
    """The same chain the team page shows for the role: the owner's pick, or the automatic route."""
    if role not in SHARED_ROLES:
        raise HTTPException(404, "Роль не найдена")
    from app.api.team_dashboard import _decision
    try:
        config = read_state(settings).config
        if not config.enabled:
            return {"role": role, "models": []}  # Router off on the team page: the mail keeps its own model.
        models = _decision(validate_configuration(config), role, "main").get("models") or []
    except (RouterStoreError, ValueError, OSError):
        models = []
    return {"role": role, "models": [m for m in models if isinstance(m, str) and m]}


@router.get("/studio/internal/companies")
def companies(_: Settings = Depends(require_servgo), db=Depends(get_db)):
    rows = db.scalars(select(Product).order_by(Product.name))
    return [{"id": str(p.id), "name": p.name, "slug": p.slug} for p in rows]


def _clean(values) -> list[str]:
    return [" ".join(str(v).split()) for v in values or [] if v is not None and str(v).strip()]


def booked_services(db, settings: Settings, product: Product) -> list[dict]:
    """What clients actually book at the linked ServGo business: service names and counts only."""
    from app.schedule.source import source_for
    source = source_for(db, settings, product, "")
    if source is None:
        return []
    today = date.today()
    try:
        schedule = source.fetch(today - timedelta(days=BOOKING_DAYS), today + timedelta(days=30))
    except Exception as exc:  # a missing schedule never blocks the search
        _logger.warning("internal_brief_schedule_failed %s", type(exc).__name__)
        return []
    if schedule is None:
        return []
    counts = Counter(b.service for b in schedule.bookings
                     if b.service and b.status not in {"cancelled", "canceled", "rejected"})
    return [{"service": name, "bookings": total} for name, total in counts.most_common()]


def company_brief(db, settings: Settings, product: Product) -> dict:
    """Everything the mail agent needs to know who this company is and whom it serves."""
    loaded = load_company_profile(settings.products_root, product.slug)
    data = sanitized_profile(loaded.profile) if loaded.profile else {}
    identity, offer, customers = data.get("identity", {}), data.get("offer", {}), data.get("customers", {})
    proof = data.get("proof", {})
    brief = {
        "id": str(product.id),
        "name": identity.get("display_name") or identity.get("name") or product.name,
        "about": identity.get("one_liner") or product.description or "",
        "category": identity.get("category") or identity.get("industry") or "",
        "website": identity.get("website") or "",
        "geography": _clean(identity.get("service_geography")) or _clean([identity.get("geography")]),
        "offer": offer.get("description") or offer.get("name") or "",
        "services": [{"name": s.get("name", ""), "description": s.get("description", "")}
                     for s in offer.get("products_services", []) if s.get("name")],
        "call_to_action": offer.get("primary_conversion_action") or "",
        "ideal_customer": customers.get("ideal_customer") or "",
        "customer_segments": _clean(customers.get("primary_segments"))
                             + _clean(s.get("name") for s in customers.get("segments", [])),
        "customer_pains": _clean(customers.get("pains")),
        "not_our_customers": _clean(customers.get("exclusions")),
        "verified_facts": _clean(f.get("statement") for f in proof.get("approved_facts", [])),
        "booked_services": booked_services(db, settings, product),
        "profile_source": loaded.source,
    }
    return brief


@router.get("/studio/internal/companies/{product_id}/brief")
def brief(product_id: UUID, settings: Settings = Depends(require_servgo), db=Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Компания не найдена")
    return company_brief(db, settings, product)
