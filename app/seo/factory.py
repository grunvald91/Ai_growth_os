"""The SEO factory as one background job: seeds -> real suggestions -> clusters -> funnel plan -> article tasks."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.composition import build_seo_llm
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.planner import plan_content, plan_seeds
from app.seo.queue import queue_plan
from app.seo.service import build_semantic_core
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.seo.activation import require_enabled, enabled_product_ids

_logger = logging.getLogger(__name__)
AGENT = "seo_factory"
REQUEST_KIND = "seo_factory_request"
REPORT_KIND = "seo_factory_report"
ATTEMPT_KIND = "seo_factory_attempt"


def recover_stale_factory(db):
    """The lease exceeds the hard run deadline, so expired work cannot still run."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    if db.bind.dialect.name == "sqlite":
        cutoff = cutoff.replace(tzinfo=None)
    for task in db.scalars(select(Task).where(Task.assigned_agent == AGENT,
            Task.status == TaskStatus.RUNNING, Task.updated_at < cutoff)):
        attempts = list(db.scalars(select(Artifact.id).where(
            Artifact.task_id == task.id, Artifact.kind == ATTEMPT_KIND)))
        task.status = TaskStatus.QUEUED if len(attempts) < 3 else TaskStatus.FAILED
    db.commit()


def queue_factory(db: Session, product_id: UUID, focus: str | None = None, *, commit: bool = True) -> Task:
    """One active factory run per company, including simultaneous dispatcher/UI requests."""
    # Serialize *all* producer paths (dispatcher and Studio form) on the
    # company row. The partial unique constraint is not present in old schemas.
    product = db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    require_enabled(db, product_id)
    active = db.scalar(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT,
                                          Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if active is not None:
        return active
    task = TaskService(db).create(product_id=product_id, title="SEO-завод: запросы, план и статьи", assigned_agent=AGENT,
                                  status=TaskStatus.QUEUED, priority=50,
                                  description="Собрать запросы клиентов, составить контент-план под воронку и поставить статьи в работу.")
    ArtifactService(db).create(product_id=product_id, task_id=task.id, kind=REQUEST_KIND, title="Запрос на SEO-завод",
                               content=json.dumps({"focus": focus}, ensure_ascii=False))
    if commit:
        db.commit()
    return task


async def run_factory_once(session_factory, settings, *, provider=None, client: httpx.AsyncClient | None = None) -> int:
    with session_factory() as db:
        recover_stale_factory(db)
        enabled = enabled_product_ids(db)
        if not enabled:
            return 0
        task = db.scalar(select(Task).where(
            Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED,
            Task.product_id.in_(enabled),
        ).order_by(Task.priority.desc(), Task.created_at, Task.id).limit(1))
        if task is None:
            return 0
        task_id, product_id = task.id, task.product_id
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=ATTEMPT_KIND,
                                   title="SEO attempt", content="{}")
        db.commit()
        request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == REQUEST_KIND))
        try:
            focus = (json.loads(request.content).get("focus") if request else None) or None
            async with asyncio.timeout(1200):
                await _execute(db, settings, task_id, product_id, focus, provider, client)
        except Exception as exc:
            db.rollback()
            _logger.warning("SEO factory failed: %s", type(exc).__name__)
            ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=REPORT_KIND,
                title="SEO-завод остановился", content=json.dumps({"error": type(exc).__name__}))
            from app.seo.planner import PLAN_KIND
            attempts = list(db.scalars(select(Artifact.id).where(
                Artifact.task_id == task_id, Artifact.kind == ATTEMPT_KIND)))
            resumable = db.scalar(select(Artifact.id).where(Artifact.task_id == task_id,
                                                           Artifact.kind == PLAN_KIND)) is not None
            retry = len(attempts) < 3 and (resumable or isinstance(exc, TimeoutError))
            TaskService(db).update(task_id, status=TaskStatus.QUEUED if retry else TaskStatus.FAILED)
            db.commit()
        return 1


async def _execute(db, settings, task_id, product_id, focus, provider, client):
    from app.seo.contracts import SemanticCore
    from app.seo.planner import PLAN_KIND
    from app.seo.service import KIND
    require_enabled(db, product_id)
    llm, privacy = build_seo_llm(settings, provider)
    core_artifact = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == KIND))
    if core_artifact is None:
        checkpoint = db.scalar(select(Artifact).where(Artifact.task_id == task_id,
                                                       Artifact.kind == "seo_suggest_checkpoint"))
        seeds = (json.loads(checkpoint.content)["seeds"] if checkpoint else
                 await plan_seeds(db, llm, privacy, product_id, products_root=settings.products_root,
                                  model=settings.llm_model, focus=focus))
        core_artifact = await build_semantic_core(db, product_id, seeds, client=client, task_id=task_id)
    core = SemanticCore.model_validate_json(core_artifact.content)
    plan = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == PLAN_KIND))
    if plan is None:
        plan = await plan_content(db, llm, privacy, product_id, core, products_root=settings.products_root,
                                  model=settings.llm_model, factory_task_id=task_id)
    require_enabled(db, product_id)
    queued = queue_plan(db, plan, {c.id: c for c in core.clusters})
    ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=REPORT_KIND,
        title=f"SEO-завод: поставлено статей — {len(queued)}",
        content=json.dumps({"seeds": len(core.seeds), "clusters": len(core.clusters),
                            "articles_queued": len(queued), "sources": [s.model_dump() for s in core.sources]}))
    TaskService(db).update(task_id, status=TaskStatus.COMPLETED)
    db.commit()
