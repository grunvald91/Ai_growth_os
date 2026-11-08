"""Explicit, tenant-scoped retry of a failed SEO article from an existing plan.

This is not an automatic factory restart. The original SEO brief and task ID
are retained so the worker reuses its safety, quota and critic checks.
"""
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.activation import require_enabled
from app.seo.queue import SEO_TASK_PREFIX
from app.workflows.article_contracts import ArticleProductionInput


def retry_failed_article(db: Session, *, product_id: UUID, task_id: UUID,
                         actor: str) -> Task:
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
    if (original.product_id != product_id or original.seo_brief is None
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
    # Requeued tasks are subject to the usual per-company daily limits,
    # original article grounding, and final critic validation.
    task.status = TaskStatus.QUEUED
    task.due_at = datetime.now(timezone.utc)
    db.add(Artifact(
        product_id=product_id, task_id=task.id, kind="seo_article_manual_retry",
        title="Повтор генерации SEO-статьи",
        content=json.dumps({
            "trigger": "owner", "actor": actor[:200],
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "plan_id": str(plan_id),
        }, ensure_ascii=False),
    ))
    db.commit()
    return task
