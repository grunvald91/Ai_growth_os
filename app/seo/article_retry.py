"""Explicit, tenant-scoped retry of a failed SEO article from an existing plan.

This is not an automatic factory restart. The original SEO brief and task ID
are retained so the worker reuses its safety, quota and critic checks.
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus
from app.seo.activation import require_enabled
from app.seo.queue import SEO_TASK_PREFIX
from app.workflows.article_contracts import ArticleProductionInput


def retry_failed_article(db: Session, *, product_id: UUID, task_id: UUID,
                         actor: str) -> Task:
    return _retry(db, product_id=product_id, task_id=task_id, actor=actor, automatic=False)


AUTO_RECOVERY = "seo_article_auto_recovery"


def _retry(db: Session, *, product_id: UUID, task_id: UUID, actor: str,
           automatic: bool) -> Task:
    # The same company lock serializes this operator action with SEO opt-out.
    if db.scalar(select(Product.id).where(Product.id == product_id).with_for_update()) is None:
        raise LookupError("Компания не найдена")
    require_enabled(db, product_id)
    task = db.scalar(select(Task).where(
        Task.id == task_id, Task.product_id == product_id,
        Task.assigned_agent == "content_factory",
        Task.title.like(f"{SEO_TASK_PREFIX}%"),
    ).with_for_update())
    if task is None:
        raise LookupError("Задание SEO-статьи не найдено")
    if task.status != TaskStatus.FAILED:
        raise ValueError("Повторить можно только статью со статусом ошибки")
    parts = task.title.split(":", 2)
    if len(parts) != 3:
        raise ValueError("Некорректная ссылка на SEO-план")
    try:
        plan_id = UUID(parts[1])
    except ValueError:
        raise ValueError("Некорректная ссылка на SEO-план") from None
    plan = db.scalar(select(Artifact.id).where(
        Artifact.id == plan_id, Artifact.product_id == product_id,
        Artifact.kind == "seo_content_plan",
    ))
    request = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task.id,
        Artifact.kind == "content_request",
    ))
    if plan is None or request is None:
        raise ValueError("Не найден исходный SEO-план или задание статьи")
    try:
        original = ArticleProductionInput.model_validate_json(request.content)
    except (ValueError, TypeError):
        raise ValueError("Исходные данные статьи не прошли проверку") from None
    if (original.product_id != product_id or original.task_id not in (None, task.id)
            or original.seo_brief is None
            or original.seo_brief.plan_item_id != parts[2]
            or original.channel != "own_site" or not original.light
            or original.produce_ready_assets):
        raise ValueError("Задание не соответствует исходному SEO-плану")
    # A finished artifact must never be generated a second time under its ID.
    article = db.scalar(select(Artifact.id).where(
        Artifact.product_id == product_id, Artifact.task_id == task.id,
        Artifact.kind == "article",
    ).limit(1))
    if article is not None:
        raise ValueError("Для этого задания уже существует статья; проверьте её статус")
    if automatic:
        from app.seo.failure_guard import paused_products
        from app.seo.failure_report import safe_error
        from app.seo.schedule import db_time
        now = datetime.now(timezone.utc)
        cutoff = db_time(db, now - timedelta(minutes=30))
        if product_id in paused_products(db, now):
            raise ValueError("Automatic recovery paused")
        if db.scalar(select(exists().where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "seo_article_manual_retry",
        ))):
            raise ValueError("Owner already retried this task")
        if db.scalar(select(exists().where(
            Artifact.product_id == product_id, Artifact.kind == AUTO_RECOVERY,
            (Artifact.task_id == task.id) | (Artifact.created_at >= cutoff),
        ))):
            raise ValueError("Automatic recovery already attempted or cooling down")
        if task.updated_at is None or (
                task.updated_at.replace(tzinfo=timezone.utc) if task.updated_at.tzinfo is None
                else task.updated_at) > now - timedelta(minutes=30):
            raise ValueError("Recent failure; wait for cooldown")
        latest = db.scalar(select(AgentRun).where(
            AgentRun.task_id == task.id, AgentRun.started_at.is_not(None),
        ).order_by(AgentRun.started_at.desc(), AgentRun.id.desc()).limit(1))
        family, stage = safe_error(latest.error if latest else None)
        # Recover only structural failures addressed by the current pipeline.
        # Provider outages/unknown errors need the separate availability policy.
        if (latest is None or latest.status not in {"failed", "completed"}
                or latest.finished_at is None or family != "invalid_article_output"
                or stage not in {"content_strategist", "seo_editor", "article_writer",
                                 "conversion_editor", "human_editor", "marketing_editor"}):
            raise ValueError("Failure is not eligible for automatic schema recovery")
        if db.scalar(select(exists().where(
            AgentRun.task_id == task.id, AgentRun.status == "running"
        ))):
            raise ValueError("An agent is still running")
    # Requeued tasks are subject to the usual per-company daily limits,
    # original article grounding, and final critic validation.
    task.status = TaskStatus.QUEUED
    # Preserve the original SEO calendar: a retry is not a new plan slot.
    # Failed articles had already reached their due date in the worker.
    # Old legacy tasks without any slot remain runnable immediately.
    if task.due_at is None:
        task.due_at = datetime.now(timezone.utc)
    db.add(Artifact(
        product_id=product_id, task_id=task.id, kind=AUTO_RECOVERY if automatic else "seo_article_manual_retry",
        title="Повтор генерации SEO-статьи",
        content=json.dumps({
            "trigger": "automatic_schema_recovery" if automatic else "owner", "actor": actor[:200],
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "plan_id": str(plan_id),
        }, ensure_ascii=False),
    ))
    db.commit()
    return task


def recover_failed_schema_once(db: Session, *, enabled: bool) -> int:
    """Requeue at most one old schema failure per worker tick, without bypassing gates."""
    if not enabled:
        return 0
    from app.seo.activation import enabled_product_ids
    from app.seo.schedule import db_time
    from sqlalchemy.orm import aliased
    products = enabled_product_ids(db)
    if not products:
        return 0
    marker = aliased(Artifact)
    cutoff = db_time(db, datetime.now(timezone.utc) - timedelta(minutes=30))
    candidates = list(db.execute(select(Task.product_id, Task.id).where(
        Task.product_id.in_(products), Task.status == TaskStatus.FAILED,
        Task.assigned_agent == "content_factory", Task.title.like(f"{SEO_TASK_PREFIX}%"),
        Task.updated_at <= cutoff,
        ~exists(select(marker.id).where(
            marker.product_id == Task.product_id, marker.kind == AUTO_RECOVERY,
            (marker.task_id == Task.id) | (marker.created_at >= cutoff))),
    ).order_by(Task.updated_at, Task.id)))
    for product_id, task_id in candidates:
        try:
            _retry(db, product_id=product_id, task_id=task_id,
                   actor="seo_worker", automatic=True)
            return 1
        except (LookupError, ValueError):
            db.rollback()
    return 0
