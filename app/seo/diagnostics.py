"""Read-only reconciliation of recorded SEO tasks, articles and publications.

A database publication receipt is not a live HTTP check of the public blog.
This module only reports observed inconsistencies and delays; it never repairs
statuses, schedules work or invokes the AI workers.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Publication, Task, TaskStatus
from app.seo.schedule import SEO_TASK_PREFIX


ISSUE_MESSAGES = {
    "publication_mismatch": "Статус статьи и запись публикации расходятся",
    "generation_failed": "Подготовка SEO-статьи завершилась с ошибкой",
    "running_long": "Подготовка статьи выполняется дольше 2 часов",
    "start_overdue": "Начало подготовки просрочено более чем на сутки",
    "missing_article": "Задание завершено, но статья не найдена",
    "draft_overdue": "Статья осталась черновиком после срока публикации",
    "approved_overdue": "Статья согласована, но публикация не подтверждена",
}
# Evaluation order makes the most serious evidence conflicts visible first.
ISSUE_PRIORITY = {
    "publication_mismatch": 0,
    "generation_failed": 1,
    "missing_article": 2,
    "running_long": 3,
    "start_overdue": 4,
    "draft_overdue": 5,
    "approved_overdue": 6,
}
GRACE = timedelta(hours=24)
LONG_RUNNING = timedelta(hours=2)
BATCH = 250


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


@dataclass(frozen=True)
class SeoIssue:
    task_id: UUID
    article_id: UUID | None
    title: str
    code: str

    @property
    def message(self) -> str:
        return ISSUE_MESSAGES[self.code]


@dataclass
class SeoAudit:
    rows: list[dict]
    issues: list[SeoIssue]
    counts: dict[str, int]


def _chunks(values: list[UUID]):
    for start in range(0, len(values), BATCH):
        yield values[start:start + BATCH]


def audit_seo(db: Session, product_id: UUID, *, now: datetime | None = None,
              tasks: list[Task] | None = None) -> SeoAudit:
    """Review all company SEO article tasks, or a caller-supplied scoped subset.

    The company constraint is repeated on every related query. The newest
    article per task is the current material; publishing an older revision
    must not validate a newer article.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    if tasks is None:
        tasks = list(db.scalars(select(Task).where(
            Task.product_id == product_id,
            Task.assigned_agent == "content_factory",
            Task.title.like(SEO_TASK_PREFIX + "%"),
        ).order_by(Task.created_at.desc(), Task.id.desc())))
    else:
        # Used only with already authorized task data; recheck anyway.
        tasks = [t for t in tasks if t.product_id == product_id
                 and t.assigned_agent == "content_factory"
                 and t.title.startswith(SEO_TASK_PREFIX)]
    if not tasks:
        return SeoAudit([], [], {})

    task_ids = [task.id for task in tasks]
    articles: dict[UUID, Artifact] = {}
    critic_present: set[UUID] = set()
    for ids in _chunks(task_ids):
        for artifact in db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_(ids),
            Artifact.kind == "article",
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc())):
            articles.setdefault(artifact.task_id, artifact)
        critic_present.update(db.scalars(select(Artifact.task_id).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_(ids),
            Artifact.kind == "seo_final_review",
        ).distinct()))

    publication_states: dict[UUID, set[str]] = {}
    article_ids = [row.id for row in articles.values()]
    for ids in _chunks(article_ids):
        for artifact_id, state in db.execute(select(
            Publication.artifact_id, Publication.status,
        ).where(
            Publication.product_id == product_id,
            Publication.artifact_id.in_(ids),
            Publication.channel == "own_site",
        )):
            publication_states.setdefault(artifact_id, set()).add(state)

    records: list[dict] = []
    issues: list[SeoIssue] = []
    for task in tasks:
        row = articles.get(task.id)
        publication_statuses = publication_states.get(row.id, set()) if row else set()
        has_published_receipt = "published" in publication_statuses
        is_marked_published = bool(row and row.status == ArtifactStatus.PUBLISHED)
        published = bool(is_marked_published and has_published_receipt)
        due = _utc(task.due_at)
        updated = _utc(task.updated_at) or _utc(task.created_at)
        issue_code = None
        if row is not None and has_published_receipt != is_marked_published:
            issue_code = "publication_mismatch"
        elif row is not None and is_marked_published and publication_statuses != {"published"}:
            # Even a previously successful publication cannot suppress an
            # explicit conflicting or withdrawn publication receipt.
            issue_code = "publication_mismatch"
        elif not published and task.status == TaskStatus.FAILED:
            issue_code = "generation_failed"
        elif task.status == TaskStatus.RUNNING and updated and updated <= now - LONG_RUNNING:
            issue_code = "running_long"
        elif (task.status == TaskStatus.QUEUED and due and updated
              and due <= now - GRACE and updated <= now - GRACE):
            issue_code = "start_overdue"
        elif (task.status == TaskStatus.COMPLETED and row is None and updated
              and updated <= now - GRACE):
            issue_code = "missing_article"
        elif (row is not None and row.status == ArtifactStatus.DRAFT
              and task.status == TaskStatus.COMPLETED
              and due and due <= now - GRACE):
            issue_code = "draft_overdue"
        elif (row is not None and row.status == ArtifactStatus.APPROVED
              and due and due <= now - GRACE):
            issue_code = "approved_overdue"

        if published:
            status = "Опубликовано"
        elif issue_code == "publication_mismatch":
            status = "Сведения о публикации расходятся"
        elif row and row.status == ArtifactStatus.AWAITING_APPROVAL:
            status = "Ждёт решения владельца"
        elif row and row.status == ArtifactStatus.APPROVED:
            status = "Согласовано, не опубликовано"
        elif row and row.status == ArtifactStatus.PUBLISHED:
            status = "Сведения о публикации расходятся"
        elif task.status == TaskStatus.FAILED:
            status = "Ошибка подготовки"
        elif task.status == TaskStatus.CANCELLED:
            status = "Отменено"
        elif task.status == TaskStatus.RUNNING:
            status = "Создаётся"
        elif task.status == TaskStatus.QUEUED:
            status = "В очереди"
        elif task.status == TaskStatus.WAITING_APPROVAL:
            status = "Ожидает разрешения на запуск"
        elif row is not None:
            status = "Черновик, публикация не подтверждена"
        else:
            status = "Результат ещё не найден"

        if issue_code:
            issues.append(SeoIssue(
                task_id=task.id, article_id=row.id if row else None,
                title=str(row.title if row else task.title)[:140],
                code=issue_code,
            ))
        records.append({
            "task": task,
            "article": row,
            "status": status,
            "critic_recorded": task.id in critic_present,
            "published": published,
            "issue_code": issue_code,
            "issue_message": ISSUE_MESSAGES.get(issue_code),
        })

    issues.sort(key=lambda issue: (ISSUE_PRIORITY[issue.code], issue.title, str(issue.task_id)))
    return SeoAudit(records, issues, dict(Counter(item.code for item in issues)))
