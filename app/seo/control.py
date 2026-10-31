"""Owner-facing queue controls for the existing SEO factory worker.

No new worker, article publisher or cost model. Queued tasks can be cancelled
before a worker claims them. A running model call cannot be safely interrupted
by flipping a database status, so running cancellation is deliberately absent.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.domain import Product, Task, TaskStatus
from app.seo.factory import AGENT, queue_factory
from app.seo.activation import require_enabled


class SeoControlError(ValueError):
    pass


def _product_lock(db: Session, product_id: UUID) -> None:
    product = db.scalar(select(Product.id).where(
        Product.id == product_id,
    ).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")


def create_seo_run(db: Session, *, product_id: UUID, focus: str | None = None) -> Task:
    """One active job per company, serialized on the product row in PostgreSQL."""
    _product_lock(db, product_id)
    normalized = (focus or "").strip()
    if len(normalized) > 300:
        raise SeoControlError("Тема SEO-завода слишком длинная")
    # The existing function deduplicates queued/running tasks.
    try:
        return queue_factory(db, product_id, normalized or None)
    except ValueError as exc:
        raise SeoControlError(str(exc)) from exc


def change_seo_run(db: Session, *, product_id: UUID, task_id: UUID, action: str) -> str:
    """Atomic, tenant-scoped status CAS. Never alter running/completed runs."""
    _product_lock(db, product_id)
    if action == "retry":
        try:
            require_enabled(db, product_id)
        except ValueError as exc:
            raise SeoControlError(str(exc)) from exc
    task = db.scalar(select(Task).where(
        Task.id == task_id,
        Task.product_id == product_id,
        Task.assigned_agent == AGENT,
    ).with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise LookupError("Запуск SEO-завода не найден")

    if action == "cancel":
        if task.status != TaskStatus.QUEUED:
            raise SeoControlError(
                "Можно отменить только задание в очереди. Выполняющийся вызов нельзя прервать этой кнопкой."
            )
        before, after = TaskStatus.QUEUED, TaskStatus.CANCELLED
    elif action == "retry":
        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise SeoControlError("Повторить можно только неудачный или отменённый запуск")
        other = db.scalar(select(Task.id).where(
            Task.product_id == product_id,
            Task.assigned_agent == AGENT,
            Task.id != task_id,
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
        ).limit(1))
        if other is not None:
            raise SeoControlError("У компании уже есть активный запуск SEO-завода")
        before, after = task.status, TaskStatus.QUEUED
    else:
        raise SeoControlError("Неизвестное действие SEO-завода")

    result = db.execute(update(Task).where(
        Task.id == task_id, Task.product_id == product_id,
        Task.assigned_agent == AGENT, Task.status == before,
    ).values(status=after).execution_options(synchronize_session="fetch"))
    if result.rowcount != 1:
        db.rollback()
        raise SeoControlError("Состояние изменилось. Обновите страницу и повторите.")
    db.commit()
    return after.value
