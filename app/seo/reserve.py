"""Read-only coverage of upcoming SEO publication days and their pipeline.

A queued article is capacity in preparation, not a ready reserve. Simulate
the existing publisher's daily cap and due dates; never count failed, rejected,
duplicate, invalid or unreviewed articles as ready.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.domain import Approval, Artifact, ArtifactStatus, Publication, Task, TaskStatus
from app.seo.clustering import normalize
from app.seo.publishing import (
    CLEAN_CRITIC_STATUSES, critic_verdict, duplicate_page_problems,
    public_blog_ready, servgo_buyer_intent_problems, static_problems,
    unsupported_servgo_pricing,
)
from app.seo.schedule import day, db_time, utc
from app.tools import ToolOperation
from app.workflows.article_contracts import ArticleArtifact, ArticleProductionInput

RESERVE_DAYS = 7
READY_STATUSES = (ArtifactStatus.DRAFT, ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED)


@dataclass(frozen=True)
class Coverage:
    ready_articles: int
    preparing_articles: int
    ready_days: tuple[str, ...]
    pipeline_days: tuple[str, ...]
    missing_ready_days: tuple[str, ...]
    missing_pipeline_days: tuple[str, ...]


def coverage(db, product_id, *, now=None, timezone_name="Europe/Moscow", daily_limit=3,
             exclude_article_id=None):
    now = utc(now or datetime.now(timezone.utc))
    tz = ZoneInfo(timezone_name)
    today = day(now, tz=tz)
    horizon = [today + timedelta(days=i) for i in range(RESERVE_DAYS + 1)]
    # Keep the next seven full days, plus today's obligation if not yet met.
    ready, preparing, actual, consumed = (defaultdict(int) for _ in range(4))
    if not public_blog_ready(db, product_id):
        missing = tuple(d.isoformat() for d in horizon)
        return Coverage(0, 0, (), (), missing, missing)
    published_ids = set(db.scalars(select(Publication.artifact_id).where(
        Publication.product_id == product_id, Publication.channel == "own_site")))
    vetoes = set(db.scalars(select(Approval.artifact_id).where(
        Approval.product_id == product_id, Approval.action_type == ToolOperation.PUBLISH.value,
        Approval.status == "rejected")))
    from app.seo.schedule import day_bounds
    start, _ = day_bounds(now, tz=tz)
    for timestamp, status in db.execute(select(Publication.published_at, Publication.status).where(
            Publication.product_id == product_id, Publication.channel == "own_site",
            Publication.published_at >= db_time(db, start))):
        if timestamp is not None:
            date = day(timestamp, tz=tz)
            consumed[date] += 1
            if status == 'published':
                actual[date] += 1

    tasks = list(db.scalars(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "content_factory",
        Task.title.like("SEO-ARTICLE:%"),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED]),
    ).order_by(Task.due_at.asc().nulls_first(), Task.created_at, Task.id)))
    task_map = {t.id: t for t in tasks}
    if not tasks:
        missing = tuple(d.isoformat() for d in horizon if not actual[d])
        met = tuple(d.isoformat() for d in horizon if actual[d])
        return Coverage(0, 0, met, met, missing, missing)

    articles = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(task_map),
        Artifact.kind == "article").order_by(Artifact.created_at, Artifact.id)))
    article_tasks = {a.task_id for a in articles}
    seen_queries, seen_bodies, seen_tasks = set(), set(), set()
    # Existing public pages must not be counted again as future supply.
    for row in db.scalars(select(Artifact).join(Publication, Publication.artifact_id == Artifact.id)
            .where(Artifact.product_id == product_id, Publication.product_id == product_id,
                   Publication.channel == 'own_site', Publication.status == 'published', Artifact.kind == 'article')):
        try:
            earlier = ArticleArtifact.model_validate_json(row.content)
            seen_queries.add(normalize(earlier.seo.primary_query))
        except (ValueError, TypeError):
            continue
    def scheduled(task):
        return max(today, day(task.due_at, tz=tz)) if task.due_at else today

    for row in sorted(articles, key=lambda a: (scheduled(task_map[a.task_id]), str(a.id))):
        task = task_map[row.task_id]
        if (task.status != TaskStatus.COMPLETED or row.status not in READY_STATUSES
                or row.id == exclude_article_id or row.id in vetoes
                or row.id in published_ids or task.id in seen_tasks):
            continue
        try:
            article = ArticleArtifact.model_validate_json(row.content)
            if (static_problems(article) or unsupported_servgo_pricing(db, row, article)
                    or servgo_buyer_intent_problems(db, row, article)
                    or duplicate_page_problems(db, row, article)
                    or critic_verdict(db, task.id, article.reader_article) not in CLEAN_CRITIC_STATUSES):
                continue
            query = normalize(article.seo.primary_query)
            body = " ".join(" ".join(s.body.casefold().split()) for s in article.reader_article.sections)
            if not query or query in seen_queries or body in seen_bodies:
                continue
        except (ValueError, TypeError):
            continue
        seen_tasks.add(task.id)
        seen_queries.add(query)
        seen_bodies.add(body)
        ready[scheduled(task)] += 1

    requests = db.scalars(select(Artifact).where(Artifact.product_id == product_id,
        Artifact.task_id.in_(task_map), Artifact.kind == "content_request"))
    request_map = {}
    for request in requests:
        # Multiple source requests for one task are ambiguous, not a promise of supply.
        request_map[request.task_id] = request if request.task_id not in request_map else None
    waits = {}
    for marker in db.scalars(select(Artifact).where(Artifact.product_id == product_id,
            Artifact.task_id.in_(task_map), Artifact.kind == 'seo_model_wait')):
        try:
            retry_at = utc(datetime.fromisoformat(marker.title))
            waits[marker.task_id] = max(waits.get(marker.task_id, now), retry_at)
        except (ValueError, TypeError):
            continue
    for task in tasks:
        if task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING) or task.id in article_tasks:
            continue
        request = request_map.get(task.id)
        if request is None:
            continue
        try:
            data = ArticleProductionInput.model_validate_json(request.content)
            if (data.product_id != product_id or data.task_id not in (None, task.id)
                    or not data.seo_brief or not data.light or data.channel != "own_site"):
                continue
            query = normalize(data.seo_brief.primary_query)
            if not query or query in seen_queries:
                continue
        except (ValueError, TypeError):
            continue
        seen_queries.add(query)
        preparing[max(scheduled(task), day(waits.get(task.id, now), tz=tz))] += 1

    def simulate(include_preparing):
        carry, met, missing = 0, [], []
        for date in horizon:
            carry += ready[date] + (preparing[date] if include_preparing else 0)
            # Reserve mode guarantees one per day before using surplus.
            release = min(carry, max(0, daily_limit - consumed[date]), max(0, 1 - actual[date]))
            carry -= release
            (met if actual[date] + release > 0 else missing).append(date.isoformat())
        return tuple(met), tuple(missing)

    ready_days, missing_ready = simulate(False)
    pipeline_days, missing_pipeline = simulate(True)
    return Coverage(sum(ready[d] for d in horizon), sum(preparing[d] for d in horizon), ready_days,
                    pipeline_days, missing_ready, missing_pipeline)


def permits_extra_release(db, row, *, now, timezone_name, daily_limit):
    """Called under the company publication lock. First public post is always
    eligible; an extra post must leave the next seven full days covered."""
    from app.seo.schedule import day_bounds
    tz = ZoneInfo(timezone_name)
    start, stop = day_bounds(now, tz=tz)
    already_live = db.scalar(select(Publication.id).where(
        Publication.product_id == row.product_id, Publication.channel == "own_site",
        Publication.status == "published", Publication.published_at >= db_time(db, start),
        Publication.published_at < db_time(db, stop)).limit(1))
    if already_live is None:
        return True
    stock = coverage(db, row.product_id, now=now, timezone_name=timezone_name,
                     daily_limit=daily_limit, exclude_article_id=row.id)
    today = day(now, tz=tz).isoformat()
    return not any(date != today for date in stock.missing_ready_days)
