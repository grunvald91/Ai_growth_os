"""Reserve coverage is verified inventory, not a count of old tasks."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.domain import Artifact, ArtifactStatus, Task, TaskStatus
from app.seo.autoschedule import enqueue_enabled_if_due
from app.seo.factory import queue_factory
from app.seo.reserve import coverage
from app.seo.schedule import available_slots, day
from app.workflows.article_contracts import ArticleProductionInput
from test_seo_publishing import env, make_article  # noqa: F401
from test_articles import Settings
from test_seo_article import BRIEF


def queued(db, product_id, when, query, *, status=TaskStatus.QUEUED):
    task = Task(product_id=product_id, assigned_agent="content_factory",
                title="SEO-ARTICLE:reserve:" + query, status=status, due_at=when)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product_id, task_id=task.id, kind="content_request",
        title="Request", content=ArticleProductionInput(product_id=product_id,
            task_id=task.id, topic=query, light=True, channel="own_site",
            seo_brief=BRIEF.model_copy(update={"primary_query": query})).model_dump_json()))
    db.commit()
    return task


@pytest.mark.asyncio
async def test_reserve_excludes_unreviewed_failed_rejected_and_duplicate_supply(env, tmp_path):
    db, product = env
    now = datetime.now(timezone.utc)
    aid = await make_article(db, product, tmp_path)
    row = db.get(Artifact, aid)
    task = db.get(Task, row.task_id)
    task.title, task.assigned_agent, task.due_at = "SEO-ARTICLE:test:a001", "content_factory", now
    db.commit()
    aid2 = await make_article(db, product, tmp_path, critic_status=None)
    row2 = db.get(Artifact, aid2)
    task2 = db.get(Task, row2.task_id)
    task2.title, task2.assigned_agent, task2.due_at = "SEO-ARTICLE:test:a002", "content_factory", now
    row2.status = ArtifactStatus.AWAITING_APPROVAL
    db.commit()
    queued(db, product.id, now + timedelta(days=1), "уникальная тема")
    queued(db, product.id, now + timedelta(days=2), "уникальная тема")
    queued(db, product.id, now + timedelta(days=2), "сбой", status=TaskStatus.FAILED)
    stock = coverage(db, product.id, now=now)
    assert stock.ready_articles == 1 and stock.preparing_articles == 1
    assert len(stock.ready_days) == 1 and len(stock.pipeline_days) == 2
    row.status = ArtifactStatus.REJECTED
    db.commit()
    assert coverage(db, product.id, now=now).ready_articles == 0


def test_reserve_scheduler_checks_coverage_and_has_daily_cooldown(env, tmp_path):
    db, product = env
    now = datetime.now(timezone.utc)
    settings = Settings(products_root=tmp_path, seo_autorun_enabled=True, seo_reserve_enabled=True)
    job = queue_factory(db, product.id)
    job.status = TaskStatus.COMPLETED
    db.commit()
    for offset in range(1, 9):
        queued(db, product.id, now + timedelta(days=offset), "тема " + str(offset))
    later = now + timedelta(days=1, hours=1)
    assert not coverage(db, product.id, now=later).missing_pipeline_days
    assert not enqueue_enabled_if_due(db, settings, now=later)
    for task in db.scalars(select(Task).where(Task.assigned_agent == "content_factory")):
        task.status = TaskStatus.FAILED
    db.commit()
    assert not enqueue_enabled_if_due(db, settings, now=now + timedelta(hours=12))
    assert enqueue_enabled_if_due(db, settings, now=later)
    assert not enqueue_enabled_if_due(db, settings, now=later)


def test_new_slots_prefer_missing_days_without_moving_existing_tasks(env):
    db, product = env
    now = datetime.now(timezone.utc)
    tz = ZoneInfo("Europe/Moscow")
    desired = day(now, tz=tz) + timedelta(days=4)
    task = queued(db, product.id, now + timedelta(days=4), "существующая")
    due = task.due_at
    slots = available_slots(db, product.id, 1, now=now, tz=tz, limit=2,
                            spread_days=7, preferred_days=(desired,))
    assert day(slots[0], tz=tz) == desired
    assert task.due_at == due
    slots = available_slots(db, product.id, 1, now=now, tz=tz, limit=1,
                            spread_days=7, preferred_days=(desired,))
    assert day(slots[0], tz=tz) != desired
