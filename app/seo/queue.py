"""Turns approved plan items into article tasks for the existing content worker."""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.content.service import queue_content
from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.seo.contracts import PlanItem, SeoContentPlan
from app.seo.planner import PLAN_KIND
from app.workflows.article_contracts import ArticleProductionInput, SeoBrief
from app.seo.activation import require_enabled

SEO_TASK_PREFIX = "SEO-ARTICLE:"


def brief_for(item: PlanItem, related: list[str]) -> SeoBrief:
    return SeoBrief(plan_item_id=item.id, primary_query=item.primary_query, related_queries=related,
                    stage=item.stage, intent=item.intent, reader_problem=item.reader_problem,
                    ai_questions=item.ai_questions, next_step=item.next_step)


def queue_plan_item(db: Session, product_id: UUID, plan_artifact_id: UUID, item: PlanItem, related: list[str]) -> Task:
    """One task per plan item. The key makes repeats return the same task, never a duplicate article."""
    require_enabled(db, product_id)
    brief = brief_for(item, related)
    task = queue_content(db, product_id, item.working_title, commit=False,
                         dedupe_key=f"{SEO_TASK_PREFIX}{plan_artifact_id}:{item.id}",
                         audience=None, editorial_brief=item.reader_problem, format="package")
    # queue_content built a package request; replace it with the own-site SEO request carrying the brief.
    request = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "content_request"))
    data = json.loads(request.content)
    if data.get("seo_brief") is None:
        data.update(channel="own_site", produce_ready_assets=False, request_publication=False, light=True,
                    seo_brief=brief.model_dump(mode="json"))
        request.content = ArticleProductionInput.model_validate(data).model_dump_json()
    db.commit()
    return task


def queue_plan(db: Session, plan_artifact: Artifact, clusters_by_id: dict, *, only: set[str] | None = None) -> list[Task]:
    require_enabled(db, plan_artifact.product_id)
    plan = SeoContentPlan.model_validate_json(plan_artifact.content)
    tasks = []
    for item in plan.items:
        if only is not None and item.id not in only:
            continue
        related = list(dict.fromkeys(q for cid in item.cluster_ids for q in clusters_by_id[cid].queries
                                     if q != item.primary_query))
        tasks.append(queue_plan_item(db, plan_artifact.product_id, plan_artifact.id, item, related))
    return tasks


OWNER_TZ = ZoneInfo("Europe/Moscow")


def _started_today(db: Session, now: datetime | None):
    """SEO article tasks started today, Moscow time. Failed attempts count: they spent model calls."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(OWNER_TZ)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if db.bind.dialect.name == "sqlite":
        day_start = day_start.replace(tzinfo=None)
    started = select(Artifact.created_at).where(Artifact.task_id == Task.id,
        Artifact.kind == "seo_article_started").limit(1).scalar_subquery()
    legacy_start = select(func.min(AgentRun.started_at)).where(AgentRun.task_id == Task.id).scalar_subquery()
    return db.scalars(select(Task).where(
        Task.assigned_agent == "content_factory", Task.title.like(f"{SEO_TASK_PREFIX}%"),
        func.coalesce(started, legacy_start) >= day_start))


def articles_started_today(db: Session, now: datetime | None = None, product_id: UUID | None = None) -> int:
    return sum(1 for t in _started_today(db, now) if product_id is None or t.product_id == product_id)


def exhausted_products(db: Session, limit: int, now: datetime | None = None) -> set[UUID]:
    """Companies whose daily article limit is used up. The limit is per company, so one business never
    takes another's articles."""
    counts: dict[UUID, int] = {}
    for task in _started_today(db, now):
        counts[task.product_id] = counts.get(task.product_id, 0) + 1
    return {product_id for product_id, count in counts.items() if count >= limit}


def quota_exhausted(db: Session, limit: int, now: datetime | None = None, product_id: UUID | None = None) -> bool:
    return articles_started_today(db, now, product_id) >= limit



def record_article_start(db, task):
    from app.services.artifacts import ArtifactService
    if db.scalar(select(Artifact.id).where(Artifact.task_id == task.id,
                                         Artifact.kind == "seo_article_started")) is None:
        ArtifactService(db).create(product_id=task.product_id, task_id=task.id,
            kind="seo_article_started", title="SEO article started", content="{}")
        db.commit()
