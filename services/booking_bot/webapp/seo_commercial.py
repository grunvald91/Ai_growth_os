"""ServGo SEO article -> verified CTA click -> commercial lead attribution.

Click rows are anonymous: article slug, selected product family and timestamp.
No cookies, IP addresses, user agents or contact details are stored here.
Contacts remain exclusively in the existing marketing_leads table, after consent.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from aiohttp import web
from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, and_, func, select

logger = logging.getLogger(__name__)
metadata = MetaData()
clicks = Table(
    "servgo_seo_commercial_clicks", metadata,
    Column("id", String(32), primary_key=True),
    Column("article_slug", String(120), nullable=False),
    Column("module", String(24), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_servgo_seo_clicks_created", "created_at"),
    Index("ix_servgo_seo_clicks_article", "article_slug", "module"),
)

SLUG = re.compile(r"^[a-z0-9-]{1,120}$")
TOKEN = re.compile(r"^[0-9a-f]{32}$")
MODULES = {
    "platform": "ServGo: вся платформа",
    "booking": "BookingBot — запись и расписание",
    "growth": "Growth — ИИ-маркетинг",
    "mail": "Mail — почтовые кампании",
    "shifts": "Смена — учёт смен",
    "seo": "SEO-завод — статьи и поиск",
}


def _private(response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def article_cta_url(slug: str) -> str:
    """Only a canonical article slug enters a public CTA path."""
    return f"/blog/cta/{slug}" if isinstance(slug, str) and SLUG.fullmatch(slug) else "/#launch"


async def verify_click(engine, token: str, slug: str, module: str) -> bool:
    """Verify an actual persisted CTA event, not forged browser UTM values."""
    if (not isinstance(token, str) or not TOKEN.fullmatch(token)
            or not isinstance(slug, str) or not SLUG.fullmatch(slug)
            or module not in MODULES):
        return False
    try:
        async with engine.connect() as conn:
            row = await conn.scalar(
                select(clicks.c.id).where(
                    clicks.c.id == token, clicks.c.article_slug == slug,
                    clicks.c.module == module,
                ).limit(1)
            )
        return row is not None
    except Exception:
        logger.exception("SEO commercial click verification temporarily unavailable")
        return False


def register_seo_commercial_routes(app, *, engine, get_user, admin_ids, public_origin):
    """Register with existing ServGo DB and owner/admin login; no second CRM."""
    reviewers = set(int(v) for v in admin_ids)
    app["seo_click_table_ready"] = False

    async def create_click_table(application):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            application["seo_click_table_ready"] = True
        except Exception:
            logger.exception("SEO commercial click table setup failed")

    app.on_startup.append(create_click_table)

    async def click(request):
        slug = request.match_info["article_slug"]
        if not SLUG.fullmatch(slug):
            raise web.HTTPNotFound()
        module = request.query.get("module", "platform")
        if module not in MODULES:
            raise web.HTTPNotFound()
        # No standalone landing page is invented for a module. The currently
        # working platform request form has a concrete selector for every module.
        from webapp.blog_content import BLOG_ARTICLES
        from webapp.blog_feed import all_articles
        known = await all_articles(BLOG_ARTICLES)
        if not any(a["slug"] == slug for a in known):
            raise web.HTTPNotFound()

        token = None
        if request.app["seo_click_table_ready"]:
            candidate = uuid4().hex
            try:
                async with engine.begin() as conn:
                    await conn.execute(clicks.insert().values(
                        id=candidate, article_slug=slug, module=module,
                        created_at=datetime.now(timezone.utc)))
                token = candidate
            except Exception:
                logger.exception("SEO CTA click storage failed; redirect without attribution token")

        params = {
            "utm_source": "servgo_blog",
            "utm_medium": "seo",
            "utm_campaign": f"seo_{module}",
            "utm_content": slug,
            "seo_module": module,
        }
        if token:
            params["utm_term"] = token
        # The URL is constructed, never read from a request-controlled 'next'.
        destination = public_origin.rstrip("/") + "/?" + urlencode(params) + "#launch"
        raise web.HTTPSeeOther(destination, headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
        })

    async def authorized(request):
        if not reviewers:
            return False
        user = await get_user(request)
        try:
            return bool(user) and int(user["id"]) in reviewers
        except (TypeError, KeyError, ValueError):
            return False

    async def report(request):
        if not await authorized(request):
            return _private(web.json_response({"error": "Нет доступа"}, status=403))
        if not request.app["seo_click_table_ready"]:
            return _private(web.json_response({"error": "Учёт переходов недоступен"}, status=503))
        try:
            days = int(request.query.get("days", "90"))
        except (ValueError, TypeError):
            return _private(web.json_response({"error": "Некорректный период"}, status=422))
        if not 1 <= days <= 365:
            return _private(web.json_response({"error": "Период: 1–365 дней"}, status=422))

        from database.models import MarketingLead
        since = datetime.now(timezone.utc) - timedelta(days=days)
        # Only actually accepted business requests count. A click does not
        # become a lead, and a failed /api/landing/connect-request cannot
        # be misreported as a successful request.
        matched = and_(
            MarketingLead.utm_term == clicks.c.id,
            MarketingLead.utm_source == "servgo_blog",
            MarketingLead.utm_medium == "seo",
            MarketingLead.utm_content == clicks.c.article_slug,
            MarketingLead.utm_campaign == ("seo_" + clicks.c.module),
            MarketingLead.attribution_confidence == "verified",
            MarketingLead.status == "received",
        )
        rows_query = (
            select(
                clicks.c.article_slug, clicks.c.module,
                func.count(func.distinct(clicks.c.id)).label("clicks"),
                func.count(func.distinct(MarketingLead.id)).label("leads"),
            ).select_from(clicks.outerjoin(MarketingLead, matched))
            .where(clicks.c.created_at >= since)
            .group_by(clicks.c.article_slug, clicks.c.module)
            .order_by(func.count(func.distinct(MarketingLead.id)).desc(),
                      clicks.c.article_slug, clicks.c.module)
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(rows_query)).all()
        items = [{"article_slug": slug, "module": module,
                  "cta_clicks": int(nclicks), "accepted_leads": int(nleads)}
                 for slug, module, nclicks, nleads in rows]
        return _private(web.json_response({
            "tracking_enabled": True, "days": days, "articles": items,
            "total_cta_clicks": sum(x["cta_clicks"] for x in items),
            "total_accepted_leads": sum(x["accepted_leads"] for x in items),
            "meaning": "Anonymous CTA events and received business connection requests, not bookings or paid customers",
        }))

    app["seo_verify_click"] = lambda token, slug, module: verify_click(engine, token, slug, module)
    app["seo_report_handler"] = report
    app.router.add_get("/blog/cta/{article_slug:[a-z0-9-]+}", click)
    # Production /api/landing/connect-request already passes the nginx proxy.
    # The authenticated read is dispatched there, without adding a new route.
