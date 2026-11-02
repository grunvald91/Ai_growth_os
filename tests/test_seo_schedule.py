"""Offline daily SEO scheduling and publication gates (Moscow calendar).

No model calls, network or production writes.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.models.domain import Artifact, Product, Publication, Task, TaskStatus
from app.seo.activation import set_enabled
from app.seo.contracts import PlanItem, QueryCluster, SeoContentPlan
from app.seo.queue import queue_plan
from app.seo.schedule import available_slots, day, published_today
from app.seo.publishing import publish_if_clear, publish_owner_approved


NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first = Product(name="ServGo", slug="servgo")
        second = Product(name="Other company", slug="other-seo")
        db.add_all([first, second])
        db.commit()
        set_enabled(db, first.id, enabled=True, actor="test-owner")
        set_enabled(db, second.id, enabled=True, actor="test-owner")
        yield db, first, second
    engine.dispose()


def plan(db, product, start, size):
    clusters, items = {}, []
    for i in range(start, start + size):
        cid, aid = f"c{i:03d}", f"a{i:03d}"
        query = f"как выбрать услугу номер {i}"
        clusters[cid] = QueryCluster(id=cid, head_query=query, queries=[query],
                                    stage="awareness", intent="informational", engines=["yandex"])
        items.append(PlanItem(id=aid, cluster_ids=[cid], primary_query=query,
                              working_title=f"Практическое руководство по услуге номер {i}",
                              stage="awareness", intent="informational",
                              reader_problem="Как выбрать подходящую услугу для себя",
                              ai_questions=["Что учесть при выборе услуги?"],
                              next_step="Посмотреть список услуг компании"))
    row = Artifact(product_id=product.id, kind="seo_content_plan",
                   title="SEO calendar plan", content=SeoContentPlan(items=items).model_dump_json())
    db.add(row)
    db.commit()
    return row, clusters


def test_full_month_is_queued_three_slots_per_moscow_day_and_retry_preserves_dates(env):
    db, company, other = env
    row, clusters = plan(db, company, 1, 8)
    tasks = queue_plan(db, row, clusters, now=NOW)
    assert len(tasks) == 8
    days = [day(task.due_at) for task in tasks]
    assert [days.count(d) for d in sorted(set(days))] == [3, 3, 2]
    assert min(task.due_at for task in tasks) >= NOW
    assert [task.id for task in queue_plan(db, row, clusters, now=NOW)] == [task.id for task in tasks]

    # Another plan must use remaining capacity, not overwrite earlier bookings.
    more, other_clusters = plan(db, company, 20, 4)
    second = queue_plan(db, more, other_clusters, now=NOW)
    assert [day(t.due_at) for t in second] == [
        days[-1], days[-1] + timedelta(days=1),
        days[-1] + timedelta(days=1), days[-1] + timedelta(days=1)
    ]
    # The calendar is isolated per company.
    other_plan, other_clusters = plan(db, other, 30, 3)
    assert all(day(t.due_at) == day(NOW) for t in
               queue_plan(db, other_plan, other_clusters, now=NOW))


def test_publication_is_held_until_due_and_daily_limit_includes_hidden_pages(env):
    db, company, _ = env
    article = Artifact(product_id=company.id, kind="article", title="Pending SEO article",
                       content="{}")
    job = Task(product_id=company.id, assigned_agent="content_factory",
               title="SEO-ARTICLE:hold", status=TaskStatus.COMPLETED,
               due_at=NOW + timedelta(days=1))
    db.add(job)
    db.flush()
    article.task_id = job.id
    db.add(article)
    db.commit()
    assert publish_if_clear(db, article.id, now=NOW).reason == "scheduled_for_later"

    # Three already-published articles (even if now hidden) consume all today's slots.
    for i in range(3):
        page = Artifact(product_id=company.id, kind="article", title=f"Existing {i}", content="{}")
        db.add(page)
        db.flush()
        db.add(Publication(product_id=company.id, artifact_id=page.id,
                           channel="own_site", status="unpublished" if i == 0 else "published",
                           published_at=NOW))
    db.commit()
    assert published_today(db, company.id, now=NOW) == 3
    job.due_at = NOW
    db.commit()
    assert publish_if_clear(db, article.id, now=NOW).reason == "daily_publication_limit"

    # Exactly one day later the author-approved path can retry, still gated by
    # the article's schema/critic and owner approval (none here).
    assert published_today(db, company.id, now=NOW + timedelta(days=1)) == 0
    assert publish_owner_approved(db, now=NOW) == 0


def test_existing_publications_reserve_slots_in_new_plans(env):
    db, company, _ = env
    page = Artifact(product_id=company.id, kind="article", title="Existing", content="{}")
    db.add(page)
    db.flush()
    db.add(Publication(product_id=company.id, artifact_id=page.id, channel="own_site",
                       status="published", published_at=NOW))
    db.commit()
    assert len(available_slots(db, company.id, 3, now=NOW)) == 3
    slots = available_slots(db, company.id, 3, now=NOW)
    assert [day(s) for s in slots].count(day(NOW)) == 2
    assert day(slots[-1]) == day(NOW) + timedelta(days=1)
