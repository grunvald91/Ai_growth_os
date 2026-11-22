"""Cross-screen owner journeys for Studio Tasks, with zero paid/provider calls.

These use real FastAPI routes, SQL persistence and approval/retry handlers.
Only AI worker output is supplied as a deterministic fixture, not generated.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import (
    AgentRun, Approval, Artifact, ArtifactStatus, Product,
    Publication, Task, TaskStatus,
)


@pytest.fixture
def owner_journey():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="Кофейня у станции", slug="journey-coffee")
        foreign = Product(name="Другая кофейня", slug="journey-foreign")
        db.add_all([company, foreign])
        db.commit()

        def override():
            yield db

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as client:
                yield client, db, company, foreign
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    engine.dispose()


def test_post_full_owner_journey_queue_progress_result_approval_no_publish(owner_journey):
    client, db, company, foreign = owner_journey
    base = f"/studio/products/{company.id}"
    topic = "Как выбрать зерно для домашнего кофе"
    created = client.post(base + "/create", data={
        "format": "post", "channel": "telegram", "topic": topic,
        "audience": "Покупатели кофейни",
    }, follow_redirects=False)
    assert created.status_code == 303
    detail_url = created.headers["location"]
    assert detail_url.startswith(base + "/tasks/")
    task = db.scalar(select(Task).where(Task.product_id == company.id))
    assert task is not None and task.status == TaskStatus.QUEUED
    request = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == "content_request",
    ))
    assert request is not None
    assert json.loads(request.content)["topic"] == topic

    queued = client.get(detail_url)
    assert queued.status_code == 200
    assert 'data-sg-state="queued"' in queued.text
    assert topic in queued.text
    work = client.get(base + "/work", params={"tab": "active", "kind": "post", "q": topic})
    assert work.status_code == 200
    assert detail_url in work.text
    assert client.get(f"/studio/products/{foreign.id}/tasks/{task.id}").status_code == 404
    assert client.get(f"/studio/products/{foreign.id}/activity.json",
                      params={"task_id": str(task.id)}).json()["jobs"] == []

    # Deterministic worker transition; no real provider is contacted.
    task.status = TaskStatus.RUNNING
    run = AgentRun(task_id=task.id, agent_name="post_strategist", status="running")
    db.add(run)
    db.commit()
    feed_running = client.get(base + "/activity.json",
                              params={"task_id": str(task.id)}).json()
    assert feed_running["active_count"] == 1
    assert any(item["id"] == str(task.id) and item["state"] == "running"
               for item in feed_running["jobs"])
    detail_running = client.get(detail_url)
    assert 'data-sg-state="running"' in detail_running.text
    assert "Этапы создания" in detail_running.text

    task.status = TaskStatus.COMPLETED
    run.status = "completed"
    ready = Artifact(
        product_id=company.id, task_id=task.id, kind="channel_text",
        title="Выбор кофейных зёрен", status=ArtifactStatus.AWAITING_APPROVAL,
        content=json.dumps({
            "channel": "telegram", "title": "Выбор кофейных зёрен",
            "body": "Сравните аромат, степень обжарки и дату упаковки.",
        }, ensure_ascii=False),
    )
    db.add(ready)
    db.commit()
    result = client.get(detail_url)
    assert result.status_code == 200
    assert 'data-sg-state="completed"' in result.text
    assert f"{base}/materials/{ready.id}" in result.text
    assert "Результаты" in result.text
    assert client.get(f"{base}/materials/{ready.id}").status_code == 200

    review = client.get(base + "/review")
    assert review.status_code == 200
    assert "Выбор кофейных зёрен" in review.text
    attention = client.get(base + "/work", params={"tab": "approval"})
    assert detail_url in attention.text
    assert "Нужно решение" in attention.text
    assert not list(db.scalars(select(Publication)))

    assert client.post(
        f"/studio/products/{foreign.id}/review/{ready.id}/approve",
        follow_redirects=False,
    ).status_code == 404
    assert ready.status == ArtifactStatus.AWAITING_APPROVAL
    confirmed = client.post(base + f"/review/{ready.id}/approve",
                            follow_redirects=False)
    assert confirmed.status_code == 303
    db.refresh(ready)
    assert ready.status == ArtifactStatus.APPROVED
    assert len(list(db.scalars(select(Approval).where(Approval.artifact_id == ready.id)))) == 1
    repeat = client.post(base + f"/review/{ready.id}/approve", follow_redirects=False)
    assert repeat.status_code == 303
    assert len(list(db.scalars(select(Approval).where(Approval.artifact_id == ready.id)))) == 1
    assert not list(db.scalars(select(Publication)))

    after = client.get(base + "/work", params={"tab": "approval"})
    assert detail_url not in after.text
    completed = client.get(base + "/work", params={"tab": "ready"})
    assert detail_url in completed.text
    assert client.get(base + "/activity.json").json()["attention_count"] == 0


def test_story_failure_retry_keeps_checkpoints_and_needs_explicit_consent(owner_journey):
    client, db, company, foreign = owner_journey
    base = f"/studio/products/{company.id}"
    create = client.post(base + "/create", data={
        "format": "story", "channel": "instagram_reels",
        "topic": "История о бариста", "image_model": "none",
    }, follow_redirects=False)
    assert create.status_code == 303
    detail_url = create.headers["location"]
    task = db.scalar(select(Task).where(
        Task.product_id == company.id, Task.assigned_agent == "content_creative",
    ))
    assert task and task.status == TaskStatus.QUEUED
    checkpoint = Artifact(
        product_id=company.id, task_id=task.id, kind="creative_brief",
        title="Сохранённая раскадровка", status=ArtifactStatus.DRAFT,
        content=json.dumps({"format": "story", "platform": "instagram_reels",
                            "frames": []}),
    )
    db.add(checkpoint)
    task.status = TaskStatus.FAILED
    db.add(AgentRun(task_id=task.id, agent_name="creative_director", status="failed"))
    db.commit()

    failed = client.get(detail_url)
    assert failed.status_code == 200
    assert "Продолжить с сохранённых этапов" in failed.text
    assert 'name="confirm_retry" value="yes" required' in failed.text
    assert "платные API" in failed.text
    assert f"{base}/materials/{checkpoint.id}" in failed.text
    problems = client.get(base + "/work", params={"tab": "problems"})
    assert detail_url in problems.text
    assert client.post(
        f"/studio/products/{foreign.id}/tasks/{task.id}/retry",
        data={"confirm_retry": "yes"}, follow_redirects=False,
    ).status_code == 404

    # A POST without consent must not mutate either the job or the saved result.
    retry_url = detail_url + "/retry"
    assert client.post(retry_url, follow_redirects=False).status_code == 409
    assert client.post(retry_url, data={"confirm_retry": "no"},
                       follow_redirects=False).status_code == 409
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    assert db.get(Artifact, checkpoint.id) is not None
    assert not list(db.scalars(select(Publication)))

    resumed = client.post(retry_url, data={"confirm_retry": "yes"},
                          follow_redirects=False)
    assert resumed.status_code == 303
    assert resumed.headers["location"] == detail_url
    assert db.get(Task, task.id).status == TaskStatus.QUEUED
    assert db.get(Artifact, checkpoint.id).status == ArtifactStatus.DRAFT
    assert client.post(retry_url, data={"confirm_retry": "yes"},
                       follow_redirects=False).status_code == 409
    assert len(list(db.scalars(select(Task).where(
        Task.product_id == company.id, Task.assigned_agent == "content_creative",
    )))) == 1
    assert detail_url in client.get(base + "/work", params={"tab": "active"}).text
    assert detail_url not in client.get(base + "/work", params={"tab": "problems"}).text


@pytest.mark.parametrize("format_name,channel,expected_agent,expected_request", [
    ("package", "telegram", "content_factory", "content_request"),
    ("short_video", "youtube_shorts", "content_creative", "creative_request"),
    ("static_image", "vk", "content_creative", "creative_request"),
])
def test_all_remaining_create_formats_have_real_task_and_no_fake_output(
    owner_journey, format_name, channel, expected_agent, expected_request,
):
    """Complete Create format coverage alongside the post and story journeys."""
    client, db, company, foreign = owner_journey
    base = f"/studio/products/{company.id}"
    response = client.post(base + "/create", data={
        "format": format_name,
        "channel": channel,
        "topic": "Памятка о выборе кофе",
    }, follow_redirects=False)
    assert response.status_code == 303
    detail_url = response.headers["location"]
    assert detail_url.startswith(base + "/tasks/")
    task = db.scalar(select(Task).where(Task.product_id == company.id))
    assert task is not None
    assert task.assigned_agent == expected_agent
    assert task.status == TaskStatus.QUEUED
    brief = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == expected_request,
    ))
    assert brief is not None
    data = json.loads(brief.content)
    assert data["topic"] == "Памятка о выборе кофе"
    if expected_agent == "content_creative":
        assert data["mode"] == "creative"
        assert data["format"] == format_name
        assert data["platform"] == channel
    else:
        # A package is an article-production contract, not a single post.
        assert data.get("mode") != "post"
    page = client.get(detail_url)
    assert page.status_code == 200
    assert "В очереди" in page.text
    assert detail_url in client.get(base + "/work", params={"tab": "active"}).text
    assert client.get(f"/studio/products/{foreign.id}/tasks/{task.id}").status_code == 404
    assert not list(db.scalars(select(Publication)))
    assert not list(db.scalars(select(Approval)))
