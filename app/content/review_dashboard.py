"""Company-scoped, read-only review history and SEO progress for Growth Studio.

Only persisted approvals and confirmed task/artifact/publication rows are used.
The presentation layer never mutates or guesses the outcome of an AI review.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import (
    Approval, Artifact, ArtifactStatus, Publication, Task, TaskStatus,
)
from app.seo.queue import SEO_TASK_PREFIX
from app.content.review import REVIEW_KINDS


PAGE_SIZE = 30
REVIEW_ACTIONS = ("studio:approve_material", "dispatcher:approve_material")
SEO_PUBLICATION_ACTION = "publish"


@dataclass
class Page:
    records: list
    total: int
    page: int
    page_count: int


def _page(total: int, page: int) -> tuple[int, int]:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return min(max(1, page), pages), pages


def history(db: Session, product_id: UUID, *, page: int = 1) -> Page:
    """Only actual *decided* events, not inferred from material.status.

    The second product_id predicate prevents a forged cross-company artifact
    binding from leaking its title or a link into the reviewing company's log.
    """
    where = (
        Approval.product_id == product_id,
        Artifact.product_id == product_id,
        Artifact.kind.in_(REVIEW_KINDS),
        Approval.decided_at.is_not(None),
        Approval.status.in_(("approved", "rejected")),
        (
            Approval.action_type.in_(REVIEW_ACTIONS)
            | ((Approval.action_type == SEO_PUBLICATION_ACTION)
               & (Approval.requested_by == "seo_factory"))
        ),
    )
    source = select(Approval, Artifact).join(Artifact, Artifact.id == Approval.artifact_id).where(*where)
    total = db.scalar(
        select(func.count(Approval.id)).join(Artifact, Artifact.id == Approval.artifact_id).where(*where)
    ) or 0
    current, pages = _page(total, page)
    results = list(db.execute(source.order_by(
        Approval.decided_at.desc(), Approval.created_at.desc(), Approval.id.desc()
    ).offset((current - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all())
    return Page(results, total, current, pages)


def seo_progress(db: Session, product_id: UUID, *, page: int = 1) -> Page:
    """Show the observed state of each scheduled SEO article production task.

    Tasks without generated articles are still shown. Only actual own-company
    published Publication records can produce an 'Опубликовано' label.
    """
    predicate = (
        Task.product_id == product_id,
        Task.assigned_agent == "content_factory",
        Task.title.like(SEO_TASK_PREFIX + "%"),
    )
    total = db.scalar(select(func.count(Task.id)).where(*predicate)) or 0
    current, pages = _page(total, page)
    tasks = list(db.scalars(select(Task).where(*predicate).order_by(
        Task.created_at.desc(), Task.id.desc()
    ).offset((current - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
    if not tasks:
        return Page([], total, current, pages)
    task_ids = [task.id for task in tasks]
    articles = {}
    for row in db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(task_ids),
        Artifact.kind == "article",
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())):
        articles.setdefault(row.task_id, row)
    critic_present = set(db.scalars(select(Artifact.task_id).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(task_ids),
        Artifact.kind == "seo_final_review",
    ).distinct()))
    ids = [row.id for row in articles.values()]
    published = set()
    if ids:
        published = set(db.scalars(select(Publication.artifact_id).where(
            Publication.product_id == product_id,
            Publication.artifact_id.in_(ids),
            Publication.channel == "own_site",
            Publication.status == "published",
        )))
    result = []
    for task in tasks:
        row = articles.get(task.id)
        # A published artifact without a matching Publication is not proof of
        # delivery. And an old Publication must not validate a newer article.
        published_confirmed = bool(
            row and row.id in published and row.status == ArtifactStatus.PUBLISHED
        )
        if published_confirmed:
            label = "Опубликовано"
        elif row and row.status == ArtifactStatus.AWAITING_APPROVAL:
            label = "Ждёт решения владельца"
        elif row and row.status == ArtifactStatus.APPROVED:
            label = "Согласовано, не опубликовано"
        elif row and row.status == ArtifactStatus.PUBLISHED:
            label = "Сведения о публикации расходятся"
        elif task.status == TaskStatus.FAILED:
            label = "Ошибка подготовки"
        elif task.status == TaskStatus.CANCELLED:
            label = "Отменено"
        elif task.status == TaskStatus.RUNNING:
            label = "Создаётся"
        elif task.status == TaskStatus.QUEUED:
            label = "В очереди"
        elif row is not None:
            label = "Черновик, публикация не подтверждена"
        else:
            label = "Результат ещё не найден"
        result.append({
            "task": task, "article": row, "status": label,
            "critic_recorded": task.id in critic_present,
            "published": published_confirmed,
        })
    return Page(result, total, current, pages)
