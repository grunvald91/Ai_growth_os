"""The SEO factory as one background job: seeds -> real suggestions -> clusters -> funnel plan -> article tasks."""
import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.composition import build_seo_llm
from app.models.domain import Artifact, Task, TaskStatus
from app.seo.planner import plan_content, plan_seeds
from app.seo.queue import queue_plan
from app.seo.service import build_semantic_core
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService

_logger = logging.getLogger(__name__)
AGENT = "seo_factory"
REQUEST_KIND = "seo_factory_request"
REPORT_KIND = "seo_factory_report"


def queue_factory(db: Session, product_id: UUID, focus: str | None = None, *, commit: bool = True) -> Task:
    """One active factory run per company: asking again while one is queued or running returns that run."""
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
        task = db.scalar(select(Task).where(Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED)
                         .order_by(Task.priority.desc(), Task.created_at, Task.id).limit(1))
        if task is None:
            return 0
        task_id, product_id = task.id, task.product_id
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        db.commit()
        request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == REQUEST_KIND))
        focus = (json.loads(request.content).get("focus") if request else None) or None
        stage = "seeds"
        try:
            llm, privacy = build_seo_llm(settings, provider)
            seeds = await plan_seeds(db, llm, privacy, product_id, products_root=settings.products_root,
                                     model=settings.llm_model, focus=focus)
            stage = "semantic_core"
            core_artifact = await build_semantic_core(db, product_id, seeds, client=client)
            from app.seo.contracts import SemanticCore
            core = SemanticCore.model_validate_json(core_artifact.content)
            stage = "content_plan"
            plan = await plan_content(db, llm, privacy, product_id, core, products_root=settings.products_root,
                                      model=settings.llm_model)
            stage = "queue"
            clusters = {c.id: c for c in core.clusters}
            queued = queue_plan(db, plan, clusters)
            report = {"seeds": len(seeds), "clusters": len(core.clusters), "articles_queued": len(queued),
                      "sources": [s.model_dump() for s in core.sources]}
            ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=REPORT_KIND,
                                       title=f"SEO-завод: поставлено статей — {len(queued)}",
                                       content=json.dumps(report, ensure_ascii=False))
            TaskService(db).update(task_id, status=TaskStatus.COMPLETED)
            db.commit()
        except Exception as exc:  # report the stage and error type only, never raw provider or customer data
            db.rollback()
            _logger.warning("SEO factory failed at %s: %s", stage, type(exc).__name__)
            ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=REPORT_KIND,
                                       title=f"SEO-завод остановился на шаге {stage}",
                                       content=json.dumps({"failed_stage": stage, "error": type(exc).__name__}))
            TaskService(db).update(task_id, status=TaskStatus.FAILED)
            db.commit()
        return 1
