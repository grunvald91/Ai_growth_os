"""One-time ServGo release: revise two existing owner guides + write one NEW article.

Usage inside the already deployed Growth application:
    python -m app.seo.servgo_three_article_release          # inspect only
    python -m app.seo.servgo_three_article_release --apply  # real critic and publish

No global activation or stored quota changes. The isolated test-only allowance
applies to this process, this company, and these 3 articles. Failed critic or
unsupported claims fail closed; no fabricated test leads, no duplicate pages.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from urllib.parse import urlparse

from sqlalchemy import select, func

from app.core.composition import build_article_production
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus, Artifact, Publication
from app.seo.activation import is_enabled, editorial_preferences
from app.seo.queue import SEO_TASK_PREFIX
from app.seo import publishing
from app.seo import revise_servgo_owner_articles
from app.workflows.article_contracts import ArticleProductionInput

NEW_QUERY = "ии для продвижения бизнеса"
OLD_PAGES = {
    "/blog/zapis-k-psihologu": "/blog/kak-psihologu-organizovat-zapis-klientov",
    "/blog/zapisatsya-na-manikyur-onlayn": "/blog/kak-masteru-manikyura-vesti-zapis-klientov",
}
_LOG = logging.getLogger(__name__)


def _new_task(db, product_id):
    """Require the exact source-backed, previously queued *new* buyer-intent topic."""
    matched = []
    tasks = db.scalars(select(Task).where(
        Task.product_id == product_id,
        Task.assigned_agent == "content_factory",
        Task.title.like(f"{SEO_TASK_PREFIX}%"),
    ))
    for task in tasks:
        source = db.scalar(select(Artifact).where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "content_request",
        ).limit(1))
        if source is None:
            continue
        try:
            raw = json.loads(source.content)
            brief = raw.get("seo_brief") or {}
        except (TypeError, ValueError, AttributeError):
            continue
        if brief.get("primary_query") == NEW_QUERY:
            request = ArticleProductionInput.model_validate(raw)
            if request.product_id != product_id or request.seo_brief is None:
                raise ValueError("SEO article request does not belong to ServGo")
            matched.append((task, request))
    if len(matched) != 1:
        raise ValueError("The source-backed new ServGo article task is missing or ambiguous")
    return matched[0]


def _article_state(db, task):
    article = db.scalar(select(Artifact).where(
        Artifact.product_id == task.product_id,
        Artifact.task_id == task.id, Artifact.kind == "article").limit(1))
    published = (db.scalar(select(Publication).where(
        Publication.product_id == task.product_id,
        Publication.artifact_id == article.id,
        Publication.channel == "own_site").limit(1)) if article is not None else None)
    attempts = db.scalar(select(func.count()).select_from(Artifact).where(
        Artifact.product_id == task.product_id, Artifact.task_id == task.id,
        Artifact.kind == "seo_article_started"))
    return article, published, attempts


def _publish_for_this_release(db, product_id, artifact_id):
    """Only this one-off process can ignore the publication count, not the critic."""
    original = publishing.editorial_preferences

    def scoped_preference(session, queried_id, **kwargs):
        prefs = original(session, queried_id, **kwargs)
        return (replace(prefs, publications_per_day=30)
                if queried_id == product_id else prefs)

    publishing.editorial_preferences = scoped_preference
    try:
        return publishing.publish_if_clear(db, artifact_id, daily_limit=30)
    finally:
        publishing.editorial_preferences = original


def _check_production_preconditions():
    settings = get_settings()
    if settings.seo_autorun_enabled or settings.seo_servgo_autorun_enabled:
        raise RuntimeError("Stop: global SEO autorun changed; this release must be isolated")
    return settings


async def run(*, apply: bool) -> dict:
    settings = _check_production_preconditions()
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == "servgo"))
        if product is None or not is_enabled(db, product.id):
            raise RuntimeError("ServGo SEO is not explicitly enabled")
        product_id = product.id
        new_task, request = _new_task(db, product_id)
        article, pub, attempts = _article_state(db, new_task)
        existing = {
            "topic": NEW_QUERY, "task_status": new_task.status.value,
            "prior_attempts": attempts, "article_exists": bool(article),
            "already_published": bool(pub and pub.status == "published"),
        }
    # The existing rewrite command enforces BOTH old-page versions, evidence,
    # an independent critic per page, backup, and an atomic two-page transaction.
    # Read-only preflight never calls the provider or writes the database.
    preflight = await revise_servgo_owner_articles.run(False)
    report = {"old_preflight": [
        {"slug": item.get("old") or item.get("new_slug"),
         "state": item.get("state")} for item in preflight
    ], "new_preflight": existing}
    if not apply:
        report["status"] = "inspection_only"
        return report

    # A previous release can be re-inspected, but must not create duplicates.
    rewrites = await revise_servgo_owner_articles.run(True)
    report["rewrites"] = [{k: item[k] for k in (
        "old_slug", "new_slug", "state", "version", "critic") if k in item}
        for item in rewrites]
    if len(report["rewrites"]) != 2:
        raise RuntimeError("Two existing articles were not both verified/revised")

    # Run only one NEW article. Do not consume unrelated owners' quota and do not
    # disable the independent critic, fact checker, tenant isolation or idempotency.
    with SessionLocal() as db:
        db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
        if not is_enabled(db, product_id):
            raise RuntimeError("ServGo disabled SEO during the release")
        task, new_request = _new_task(db, product_id)
        article, published, attempts = _article_state(db, task)
        if published is not None and published.status == "published":
            report["new_article"] = {"state": "already_published",
                                     "slug": published.external_reference}
            return report
        if task.status == TaskStatus.COMPLETED and article is not None:
            outcome = _publish_for_this_release(db, product_id, article.id)
        else:
            if task.status != TaskStatus.QUEUED or article is not None:
                raise RuntimeError("New article must be queued with no prior published/draft article")
            if attempts > 1:
                raise RuntimeError("Too many previous SEO attempts; requires manual failure review")
            if task.due_at is not None:
                due = task.due_at.replace(tzinfo=timezone.utc) if task.due_at.tzinfo is None else task.due_at
                if due > datetime.now(timezone.utc):
                    raise RuntimeError("New article is scheduled for a future date")
            db.commit()  # release the preflight product lock before normal worker lock order
            pipeline = build_article_production(db, settings, force_critic=True)
            pipeline.seo_default_articles_per_day = 0  # one-process release exception only
            result = await pipeline.run(new_request.model_copy(update={"task_id": task.id}))
            if result.status != "completed":
                report["new_article"] = {
                    "state": "generation_failed",
                    "reason_code": (result.error or "unknown")[:160],
                }
                return report
            # Only a final artifact with a clean independent critic can publish.
            outcome = _publish_for_this_release(db, product_id, result.artifact_ids[0])
        report["new_article"] = {
            "state": "published" if outcome.published else "held_for_review",
            "reason": outcome.reason[:160],
            "slug": "/blog/" + outcome.slug if outcome.slug else None,
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Run paid model calls and publish when critic passes")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(apply=args.apply))
        print("SERVGO_THREE_ARTICLES=" + json.dumps(report, ensure_ascii=False, default=str), flush=True)
    except Exception as exc:
        _LOG.exception("Three-article release aborted safely: %s", type(exc).__name__)
        print("SERVGO_THREE_ARTICLES=" + json.dumps({
            "state": "failed_closed", "error_class": type(exc).__name__,
        }), flush=True)
        raise


if __name__ == "__main__":
    main()
