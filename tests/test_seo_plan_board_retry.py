"""SEO calendar is source-backed; manual recovery never cross-writes tenants."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.seo_plan_board import seo_plan_board, visible_plan_run
from app.db.models import Base
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.activation import SeoFactoryActivation
from app.seo.article_retry import retry_failed_article
from app.workflows.article_contracts import ArticleProductionInput, SeoBrief


def test_seo_board_distinguishes_preparation_from_actual_publication_and_escapes():
    product_id = uuid4()
    run = {
        "status": "completed", "status_label": "Завершён",
        "horizon_days": 30, "articles_queued": 3,
        "timezone_name": "Europe/Moscow",
        "updated_at": "2026-11-08T08:00:00+00:00",
        "semantic_ready": True, "plan_ready": True,
        "articles": [
            {"title": "<script>alert(1)</script>", "primary_query": "seo test",
             "scheduled_at": "09.11.2026 09:00", "scheduled_iso": "2026-11-09T06:00:00+00:00",
             "task_status": "failed", "failure_code": "invalid_article_output",
             "article_status": None, "publication_path": None, "task_id": str(uuid4())},
            {"title": "Опубликованная статья", "primary_query": "запись",
             "scheduled_at": "08.11.2026 09:00", "scheduled_iso": "2026-11-08T06:00:00+00:00",
             "task_status": "completed", "article_status": "published",
             "published_at": "08.11.2026 12:32", "publication_path": "/blog/record",
             "task_id": str(uuid4()), "article_id": str(uuid4())},
            {"title": "На согласовании", "scheduled_at": "10.11.2026 09:00",
             "scheduled_iso": "2026-11-10T06:00:00+00:00", "task_status": "completed",
             "article_status": "awaiting_approval", "publication_path": None,
             "task_id": str(uuid4()), "article_id": str(uuid4())},
        ],
    }
    page = seo_plan_board(run, product_id, autorun=False, enabled=True)
    assert "План на 30 дней" in page
    assert "Плановый старт подготовки" in page or "плановый старт подготовки" in page
    assert "Фактический выход" in page or "Фактический" in page
    assert "Вышла 08.11.2026 12:32" in page
    assert "Ошибка формата ответа ИИ" in page
    assert "Ждёт проверки" in page
    assert "Автопланирование: выключено" in page
    assert "Повторить генерацию" in page
    # A publication can be confirmed even if a public link is not stored.
    page_confirmed = seo_plan_board({
        **run, "articles": [{**run["articles"][1],
                             "publication_path": None, "published_confirmed": True}]
    }, product_id, autorun=False, enabled=True)
    assert "Опубликована" in page_confirmed
    assert "Вышла 08.11.2026 12:32" in page_confirmed
    assert "&lt;script&gt;" in page and "<script>" not in page
    assert page.index("Опубликованная статья") < page.index("&lt;script&gt;")


def test_empty_board_discloses_no_plan_or_dates():
    page = seo_plan_board(None, uuid4(), autorun=False, enabled=False)
    assert "Запусков пока нет" in page
    assert "Создать план" not in page
    assert "Опубликовано" not in page


def test_retry_reuses_original_article_only_when_failed_and_enabled():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="ServGo", slug="servgo-retry-tests")
        other = Product(name="Другой", slug="other-retry-tests")
        db.add_all([product, other])
        db.flush()
        db.add(SeoFactoryActivation(
            product_id=product.id, enabled=True,
            changed_at=datetime.now(timezone.utc), changed_by="owner"))
        db.add(SeoFactoryActivation(
            product_id=other.id, enabled=True,
            changed_at=datetime.now(timezone.utc), changed_by="other-owner"))
        plan = Artifact(product_id=product.id, kind="seo_content_plan",
                        title="План", content='{"items":[]}')
        db.add(plan)
        db.flush()
        brief = SeoBrief(
            plan_item_id="a001", primary_query="онлайн запись",
            related_queries=[], stage="awareness", intent="informational",
            reader_problem="Владельцу сложно вести запись без единого календаря",
            ai_questions=["Как вести онлайн запись"], next_step="Проверить расписание")
        task = Task(product_id=product.id, title=f"SEO-ARTICLE:{plan.id}:a001",
                    assigned_agent="content_factory", status=TaskStatus.FAILED,
                    due_at=datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc))
        db.add(task)
        db.flush()
        request = ArticleProductionInput(
            product_id=product.id, task_id=task.id, topic="Как вести запись",
            channel="own_site", light=True, seo_brief=brief)
        db.add(Artifact(product_id=product.id, task_id=task.id, kind="content_request",
                        title="Request", content=request.model_dump_json()))
        db.commit()
        with pytest.raises(LookupError):
            retry_failed_article(db, product_id=other.id, task_id=task.id, actor="other")
        assert task.status == TaskStatus.FAILED
        retried = retry_failed_article(db, product_id=product.id, task_id=task.id, actor="owner")
        assert retried.id == task.id and retried.status == TaskStatus.QUEUED
        assert retried.due_at == datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
        assert db.scalar(select(Artifact).where(Artifact.task_id == task.id,
            Artifact.kind == "seo_article_manual_retry")) is not None
        with pytest.raises(ValueError):
            retry_failed_article(db, product_id=product.id, task_id=task.id, actor="owner")
        assert len(list(db.scalars(select(Task)))) == 1
    engine.dispose()


def test_visible_plan_keeps_previous_calendar_during_new_build():
    previous = {"id": "older", "status": "completed", "plan_ready": True,
                "articles": [{"title": "Старая действующая статья"}]}
    latest = {"id": "new", "status": "running", "plan_ready": False}
    chosen, older = visible_plan_run([latest, previous])
    assert chosen == previous and older is True
    assert visible_plan_run([previous]) == (previous, False)
    assert visible_plan_run([latest]) == (latest, False)
    queued_plan = {"id": "newer-plan", "status": "running", "plan_ready": True,
                   "articles": []}
    assert visible_plan_run([queued_plan, previous]) == (previous, True)
    assert visible_plan_run([]) == (None, False)


def test_generic_task_retry_must_reuse_seo_owner_gate_and_reject_cancelled_jobs():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="SEO", slug="retry-generic-seo")
        db.add(company)
        db.flush()
        db.add(SeoFactoryActivation(
            product_id=company.id, enabled=True,
            changed_at=datetime.now(timezone.utc), changed_by="owner"))
        plan = Artifact(product_id=company.id, kind="seo_content_plan",
                        title="SEO", content='{"items":[]}')
        db.add(plan)
        db.flush()
        task = Task(product_id=company.id,
                    title=f"SEO-ARTICLE:{plan.id}:a001",
                    assigned_agent="content_factory", status=TaskStatus.FAILED)
        cancelled = Task(product_id=company.id,
                         title=f"SEO-ARTICLE:{plan.id}:a002",
                         assigned_agent="content_factory", status=TaskStatus.CANCELLED)
        db.add_all([task, cancelled])
        db.flush()
        brief = SeoBrief(
            plan_item_id="a001", primary_query="онлайн запись",
            related_queries=[], stage="awareness", intent="informational",
            reader_problem="Как уменьшить ручную запись без выдуманных обещаний",
            ai_questions=["Как работает онлайн запись"], next_step="Посмотреть услуги")
        db.add(Artifact(product_id=company.id, task_id=task.id,
                        kind="content_request", title="Request",
                        content=ArticleProductionInput(
                            product_id=company.id, task_id=task.id,
                            topic="Программа онлайн записи",
                            channel="own_site", seo_brief=brief, light=True,
                        ).model_dump_json()))
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as client:
                base = f"/studio/products/{company.id}"
                bad = client.post(f"{base}/tasks/{cancelled.id}/retry",
                                  follow_redirects=False)
                assert bad.status_code == 409
                assert cancelled.status == TaskStatus.CANCELLED
                first = client.post(f"{base}/tasks/{task.id}/retry",
                                    follow_redirects=False)
                assert first.status_code == 303
                assert first.headers["location"] == f"{base}/seo"
                assert task.status == TaskStatus.QUEUED
                duplicate = client.post(f"{base}/tasks/{task.id}/retry",
                                        follow_redirects=False)
                assert duplicate.status_code == 409
                assert len(list(db.scalars(select(Artifact).where(
                    Artifact.task_id == task.id,
                    Artifact.kind == "seo_article_manual_retry")))) == 1
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
