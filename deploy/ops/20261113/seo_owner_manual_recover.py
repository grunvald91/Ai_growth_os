"""One-time, owner-approved retry for exactly three pre-existing ServGo SEO tasks.

Format-only production patch must be deployed first. This process does NOT
change the global or company SEO quotas. It applies a *per-process* generation
quota exception to exactly the selected tasks. All original source / tenant,
critic, safety and publication validations remain active.

MODE=recover_failed: 1 failed task, then stop; if unsuccessful, do not process queued.
MODE=process_queued: run up to 2 waiting tasks, stop on first generation error.

Only prints safe statuses and error classes, no user/company drafts.
"""
import asyncio
import json
import os
from sqlalchemy import select

from app.core.config import get_settings
from app.core.composition import build_article_production
from app.db.session import SessionLocal
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.activation import require_enabled
from app.seo.article_retry import retry_failed_article
from app.seo.publishing import publish_if_clear
from app.seo.queue import SEO_TASK_PREFIX
from app.workflows.article_contracts import ArticleProductionInput
from app.workflows.article_production import _stage_schema_repair_hint


async def main() -> None:
    mode = os.environ.get("MODE")
    if mode not in {"recover_failed", "process_queued"}:
        raise SystemExit("Invalid operator mode")
    hint = _stage_schema_repair_hint("article_writer", "sections.0.kind:literal_error")
    if "kind must be exactly lowercase" not in hint:
        raise SystemExit("Patched SEO writer not installed in production")
    settings = get_settings()
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == "servgo"))
        if product is None:
            raise SystemExit("ServGo company not found")
        require_enabled(db, product.id)
        all_articles = list(db.scalars(select(Task).where(
            Task.product_id == product.id,
            Task.assigned_agent == "content_factory",
            Task.title.like(f"{SEO_TASK_PREFIX}%"),
        ).order_by(Task.created_at.asc(), Task.id.asc())))
        failed = [t for t in all_articles if t.status == TaskStatus.FAILED]
        queued = [t for t in all_articles if t.status == TaskStatus.QUEUED]
        print("SEO_OWNER_PREFLIGHT", "failed", len(failed), "queued", len(queued), flush=True)
        if mode == "recover_failed":
            if len(failed) != 1 or len(queued) != 2:
                raise SystemExit("Expected exactly one failed and two queued tasks")
            chosen = failed
        else:
            if failed:
                # Stop, rather than spending more after an unsuccessful first recovery.
                raise SystemExit("An SEO article failed; resolve it before processing queued")
            if not 1 <= len(queued) <= 2:
                raise SystemExit("Unexpected number of queued SEO articles")
            chosen = queued[:2]

        for task in chosen:
            if mode == "recover_failed":
                retry_failed_article(db, product_id=product.id, task_id=task.id,
                                     actor="owner_manual_quota_exception_20261113")
            latest = db.get(Task, task.id)
            if latest.status != TaskStatus.QUEUED:
                raise SystemExit("Target task is not queued")
            if db.scalar(select(Artifact.id).where(
                Artifact.product_id == product.id, Artifact.task_id == task.id,
                Artifact.kind == "article"
            ).limit(1)):
                raise SystemExit("Article already exists for queued task")
            source = db.scalar(select(Artifact).where(
                Artifact.product_id == product.id, Artifact.task_id == task.id,
                Artifact.kind == "content_request"
            ).limit(1))
            if source is None:
                raise SystemExit("Missing original article request")
            data = ArticleProductionInput.model_validate_json(source.content)
            if (data.product_id != product.id or not data.seo_brief or not data.light
                    or data.channel != "own_site" or data.produce_ready_assets):
                raise SystemExit("Original SEO request identity/format mismatch")
            # Scoped exception: one pre-existing task at a time, never new SEO jobs.
            pipeline = build_article_production(db, settings, force_critic=True)
            pipeline.seo_default_articles_per_day = 0
            print("SEO_OWNER_GENERATION_START", flush=True)
            result = await pipeline.run(data.model_copy(update={"task_id": task.id}))
            print("SEO_OWNER_GENERATION_RESULT", result.status,
                  "error_code", (result.error or "none").split(":")[0][:80], flush=True)
            if result.status != "completed":
                raise SystemExit("SEO generation failed; stopped before any next task")
            try:
                outcome = publish_if_clear(db, result.artifact_ids[0],
                                           daily_limit=settings.seo_publications_per_day)
                print("SEO_OWNER_PUBLICATION_RESULT",
                      "published" if outcome.published else "held_for_review", flush=True)
            except Exception as exc:
                db.rollback()
                print("SEO_OWNER_PUBLICATION_CHECK_FAILED", type(exc).__name__, flush=True)
                # Successful article is preserved even if publishing rejected.
    print("SEO_OWNER_RECOVERY_DONE", mode, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
