"""Studio queue controls and article destinations use the existing SEO worker.

All tests are local SQLite. No LLM, provider request or publication is executed.
"""
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.growth_live import router as growth_router, seo_run_card
from app.api.seo_run_view import seo_runs
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.factory import AGENT, REQUEST_KIND
from app.seo.control import create_seo_run


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first = Product(name="Studio 1", slug="studio-seo-one")
        other = Product(name="Studio 2", slug="studio-seo-two")
        db.add_all([first, other])
        db.commit()
        db.refresh(first)
        db.refresh(other)
        app = FastAPI()
        app.include_router(growth_router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_studio_auth] = lambda: None
        app.dependency_overrides[require_studio_csrf] = lambda: None
        with TestClient(app) as client:
            yield client, db, first.id, other.id
    engine.dispose()


def url(pid, suffix=""):
    return f"/studio/products/{pid}/seo" + suffix


def jobs(db, pid):
    return list(db.scalars(select(Task).where(
        Task.product_id == pid, Task.assigned_agent == AGENT,
    )))


def test_confirmation_does_not_queue_and_explicit_post_deduplicates(env):
    client, db, pid, _ = env
    before = client.get(url(pid, "/queue"))
    assert before.status_code == 200
    assert "автоматически" in before.text
    assert "Подтвердить постановку в очередь" in before.text
    assert jobs(db, pid) == []
    for _ in range(2):
        response = client.post(url(pid, "/queue"),
                               data={"focus": "запись для салонов"},
                               follow_redirects=False)
        assert response.status_code == 303
    assert len(jobs(db, pid)) == 1
    task = jobs(db, pid)[0]
    assert task.status == TaskStatus.QUEUED
    assert not list(db.scalars(select(Task).where(Task.assigned_agent == "content_factory")))
    request = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == REQUEST_KIND,
    ))
    assert json.loads(request.content) == {"focus": "запись для салонов"}


def test_queue_cancel_retry_and_running_claim_guard(env):
    client, db, pid, _ = env
    task = create_seo_run(db, product_id=pid, focus="сервис")
    base = url(pid, f"/{task.id}")
    response = client.post(base + "/cancel", follow_redirects=False)
    assert response.status_code == 303
    db.refresh(task)
    assert task.status == TaskStatus.CANCELLED
    response = client.post(base + "/retry", follow_redirects=False)
    assert response.status_code == 303
    db.refresh(task)
    assert task.status == TaskStatus.QUEUED
    assert len(jobs(db, pid)) == 1
    task.status = TaskStatus.RUNNING
    db.commit()
    assert client.post(base + "/cancel").status_code == 409
    assert client.post(base + "/retry").status_code == 409
    db.refresh(task)
    assert task.status == TaskStatus.RUNNING
    page = client.get(url(pid))
    assert "Запущенное выполнение нельзя прервать" in page.text


def test_company_scope_other_jobs_and_active_retry_conflicts(env):
    client, db, pid, other_pid = env
    task = create_seo_run(db, product_id=pid)
    db.refresh(task)
    assert client.post(url(other_pid, f"/{task.id}/cancel")).status_code == 404
    assert client.post(url(other_pid, f"/{task.id}/retry")).status_code == 404
    assert client.get(url(other_pid)).status_code == 200
    assert "Отменить из очереди" not in client.get(url(other_pid)).text
    assert client.post(url(pid, f"/{task.id}/retry")).status_code == 409
    assert client.post(url(other_pid, "/queue"), data={"focus": "чужая тема"},
                       follow_redirects=False).status_code == 303
    assert len(jobs(db, other_pid)) == 1

    task.status = TaskStatus.FAILED
    db.commit()
    other_active = create_seo_run(db, product_id=pid)
    assert other_active.id != task.id
    assert client.post(url(pid, f"/{task.id}/retry")).status_code == 409
    db.refresh(task)
    assert task.status == TaskStatus.FAILED


def test_invalid_focus_and_missing_run_are_not_persisted(env):
    client, db, pid, _ = env
    assert client.post(url(pid, "/queue"), data={"focus": "a"*301}).status_code == 422
    assert jobs(db, pid) == []
    from uuid import uuid4
    assert client.post(url(pid, f"/{uuid4()}/cancel")).status_code == 404


def test_run_articles_link_to_queued_task_saved_material_and_public_blog(env):
    client, db, pid, other_pid = env
    run = create_seo_run(db, product_id=pid)
    plan = Artifact(product_id=pid, task_id=run.id, kind="seo_content_plan",
                    title="SEO план", content="{}")
    db.add(plan)
    db.commit()
    db.refresh(plan)
    article_task = Task(product_id=pid, assigned_agent="content_factory",
                        title=f"SEO-ARTICLE:{plan.id}:a001",
                        status=TaskStatus.QUEUED)
    db.add(article_task)
    db.commit()
    db.refresh(article_task)
    db.add(Artifact(product_id=pid, task_id=article_task.id,
                    kind="content_request", title="Запрос",
                    content=json.dumps({"topic": "<img src=x onerror=alert(1)>"})))
    db.commit()

    summary = seo_runs(db, pid)
    assert len(summary) == 1
    item = summary[0]["articles"][0]
    assert item["task_id"] == str(article_task.id)
    assert item["article_id"] is None
    assert item["title"].startswith("<img")
    listing = client.get(url(pid))
    assert f"/studio/products/{pid}/tasks/{article_task.id}" in listing.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in listing.text
    assert "<img src=x onerror=alert(1)>" not in listing.text

    article = Artifact(product_id=pid, task_id=article_task.id, kind="article",
                       title="Готовая статья", content="{}",
                       status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(article)
    db.commit()
    db.refresh(article)
    summary = seo_runs(db, pid)
    assert summary[0]["articles"][0]["article_status"] == "awaiting_approval"
    assert summary[0]["articles"][0]["publication_path"] is None
    assert f"/studio/products/{pid}/materials/{article.id}" in client.get(url(pid)).text

    article.status = ArtifactStatus.PUBLISHED
    db.add(Publication(product_id=pid, artifact_id=article.id, channel="own_site",
                       status="published", external_reference="/blog/seo-test-article",
                       published_at=datetime.now(timezone.utc)))
    db.commit()
    summary = seo_runs(db, pid)
    assert summary[0]["articles"][0]["publication_path"] == "/blog/seo-test-article"
    page = client.get(url(pid))
    assert "https://servgo.ru/blog/seo-test-article" not in page.text
    assert "адрес сайта этой компании не подключён" in page.text
    # ServGo's own product is the ONLY one currently mounted at servgo.ru/blog.
    db.get(Product, pid).slug = "servgo"
    db.commit()
    own_page = client.get(url(pid))
    assert "https://servgo.ru/blog/seo-test-article" in own_page.text
    assert seo_runs(db, other_pid) == []


def test_verified_critic_review_is_visible_only_for_correct_article_version(env, monkeypatch):
    """The owner sees persisted findings, but never a critic verdict from a stale article."""
    from types import SimpleNamespace
    from app.models.domain import AgentRun, Approval

    client, db, pid, other_pid = env
    run = create_seo_run(db, product_id=pid)
    plan = Artifact(product_id=pid, task_id=run.id, kind="seo_content_plan",
                    title="План", content="{}")
    db.add(plan)
    db.flush()
    task = Task(product_id=pid, assigned_agent="content_factory",
                title=f"SEO-ARTICLE:{plan.id}:a001", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    article = Artifact(product_id=pid, task_id=task.id, kind="article",
                       title="Статья с проверкой", content="{}",
                       status=ArtifactStatus.AWAITING_APPROVAL)
    reviewer_run = AgentRun(task_id=task.id, agent_name="marketing_editor", status="completed")
    db.add_all([article, reviewer_run])
    db.flush()
    review = Artifact(product_id=pid, task_id=task.id, agent_run_id=reviewer_run.id,
                      kind="seo_final_review", title="Final review",
                      content=json.dumps({"status": "all_rejected", "reader_hash": "hash-1",
                          "findings": [{"problem": "<img src=x onerror=alert(1)>",
                                         "severity": "major"}]}))
    approval = Approval(product_id=pid, artifact_id=article.id, action_type="publish",
                        risk_level="high", status="pending", requested_by="seo_factory",
                        reason="Проверить: <script>alert(1)</script>")
    db.add_all([review, approval])
    db.commit()

    from app.api import seo_run_view
    monkeypatch.setattr(seo_run_view.ArticleArtifact, "model_validate_json",
                        classmethod(lambda cls, content: SimpleNamespace(reader_article=object())))
    monkeypatch.setattr(seo_run_view, "reader_hash", lambda reader: "hash-1")
    data = seo_runs(db, pid)[0]["articles"][0]
    assert data["critic_status"] == "all_rejected"
    assert data["critic_findings_total"] == 1
    assert "onerror" in data["critic_findings"][0]

    page = client.get(url(pid))
    assert page.status_code == 200
    assert "автором" in page.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in page.text
    assert "<img src=x onerror=alert(1)>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert 'name="confirm_publish"' in page.text
    assert f'/review/{article.id}/approve' in page.text
    assert "Согласовать публикацию" in page.text
    assert client.get(url(other_pid)).status_code == 200
    assert "Статья с проверкой" not in client.get(url(other_pid)).text

    # The recorded critic hash must match the CURRENT article; otherwise no findings are trusted.
    monkeypatch.setattr(seo_run_view, "reader_hash", lambda reader: "changed-reader")
    stale = seo_runs(db, pid)[0]["articles"][0]
    assert stale["critic_status"] == "content_mismatch"
    assert stale["critic_findings"] == []
    assert "проверка относится к другой версии" in client.get(url(pid)).text


def test_seo_discloses_unconnected_blog_without_claiming_publication(env):
    client, db, pid, other_pid = env
    assert "Внешний адрес сайта для этой компании пока не подключён" in client.get(url(pid)).text
    assert "для других компаний публичный сайт ещё не подключён" in client.get(url(pid, "/queue")).text
    db.get(Product, pid).slug = "servgo"
    db.commit()
    assert "https://servgo.ru/blog" in client.get(url(pid)).text
    assert "Внешний адрес сайта для этой компании пока не подключён" in client.get(url(other_pid)).text
