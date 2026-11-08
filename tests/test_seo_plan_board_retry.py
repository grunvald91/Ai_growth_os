"""SEO calendar is source-backed; manual recovery never cross-writes tenants."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.seo_plan_board import seo_plan_board
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
                    assigned_agent="content_factory", status=TaskStatus.FAILED)
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
        assert retried.due_at is not None
        assert db.scalar(select(Artifact).where(Artifact.task_id == task.id,
            Artifact.kind == "seo_article_manual_retry")) is not None
        with pytest.raises(ValueError):
            retry_failed_article(db, product_id=product.id, task_id=task.id, actor="owner")
        assert len(list(db.scalars(select(Task)))) == 1
    engine.dispose()
