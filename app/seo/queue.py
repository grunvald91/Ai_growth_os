"""Turns approved plan items into article tasks for the existing content worker."""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, func, exists
from sqlalchemy.orm import Session

from app.content.service import queue_content
from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.seo.contracts import PlanItem, SeoContentPlan
from app.seo.planner import PLAN_KIND
from app.workflows.article_contracts import ArticleProductionInput, SeoBrief
from app.seo.activation import require_enabled, editorial_preferences
from app.seo.schedule import available_slots, PUBLICATIONS_PER_DAY, db_time
from app.models.domain import Product

SEO_TASK_PREFIX = "SEO-ARTICLE:"


class SeoQuotaDeferred(RuntimeError):
    """SEO task remains queued when the company daily budget is exhausted."""



def brief_for(item: PlanItem, related: list[str]) -> SeoBrief:
    return SeoBrief(plan_item_id=item.id, primary_query=item.primary_query, related_queries=related,
                    stage=item.stage, intent=item.intent, reader_problem=item.reader_problem,
                    target_segment=item.target_segment,
                    ai_questions=item.ai_questions, next_step=item.next_step)


def queue_plan_item(db: Session, product_id: UUID, plan_artifact_id: UUID, item: PlanItem,
                    related: list[str], *, due_at: datetime | None = None, commit: bool = True) -> Task:
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
    if due_at is not None and task.due_at is None:
        task.due_at = due_at
    if commit:
        db.commit()
    return task


def queue_plan(db: Session, plan_artifact: Artifact, clusters_by_id: dict, *,
               only: set[str] | None = None, per_day: int | None = None,
               now: datetime | None = None, timezone_name: str | None = None) -> list[Task]:
    require_enabled(db, plan_artifact.product_id)
    # Same Product lock is shared by all SEO plan publishers and article releases.
    # One transaction covers the entire schedule to avoid duplicate days in concurrent plans.
    if db.scalar(select(Product.id).where(
            Product.id == plan_artifact.product_id).with_for_update()) is None:
        raise LookupError("Company not found")
    prefs = editorial_preferences(db, plan_artifact.product_id)
    per_day = prefs.publications_per_day if per_day is None else per_day
    tz = ZoneInfo(timezone_name or prefs.timezone_name)
    plan = SeoContentPlan.model_validate_json(plan_artifact.content)
    selected = [item for item in plan.items if only is None or item.id in only]
    titles = {f"{SEO_TASK_PREFIX}{plan_artifact.id}:{item.id}" for item in selected}
    existing = {t.title: t for t in db.scalars(select(Task).where(
        Task.product_id == plan_artifact.product_id,
        Task.assigned_agent == "content_factory", Task.title.in_(titles)))}
    new_count = sum(f"{SEO_TASK_PREFIX}{plan_artifact.id}:{item.id}" not in existing for item in selected)
    calendar = iter(available_slots(db, plan_artifact.product_id, new_count, limit=per_day, now=now, tz=tz))
    tasks = []
    for item in selected:
        title = f"{SEO_TASK_PREFIX}{plan_artifact.id}:{item.id}"
        if title in existing:
            tasks.append(existing[title])
            continue
        related = list(dict.fromkeys(q for cid in item.cluster_ids for q in clusters_by_id[cid].queries
                                     if q != item.primary_query))
        tasks.append(queue_plan_item(db, plan_artifact.product_id, plan_artifact.id,
                                     item, related, due_at=next(calendar), commit=False))
    db.commit()
    return tasks


OWNER_TZ = ZoneInfo("Europe/Moscow")


def _recent_starts(db: Session, now: datetime):
    """Count every recorded attempt; preserve legacy AgentRun fallback."""
    cutoff = db_time(db, now - timedelta(days=2))
    markers = list(db.execute(select(Task, Artifact.created_at)
        .join(Artifact, Artifact.task_id == Task.id)
        .where(Task.assigned_agent == "content_factory",
               Task.title.like(f"{SEO_TASK_PREFIX}%"),
               Artifact.kind == "seo_article_started",
               Artifact.created_at >= cutoff)))
    has_marker = exists(select(Artifact.id).where(
        Artifact.task_id == Task.id, Artifact.kind == "seo_article_started"))
    legacy = list(db.execute(select(Task, func.min(AgentRun.started_at))
        .join(AgentRun, AgentRun.task_id == Task.id)
        .where(Task.assigned_agent == "content_factory",
               Task.title.like(f"{SEO_TASK_PREFIX}%"),
               AgentRun.started_at >= cutoff, ~has_marker)
        .group_by(Task.id)))
    return markers + legacy


def _today_start(now: datetime, timezone_name: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone_name))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _started_in_local_day(started, now, timezone_name: str) -> bool:
    if started is None:
        return False
    actual = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started.astimezone(timezone.utc)
    return _today_start(now, timezone_name) <= actual <= now


def articles_started_today(db: Session, now: datetime | None = None, product_id: UUID | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    # Legacy cross-company count keeps the historical Moscow default; callers
    # inspecting a single company use that company's own calendar.
    tz = (editorial_preferences(db, product_id).timezone_name
          if product_id is not None else OWNER_TZ.key)
    return sum(1 for task, timestamp in _recent_starts(db, now)
               if (product_id is None or task.product_id == product_id)
               and _started_in_local_day(timestamp, now, tz))


def exhausted_products(db: Session, limit: int, now: datetime | None = None) -> set[UUID]:
    """Compare each company's daily spend to its own generation limit and timezone."""
    now = now or datetime.now(timezone.utc)
    preferences = {}
    counts: dict[UUID, int] = {}
    exhausted = set()
    for task, timestamp in _recent_starts(db, now):
        p = preferences.get(task.product_id)
        if p is None:
            p = editorial_preferences(db, task.product_id, default_articles_per_day=limit)
            preferences[task.product_id] = p
        if _started_in_local_day(timestamp, now, p.timezone_name):
            counts[task.product_id] = counts.get(task.product_id, 0) + 1
            if counts[task.product_id] >= p.articles_per_day:
                exhausted.add(task.product_id)
    return exhausted


def quota_exhausted(db: Session, limit: int, now: datetime | None = None, product_id: UUID | None = None) -> bool:
    if product_id is not None:
        p = editorial_preferences(db, product_id, default_articles_per_day=limit)
        return articles_started_today(db, now, product_id) >= p.articles_per_day
    return articles_started_today(db, now) >= limit


def record_article_start(db, task):
    """Persist an event for each claimed generation attempt, even after failure."""
    from app.services.artifacts import ArtifactService
    ArtifactService(db).create(product_id=task.product_id, task_id=task.id,
        kind="seo_article_started", title="SEO article started", content="{}")
    db.commit()
