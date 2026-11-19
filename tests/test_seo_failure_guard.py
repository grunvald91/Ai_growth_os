"""Burst-failure control for automated SEO: tenant-scoped and cost-safe."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus
from app.seo.failure_guard import (
    TECHNICAL_FAILURE_THRESHOLD,
    paused_products, automatic_generation_paused, technical_failure_class,
)
from test_seo_factory import env  # noqa: F401


def _failed_article(db, company, idx, *, code="assembly_failed:invalid_marketing_editor_grounding:ValidationError"):
    task = Task(product_id=company, title=f"SEO-ARTICLE:plan:{idx}",
                assigned_agent="content_factory", status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(AgentRun(task_id=task.id, agent_name="marketing_editor", provider="fake",
                    model="fake", status="completed", started_at=now,
                    finished_at=now, error=code))
    db.commit()
    return task


def test_five_distinct_technical_failures_pause_only_the_affected_company(env):
    factory, _, company = env
    with factory() as db:
        other = Product(name="Other", slug="other-seo")
        db.add(other)
        db.commit()
        tasks = [_failed_article(db, company, n) for n in range(TECHNICAL_FAILURE_THRESHOLD)]
        _failed_article(db, other.id, 100)
        assert paused_products(db) == {company}
        waiting = Task(product_id=company, title="SEO-ARTICLE:next",
                       assigned_agent="content_factory", status=TaskStatus.QUEUED)
        other_waiting = Task(product_id=other.id, title="SEO-ARTICLE:next",
                             assigned_agent="content_factory", status=TaskStatus.QUEUED)
        db.add_all([waiting, other_waiting])
        db.commit()
        assert automatic_generation_paused(db, product_id=company, task_id=waiting.id)
        assert not automatic_generation_paused(db, product_id=other.id, task_id=other_waiting.id)
        assert len(set(t.id for t in tasks)) == TECHNICAL_FAILURE_THRESHOLD


def test_explicit_owner_retry_bypasses_only_its_own_task(env):
    factory, _, company = env
    with factory() as db:
        for i in range(TECHNICAL_FAILURE_THRESHOLD):
            _failed_article(db, company, i)
        owner_task = Task(product_id=company, title="SEO-ARTICLE:owner-retry",
                          assigned_agent="content_factory", status=TaskStatus.QUEUED)
        new_task = Task(product_id=company, title="SEO-ARTICLE:new",
                        assigned_agent="content_factory", status=TaskStatus.QUEUED)
        db.add_all([owner_task, new_task])
        db.flush()
        db.add(Artifact(product_id=company, task_id=owner_task.id,
                        kind="seo_article_manual_retry", title="Owner retry", content="{}"))
        db.commit()
        assert not automatic_generation_paused(db, product_id=company, task_id=owner_task.id)
        assert automatic_generation_paused(db, product_id=company, task_id=new_task.id)


def test_editorial_rejection_does_not_trip_technical_breaker(env):
    factory, _, company = env
    with factory() as db:
        for i in range(TECHNICAL_FAILURE_THRESHOLD + 1):
            _failed_article(db, company, i, code="critic_rejected:unverified_claim")
        assert paused_products(db) == set()
        assert technical_failure_class("critic_rejected:unverified_claim") is None


def test_failure_guard_expires_after_half_hour(env):
    factory, _, company = env
    with factory() as db:
        for i in range(TECHNICAL_FAILURE_THRESHOLD):
            _failed_article(db, company, i)
        assert paused_products(db) == {company}
        assert paused_products(db, now=datetime.now(timezone.utc) + timedelta(minutes=31)) == set()
