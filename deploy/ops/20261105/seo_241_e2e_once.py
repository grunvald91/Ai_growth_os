"""One-time #241 production canary. Only the three pre-existing ServGo owner-buyer SEO jobs.

No global flags, DB quota changes, tenant activation changes, fake leads or secrets
in logs. Quota is bypassed only within this ephemeral process, not any worker.
The normal independent critic, verified-facts and publication guards stay on.
"""
import asyncio
import dataclasses
import json
import re
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import select, func
from app.core.config import get_settings
from app.core.composition import build_article_production
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus, Artifact, Publication, AgentRun
from app.seo.activation import require_enabled, editorial_preferences as base_preferences
from app.seo.queue import SEO_TASK_PREFIX
from app.workflows.article_contracts import ArticleProductionInput
import app.seo.publishing as publishing

EXPECTED_RELEASE = "08b62d0414da8985009ee004b45b7f01dc0f622e"
QUERIES = (
    "программа онлайн записи клиентов",
    "ии для продвижения бизнеса",
    "как привлечь клиентов в салон красоты",
)
MAX_ATTEMPTS_PER_TOPIC = 3
REPORT = {q: {"result": "not_started"} for q in QUERIES}


def log(kind, payload):
    print("SEO241 " + kind + " " + json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def find_tasks(db, product_id):
    tasks = list(db.scalars(select(Task).where(
        Task.product_id == product_id,
        Task.assigned_agent == "content_factory",
        Task.title.like(f"{SEO_TASK_PREFIX}%"),
    ).order_by(Task.created_at.desc())))
    matches = {q: [] for q in QUERIES}
    for task in tasks:
        row = db.scalar(select(Artifact).where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "content_request").limit(1))
        if row is None:
            continue
        try:
            payload = json.loads(row.content)
            query = (payload.get("seo_brief") or {}).get("primary_query")
            if query in matches and payload.get("product_id") == str(product_id):
                matches[query].append((task, row))
        except (ValueError, TypeError):
            continue
    if any(len(matches[q]) != 1 for q in QUERIES):
        raise RuntimeError("Canary identity mismatch; refusing to use unrelated task")
    return {q: matches[q][0] for q in QUERIES}


def artifacts(db, task):
    article = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "article").limit(1))
    count = db.scalar(select(func.count()).select_from(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "seo_article_started"))
    pub = None
    if article:
        pub = db.scalar(select(Publication).where(
            Publication.artifact_id == article.id,
            Publication.product_id == task.product_id,
            Publication.channel == "own_site").limit(1))
    return article, count or 0, pub


def safe_last_failure(db, task):
    rows = list(db.scalars(select(AgentRun).where(
        AgentRun.task_id == task.id
    ).order_by(AgentRun.created_at.desc()).limit(10)))
    for row in rows:
        if row.status == "failed":
            return (row.error or "no_class").split(":", 2)[:2]
    return ["none"]


def temporary_publication_quota(company_id):
    original = publishing.editorial_preferences

    def test_only(db, product_id, **kwargs):
        result = original(db, product_id, **kwargs)
        if product_id != company_id:
            return result
        return dataclasses.replace(result, publications_per_day=30)

    publishing.editorial_preferences = test_only
    return original


def do_publish(db, company_id, task):
    article, starts, pub = artifacts(db, task)
    if article is None:
        return {"result": "no_article", "attempts": starts}
    if pub is not None and pub.status == "published":
        return {"result": "published", "slug": pub.external_reference,
                "attempts": starts, "already_published": True}
    original = temporary_publication_quota(company_id)
    try:
        outcome = publishing.publish_if_clear(db, article.id, daily_limit=30)
    finally:
        publishing.editorial_preferences = original
    return {"result": "published" if outcome.published else "held",
            "reason": outcome.reason[:160], "slug": ("/blog/" + outcome.slug) if outcome.slug else None,
            "attempts": starts}


async def main():
    settings = get_settings()
    if settings.release_sha != EXPECTED_RELEASE:
        raise RuntimeError("Production release changed: a new code review is required")
    if settings.seo_autorun_enabled or settings.seo_servgo_autorun_enabled:
        raise RuntimeError("Global automatic SEO scheduling unexpectedly enabled")
    with SessionLocal() as db:
        company = db.scalar(select(Product).where(Product.slug == "servgo"))
        if company is None:
            raise RuntimeError("Canonical ServGo company not found")
        require_enabled(db, company.id)
        company_id = company.id
        tasks = find_tasks(db, company_id)
        prefs = base_preferences(db, company_id)
        log("PREFLIGHT", {"release": settings.release_sha[:12],
                          "global_autorun": False,
                          "service": "servgo",
                          "quota_override": "in-process generation+publication ONLY",
                          "normal_articles_per_day": prefs.articles_per_day,
                          "normal_publications_per_day": prefs.publications_per_day})
        for query in QUERIES:
            task, request_row = tasks[query]
            # Check the task again immediately before each action.
            db.refresh(task)
            require_enabled(db, company_id)
            article, starts, pub = artifacts(db, task)
            status = task.status.value
            log("BEFORE", {"query": query, "status": status, "starts": starts,
                           "has_article": bool(article),
                           "published": bool(pub and pub.status == "published")})
            if pub and pub.status == "published":
                REPORT[query] = do_publish(db, company_id, task)
                continue
            if status == TaskStatus.COMPLETED.value:
                REPORT[query] = do_publish(db, company_id, task)
                log("AFTER", {"query": query, **REPORT[query]})
                continue
            if status == TaskStatus.RUNNING.value:
                REPORT[query] = {"result": "already_running"}
                continue
            if status == TaskStatus.FAILED.value:
                if starts >= MAX_ATTEMPTS_PER_TOPIC or article is not None:
                    REPORT[query] = {"result": "failed_not_retried", "attempts": starts,
                                     "error_class": safe_last_failure(db, task)}
                    continue
                # One previously broken canary may be retried. Do not retry
                # blocked factual/critic/approval decisions.
                if db.scalar(select(Artifact.id).where(
                    Artifact.task_id == task.id, Artifact.kind == "seo_final_review").limit(1)):
                    REPORT[query] = {"result": "critic_review_present_no_requeue"}
                    continue
                lock = db.scalar(select(Product.id).where(
                    Product.id == company_id).with_for_update())
                db.refresh(task)
                if lock is None or task.status != TaskStatus.FAILED:
                    db.rollback()
                    REPORT[query] = {"result": "concurrent_change"}
                    continue
                task.status = TaskStatus.QUEUED
                db.commit()
                log("REQUEUED", {"query": query, "previous_attempts": starts})
            elif status != TaskStatus.QUEUED.value:
                REPORT[query] = {"result": "unexpected_status", "status": status}
                continue
            if task.due_at is not None:
                due = task.due_at.replace(tzinfo=timezone.utc) if task.due_at.tzinfo is None else task.due_at
                if due > datetime.now(timezone.utc):
                    REPORT[query] = {"result": "future_schedule_not_overridden"}
                    continue
            raw = json.loads(request_row.content)
            request = ArticleProductionInput.model_validate(raw)
            if request.product_id != company_id or request.seo_brief.primary_query != query:
                raise RuntimeError("Request tenant or topic changed")
            log("BEGIN_REAL_PIPELINE", {"query": query, "attempt": starts + 1})
            factory = build_article_production(db, settings, force_critic=True)
            # Scope test quota bypass to this single process / three identified
            # ServGo tasks. Permanent settings and background workers stay unchanged.
            factory.seo_default_articles_per_day = 0
            try:
                result = await factory.run(request.model_copy(update={"task_id": task.id}))
            except Exception as exc:
                REPORT[query] = {"result": "uncaught_pipeline_error",
                                 "error_class": type(exc).__name__}
                log("AFTER", {"query": query, **REPORT[query]})
                db.rollback()
                continue
            if result.status != "completed":
                REPORT[query] = {"result": "pipeline_failed",
                                 "error_class": (result.error or "unknown")[:150],
                                 "retryable": result.retryable}
            else:
                REPORT[query] = do_publish(db, company_id, task)
            log("AFTER", {"query": query, **REPORT[query]})

    await public_checks()


async def public_checks():
    import urllib.request
    from html.parser import HTMLParser
    from urllib.parse import urlparse
    from xml.etree import ElementTree

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.canonical = []
            self.cta = []
        def handle_starttag(self, tag, attrs):
            if tag not in ("a", "link"):
                return
            props = dict(attrs)
            if tag == "link" and props.get("rel") == "canonical":
                self.canonical.append(props.get("href"))
            if tag == "a" and str(props.get("href", "")).startswith("/blog/cta/"):
                self.cta.append(props["href"])

    def get(path):
        url = "https://servgo.ru" + path
        req = urllib.request.Request(url, headers={"User-Agent": "ServGo-SEO-acceptance/1.0"})
        with urllib.request.urlopen(req, timeout=18) as resp:
            return resp.status, resp.read(1_500_000).decode("utf-8", "replace")

    published = {q: v["slug"] for q, v in REPORT.items()
                 if v.get("result") == "published" and v.get("slug")}
    if not published:
        log("HTTP", {"result": "no_new_or_existing_published_canaries"})
        return
    # The public BookingBot feed has a documented 300 s cache TTL.
    for attempt in range(34):
        try:
            sitemap_status, sitemap = get("/blog/sitemap.xml")
            robots_status, robots = get("/robots.txt")
            missing = [slug for slug in published.values()
                       if "https://servgo.ru" + slug not in sitemap]
            if sitemap_status == 200 and robots_status == 200 and not missing:
                break
        except Exception:
            pass
        if attempt != 33:
            await asyncio.sleep(10)
    for q, slug in published.items():
        entry = {"slug": slug}
        try:
            status, html = get(slug)
            links = Links()
            links.feed(html)
            entry.update(
                status=status,
                canonical_ok=("https://servgo.ru" + slug in links.canonical),
                sitemap_ok=("https://servgo.ru" + slug in sitemap),
                cta_ok=any(a.startswith("/blog/cta/" + slug.split("/")[-1])
                           for a in links.cta),
                robots_ok=("Disallow: /blog" not in robots),
                html_title=("<title>" in html),
            )
        except Exception as exc:
            entry.update(error_type=type(exc).__name__, status="network_error")
        REPORT[q]["public_http"] = entry
        log("PUBLIC", {"query": q, **entry})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        log("ABORT", {"error_type": type(exc).__name__, "details": str(exc)[:190]})
        raise
    finally:
        log("FINAL", REPORT)
    passed = sum(v.get("result") == "published"
                 and v.get("public_http", {}).get("status") == 200
                 and v.get("public_http", {}).get("canonical_ok")
                 and v.get("public_http", {}).get("sitemap_ok")
                 and v.get("public_http", {}).get("cta_ok")
                 and v.get("public_http", {}).get("robots_ok")
                 for v in REPORT.values())
    log("ACCEPTANCE", {"passed": passed, "required": 3,
                       "failure_matrix": "see targeted pytest logs",
                       "signup": "never simulated: no fake customer leads"})
    if passed < 3:
        sys.exit(2)
