"""User journeys for the simplified owner workflow — no fake success signals."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_article import render_article
from app.content.render import asset_path
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import AgentRun, Artifact, Product, Publication, Task, TaskStatus


@pytest.fixture
def owner_studio():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="Мастерская кофе", slug="coffee-work-ui")
        outsider = Product(name="Соседняя компания", slug="other-work-ui")
        db.add_all([company, outsider])
        db.commit()

        def session_override():
            yield db

        app.dependency_overrides[get_db] = session_override
        with TestClient(app) as client:
            yield client, db, company, outsider
        app.dependency_overrides.clear()
    engine.dispose()


def test_global_notifications_scoped_and_real(owner_studio):
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, title="CONTENT: Осенний кофе",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    ready = Artifact(product_id=company.id, task_id=task.id,
                     title="Чем полезен кофе", kind="article",
                     status="awaiting_approval",
                     content=json.dumps({"full_article": "# Кофе\n\nПолезный материал"}))
    db.add(ready)
    db.commit()
    url = f"/studio/products/{company.id}"
    dash = client.get(url)
    assert dash.status_code == 200
    assert 'data-sg-notifications' in dash.text
    assert 'href="' + url + '/work"' in dash.text
    assert "Сейчас создаётся" not in dash.text  # completed work is not in progress
    assert "Последние задания" in dash.text
    feed = client.get(url + "/activity.json")
    assert feed.status_code == 200
    value = feed.json()
    assert value["active_count"] == 0
    assert value["attention_count"] >= 1
    assert value["notices"][0]["url"] == f"{url}/materials/{ready.id}"
    assert value["jobs"][0]["state"] == "completed"
    assert len(value["jobs"][0]["artifacts"]) == 1
    isolated = client.get(f"/studio/products/{other.id}/activity.json")
    assert isolated.status_code == 200
    assert isolated.json()["attention_count"] == 0
    assert isolated.json()["jobs"] == []


def test_creation_stays_visible_as_queued_and_running(owner_studio):
    client, db, company, other = owner_studio
    base = f"/studio/products/{company.id}"
    result = client.post(base + "/create", data={
        "format": "post", "channel": "telegram",
        "topic": "Как выбрать аромат кофе",
    }, follow_redirects=False)
    assert result.status_code == 303
    assert "/tasks/" in result.headers["location"]
    page = client.get(result.headers["location"])
    assert page.status_code == 200
    assert "В очереди" in page.text
    assert "Этапы создания" in page.text
    assert "Результаты появятся" in page.text
    activity = client.get(base + "/work?tab=active")
    assert "В очереди" in activity.text
    assert "Пост для Telegram" in activity.text
    db_task = db.scalar(select(Task).where(Task.product_id == company.id))
    db_task.status = TaskStatus.RUNNING
    db.add(AgentRun(task_id=db_task.id, agent_name="post_strategist",
                    status="running"))
    db.commit()
    active = client.get(base + "/activity.json").json()
    assert active["active_count"] == 1
    detail = client.get(base + f"/tasks/{db_task.id}")
    assert "Изучение темы" in detail.text or "Идея и структура" in detail.text
    assert "Выполняется" in detail.text
    assert client.get(f"/studio/products/{other.id}/tasks/{db_task.id}").status_code == 404
    assert not list(db.scalars(select(Publication)))


def test_markdown_displays_article_not_markup_or_scripts():
    source = (
        "# Пример заголовка\n\n**Жирная мысль** и *акцент*.\n\n"
        "- Совет первый\n- Совет второй\n\n"
        "1. Шаг первый\n2. Шаг второй\n\n"
        "[Источник](https://example.com/coffee)\n\n"
        "<script>alert('XSS')</script>\n\n"
        "[Опасная ссылка](javascript:alert(1))"
    )
    html = render_article(source)
    assert "<h2>Пример заголовка</h2>" in html
    assert "<strong>Жирная мысль</strong>" in html
    assert "<em>акцент</em>" in html
    assert "<ul><li>Совет первый</li><li>Совет второй</li></ul>" in html
    assert "<ol><li>Шаг первый</li><li>Шаг второй</li></ol>" in html
    assert '<a href="https://example.com/coffee"' in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "href=\"javascript" not in html
    assert "###" not in html


def test_mp4_link_only_for_real_render_and_only_for_own_company(owner_studio, tmp_path, monkeypatch):
    client, db, company, other = owner_studio
    script = uuid4()
    video = Artifact(product_id=company.id, kind="video_render",
                     title="Готовое типографическое видео",
                     status="awaiting_approval",
                     content=json.dumps({"script_id": str(script), "duration_seconds": 30}))
    db.add(video)
    db.commit()
    base = f"/studio/products/{company.id}"
    detail = client.get(base + f"/materials/{video.id}")
    assert detail.status_code == 200
    assert "Готовый MP4" in detail.text
    assert "Скачать видео MP4" in detail.text
    monkeypatch.setattr("app.api.content_studio.get_settings",
                        lambda: SimpleNamespace(products_root=tmp_path))
    src = base + f"/renders/{video.id}/video.mp4"
    assert client.get(src).status_code == 404
    path = asset_path(tmp_path, company.id, script, "video.mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video-for-authorized-download-route")
    delivered = client.get(src)
    assert delivered.status_code == 200
    assert "attachment" in delivered.headers["content-disposition"]
    assert delivered.content == path.read_bytes()
    assert client.get(f"/studio/products/{other.id}/renders/{video.id}/video.mp4").status_code == 404
    assert client.get(base + f"/renders/{video.id}/../../../../etc/passwd").status_code != 200
