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
    from app.seo.contracts import PlanItem, SeoContentPlan
    brief = BRIEF.model_copy(update={"primary_query": query})
    item = PlanItem(id=brief.plan_item_id, cluster_ids=["c001"], primary_query=query,
        working_title="Практическая статья: " + query, stage=brief.stage, intent=brief.intent,
        reader_problem=brief.reader_problem, ai_questions=brief.ai_questions, next_step=brief.next_step)
    plan = Artifact(product_id=product_id, kind="seo_content_plan", title="Reserve plan",
                    content=SeoContentPlan(items=[item]).model_dump_json())
    db.add(plan)
    db.flush()
    task = Task(product_id=product_id, assigned_agent="content_factory",
                title=f"SEO-ARTICLE:{plan.id}:{item.id}", status=status, due_at=when)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product_id, task_id=task.id, kind="content_request",
        title="Request", content=ArticleProductionInput(product_id=product_id,
            task_id=task.id, topic=query, light=True, channel="own_site",
            seo_brief=brief).model_dump_json()))
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
    for offset in range(1, 10):
        queued(db, product.id, now + timedelta(days=offset), "тема " + str(offset))
    later = now + timedelta(days=1, seconds=5)
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


def test_backlog_spreads_across_days_instead_of_consuming_three_today(env):
    db, product = env
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    for i in range(8):
        queued(db, product.id, now, "запись клиентов тема " + str(i))
    stock = coverage(db, product.id, now=now, daily_limit=3)
    assert len(stock.pipeline_days) == 8
    assert not stock.missing_pipeline_days
    assert stock.ready_articles == 0


def test_waiting_model_does_not_cover_a_day_before_retry(env):
    db, product = env
    now = datetime(2026, 11, 26, 20, 59, tzinfo=timezone.utc)
    task = queued(db, product.id, now, "запись клиентов")
    db.add(Artifact(product_id=product.id, task_id=task.id, kind="seo_model_wait",
        title=(now + timedelta(minutes=5)).isoformat(timespec="microseconds"), content="{}"))
    db.commit()
    stock = coverage(db, product.id, now=now)
    assert stock.pipeline_days == ("2026-11-27",)
    assert "2026-11-26" in stock.missing_pipeline_days


def test_extra_release_requires_seven_future_days_but_first_release_is_allowed(env, monkeypatch):
    from app.models.domain import Publication
    from app.seo.reserve import Coverage, permits_extra_release
    import app.seo.reserve as reserve
    db, product = env
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    row = Artifact(product_id=product.id, kind="article", title="candidate", content="{}")
    first = Artifact(product_id=product.id, kind="article", title="first", content="{}")
    db.add_all([row, first])
    db.commit()
    def check():
        return permits_extra_release(db, row, now=now, timezone_name="Europe/Moscow", daily_limit=3)
    assert check()
    db.add(Publication(product_id=product.id, artifact_id=first.id, channel="own_site",
                       status="published", published_at=now))
    db.commit()
    assert check()  # An ordinary public article is not the daily SEO release.
    task = Task(product_id=product.id, title="SEO-ARTICLE:example:a001",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    first.task_id, first.status = task.id, ArtifactStatus.PUBLISHED
    db.commit()
    def short(db, product_id, **kw):
        assert kw["exclude_article_id"] == row.id
        return Coverage(6, 0, (), (), ("2026-12-03",), ())
    monkeypatch.setattr(reserve, "coverage", short)
    assert not check()
    monkeypatch.setattr(reserve, "coverage", lambda *a, **kw: Coverage(7, 0, (), (), (), ()))
    assert check()


def test_broken_or_foreign_plan_is_not_future_supply(env):
    db, product = env
    now = datetime.now(timezone.utc)
    task = queued(db, product.id, now, "уникальная тема")
    task.title = "SEO-ARTICLE:missing:a001"
    db.commit()
    assert coverage(db, product.id, now=now).preparing_articles == 0


def test_inconsistent_receipts_do_not_cover_daily_seo_obligation(env):
    from app.models.domain import Publication
    db, product = env
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    task = Task(product_id=product.id, title="SEO-ARTICLE:example:a001",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    row = Artifact(product_id=product.id, task_id=task.id, kind="article",
                   title="example", content="{}", status=ArtifactStatus.DRAFT)
    db.add(row)
    db.flush()
    pub = Publication(product_id=product.id, artifact_id=row.id, channel="own_site",
                      status="published", published_at=now)
    db.add(pub)
    db.commit()
    assert "2026-11-26" in coverage(db, product.id, now=now).missing_ready_days
    row.status = ArtifactStatus.PUBLISHED
    db.commit()
    assert "2026-11-26" in coverage(db, product.id, now=now).ready_days
    pub.published_at = now + timedelta(hours=1)
    db.commit()
    assert "2026-11-26" in coverage(db, product.id, now=now).missing_ready_days
