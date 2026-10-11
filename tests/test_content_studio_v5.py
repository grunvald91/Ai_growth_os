"""UI regression tests for Growth Studio v5: no mocked publishing success."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus


@pytest.fixture
def studio_v5():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="Цветочная мастерская", slug="florist-ui-v5")
        second = Product(name="Другой бизнес", slug="other-ui-v5")
        db.add_all([company, second])
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        with TestClient(app) as client:
            yield client, db, company, second
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_task_first_pages_and_mobile_navigation(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    dashboard = client.get(base)
    assert dashboard.status_code == 200
    assert "sg-workflow-banner" in dashboard.text
    assert 'href="' + base + '/create"' in dashboard.text
    assert 'href="' + base + '/review"' in dashboard.text
    assert "Ближайшее по плану" in dashboard.text
    assert "sg-legacy-details" in dashboard.text

    create = client.get(base + "/create")
    assert create.status_code == 200
    for label in ["Пост", "Короткое видео", "Stories", "Изображение", "Полный пакет"]:
        assert label in create.text
    assert 'name="topic"' in create.text
    assert 'data-studio-create' in create.text

    review = client.get(base + "/review")
    assert review.status_code == 200 and "Очередь согласования" in review.text
    channels = client.get(base + "/channels")
    assert channels.status_code == 200 and "Telegram-каналы" in channels.text

    css = client.get("/studio/assets/studio.css")
    js = client.get("/studio/assets/studio.js")
    assert "sg-workflow-banner" in css.text
    assert "prefers-reduced-motion" in css.text
    assert "data-studio-create" in js.text


def test_create_post_and_story_from_web_without_publishing(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    post = client.post(base + "/create", data={
        "format": "post", "channel": "telegram",
        "topic": "Как подобрать цветы для подарка",
        "audience": "Покупатели",
    }, follow_redirects=False)
    assert post.status_code == 303
    assert post.headers["location"].endswith("/create?created=1")
    queued = list(db.scalars(select(Task).where(Task.product_id == company.id)))
    assert len(queued) == 1
    assert queued[0].status == TaskStatus.QUEUED
    assert queued[0].assigned_agent == "content_factory"
    request = db.scalar(select(Artifact).where(Artifact.task_id == queued[0].id,
                                              Artifact.kind == "content_request"))
    assert json.loads(request.content)["mode"] == "post"

    story = client.post(base + "/create", data={
        "format": "story", "channel": "vk", "topic": "Покажи новые букеты",
    }, follow_redirects=False)
    assert story.status_code == 303
    creative = db.scalar(select(Task).where(
        Task.product_id == company.id, Task.assigned_agent == "content_creative"
    ))
    assert creative.status == TaskStatus.QUEUED
    payload = db.scalar(select(Artifact).where(
        Artifact.task_id == creative.id, Artifact.kind == "creative_request"
    ))
    assert json.loads(payload.content)["format"] == "story"
    assert list(db.scalars(select(Publication))) == []


def test_review_owner_approves_only_material_in_correct_company(studio_v5):
    client, db, company, second = studio_v5
    row = Artifact(product_id=company.id, kind="channel_text",
                   title="Первый пост", status="awaiting_approval",
                   content=json.dumps({"channel":"telegram", "title":"Первый пост",
                                       "body":"Выбирайте композицию с учётом повода"},
                                      ensure_ascii=False))
    article = Artifact(product_id=company.id, kind="article",
                       title="Статья о цветах", status="awaiting_approval",
                       content=json.dumps({"full_article":"Исходная статья"}))
    db.add_all([row, article])
    db.commit()
    base = f"/studio/products/{company.id}"
    review = client.get(base + "/review")
    assert "Первый пост" in review.text
    assert "Статья о цветах" in review.text
    assert "Утвердить" in review.text
    wrong = client.post(
        f"/studio/products/{second.id}/review/{row.id}/approve",
        follow_redirects=False,
    )
    assert wrong.status_code == 404
    assert row.status == ArtifactStatus.AWAITING_APPROVAL
    correct = client.post(base + f"/review/{row.id}/approve", follow_redirects=False)
    assert correct.status_code == 303
    db.refresh(row)
    assert row.status == ArtifactStatus.APPROVED
    assert list(db.scalars(select(Publication))) == []

    article_approved = client.post(base + f"/review/{article.id}/approve",
                                    follow_redirects=False)
    assert article_approved.status_code == 303
    db.refresh(article)
    assert article.status == ArtifactStatus.APPROVED


def test_web_revision_form_creates_new_task_without_discarding_current_text(studio_v5):
    client, db, company, _ = studio_v5
    body = json.dumps({
        "channel":"telegram", "title":"Подарки", "origin":"quick_post",
        "body":"Старая версия текста для подписчиков",
    }, ensure_ascii=False)
    post = Artifact(product_id=company.id, kind="channel_text",
                    status="awaiting_approval", title="Подарки", content=body)
    db.add(post)
    db.commit()
    base = f"/studio/products/{company.id}"
    detail = client.get(base + f"/materials/{post.id}")
    assert detail.status_code == 200
    assert "Попросить ИИ доработать" in detail.text
    revised = client.post(base + f"/review/{post.id}/revise", data={
        "feedback": "Убери шаблонные фразы и добавь хороший пример",
    }, follow_redirects=False)
    assert revised.status_code == 303
    db.refresh(post)
    assert post.content == body
    job = db.scalar(select(Task).where(Task.product_id == company.id,
                                       Task.assigned_agent == "content_editor"))
    assert job is not None and job.status == TaskStatus.QUEUED


def test_channel_schedule_form_does_not_send_before_due_and_is_company_scoped(studio_v5):
    client, db, company, second = studio_v5
    post = Artifact(product_id=company.id, kind="channel_text",
                    title="Готовый пост", status="approved",
                    content=json.dumps({"channel":"telegram",
                                        "title":"Готовый пост", "body":"Полезный текст"}))
    conn = Artifact(product_id=company.id, kind="telegram_channel_connection",
                    title="Наш канал", status="approved",
                    content=json.dumps({"chat_id":"-1001234567890","title":"Наш канал"}))
    db.add_all([post, conn])
    db.commit()
    when = (datetime.now(ZoneInfo("Europe/Moscow")) +
            timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    fields = {"post_id":str(post.id), "connection_id":str(conn.id), "when":when}
    forbidden = client.post(f"/studio/products/{second.id}/publishing/schedule",
                            data=fields, follow_redirects=False)
    assert forbidden.status_code == 422
    success = client.post(f"/studio/products/{company.id}/publishing/schedule",
                          data=fields, follow_redirects=False)
    assert success.status_code == 303
    pub = db.scalar(select(Publication).where(Publication.product_id == company.id))
    assert pub.status == "scheduled"
    page = client.get(f"/studio/products/{company.id}/channels")
    assert "Наш канал" in page.text
    assert "Запланировано" in page.text
    assert "Автоотправка выключена" in page.text
    assert client.post(f"/studio/products/{company.id}/publishing/{pub.id}/cancel",
                       follow_redirects=False).status_code == 303
    db.refresh(pub)
    assert pub.status == "cancelled"
