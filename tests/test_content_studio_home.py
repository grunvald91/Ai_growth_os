"""Home page quick line: one topic field for a Telegram post and suggestions under it."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_home import MAX_SUGGESTIONS, topic_suggestions
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product, Task


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="Цветочная мастерская", slug="florist-home-line")
        db.add(company)
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        with TestClient(app) as client:
            yield client, db, company
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _research(db, company, status="evidence_available", **fields):
    db.add(Artifact(product_id=company.id, kind="viral_content_research", title="research",
                    content=json.dumps({"source_status": status, **fields}, ensure_ascii=False)))
    db.commit()


def test_suggestions_come_from_open_plan_topics_then_research(studio):
    _, db, company = studio
    _research(db, company, seo_topic_seeds=["как выбрать букет на свидание", "Как сохранить букет свежим"],
              content_angles=["ошибки при выборе цветов для мамы", "x" * 400])
    plan = {"slots": [
        {"topic": "Как сохранить букет свежим", "state": "planned"},
        {"topic": "Уже готовая тема", "state": "completed"},
        {"topic": "", "state": "planned"},
    ]}
    assert topic_suggestions(db, company.id, plan) == [
        "Как сохранить букет свежим",           # plan first, duplicate seed dropped
        "Как выбрать букет на свидание",        # capitalised
        "Ошибки при выборе цветов для мамы",    # over-long angle skipped
    ]


def test_suggestions_ignore_research_without_evidence_and_are_capped(studio):
    _, db, company = studio
    _research(db, company, status="insufficient_evidence", seo_topic_seeds=["тема без источников"])
    assert topic_suggestions(db, company.id, None) == []
    plan = {"slots": [{"topic": f"Тема номер {i}", "state": "planned"} for i in range(10)]}
    assert len(topic_suggestions(db, company.id, plan)) == MAX_SUGGESTIONS


def test_home_shows_one_topic_line_with_ideas_and_it_queues_a_telegram_post(studio):
    client, db, company = studio
    _research(db, company, seo_topic_seeds=["как выбрать букет на свидание"])
    base = f"/studio/products/{company.id}"
    page = client.get(base)
    assert page.status_code == 200
    assert 'class="sg-quick-topic" method="post" action="' + base + '/create"' in page.text
    assert 'name="format" value="post"' in page.text and 'name="channel" value="telegram"' in page.text
    assert 'data-topic-suggestion="Как выбрать букет на свидание"' in page.text
    assert "data-topic-suggestion" in client.get("/studio/assets/studio.js").text
    assert ".sg-quick-topic" in client.get("/studio/assets/studio.css").text

    queued = client.post(base + "/create", data={"format": "post", "channel": "telegram",
                                                 "topic": "Как выбрать букет на свидание"},
                         follow_redirects=False)
    assert queued.status_code == 303
    task = db.scalar(select(Task).where(Task.product_id == company.id))
    assert task.assigned_agent == "content_factory"


def test_home_without_plan_or_research_explains_where_ideas_come_from(studio):
    client, _, company = studio
    page = client.get(f"/studio/products/{company.id}")
    assert "Идеи появятся после контент-плана или исследования ниши." in page.text
    assert "data-topic-suggestion" not in page.text
