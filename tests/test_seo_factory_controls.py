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
from app.seo.activation import set_enabled


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
        set_enabled(db, first.id, enabled=True, actor='test-owner')
        set_enabled(db, other.id, enabled=True, actor='test-owner')
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
    assert "не более трёх статей" in before.text
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


def test_pilot_dashboard_shows_queued_and_deferred_articles(env):
    client, db, pid, other_pid = env
    run = create_seo_run(db, product_id=pid)
    db.add(Artifact(product_id=pid, task_id=run.id, kind="seo_factory_report",
                    title="Пилот SEO", content=json.dumps({
                        "articles_queued": 3, "articles_deferred": 9, "pilot_limit": 3,
                        "sources": []
                    })))
    db.commit()
    overview = seo_runs(db, pid)[0]
    assert overview["articles_queued"] == 3 and overview["articles_deferred"] == 9
    page = client.get(url(pid)).text
    assert "Статей поставлено в очередь: <b>3</b>" in page
    assert "Осталось в SEO-плане без генерации: <b>9</b>" in page
    assert seo_runs(db, other_pid) == []


def test_search_insights_do_not_invent_search_traffic_or_ai_citations(env):
    client, db, pid, other_pid = env
    article = Artifact(product_id=pid, kind="article", title="SEO-статья",
                       content="{}", status=ArtifactStatus.PUBLISHED)
    db.add(article)
    db.flush()
    db.add(Publication(product_id=pid, artifact_id=article.id, channel="own_site",
                       status="published", external_reference="/blog/seo-article",
                       published_at=datetime.now(timezone.utc)))
    db.commit()
    response = client.get(url(pid, "/insights"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    report = response.json()
    assert report["published_articles"] == 1
    assert report["hidden_articles"] == 0
    assert report["search_sources"]["google_search_console"]["clicks"] is None
    assert report["search_sources"]["yandex_webmaster"]["impressions"] is None
    assert report["search_sources"]["bing_ai_performance"]["citations"] is None
    assert "неизвестны" in client.get(url(pid)).text
    assert client.get(url(other_pid, "/insights")).json()["published_articles"] == 0


def test_owner_can_hide_and_restore_company_article_without_creating_a_second_url(env):
    client, db, pid, other_pid = env
    article = Artifact(product_id=pid, kind="article", title="Оригинальная статья",
                       content="{}", status=ArtifactStatus.PUBLISHED)
    other = Artifact(product_id=other_pid, kind="article", title="Чужая статья",
                     content="{}", status=ArtifactStatus.PUBLISHED)
    db.add_all([article, other])
    db.flush()
    published = Publication(product_id=pid, artifact_id=article.id, channel="own_site",
                            status="published", external_reference="/blog/article-one",
                            published_at=datetime.now(timezone.utc))
    other_pub = Publication(product_id=other_pid, artifact_id=other.id, channel="own_site",
                            status="published", external_reference="/blog/article-other",
                            published_at=datetime.now(timezone.utc))
    db.add_all([published, other_pub])
    db.commit()

    base = f"/studio/products/{pid}/seo/articles/{article.id}"
    assert "Снять с публикации" in client.get(url(pid)).text
    response = client.post(base + "/hide", follow_redirects=False)
    assert response.status_code == 303
    db.refresh(article)
    db.refresh(published)
    assert published.status == "unpublished"
    assert article.status == ArtifactStatus.APPROVED
    assert "Вернуть в блог" in client.get(url(pid)).text
    assert other_pub.status == "published"

    assert client.post(base + "/hide", follow_redirects=False).status_code == 303
    assert client.post(f"/studio/products/{pid}/seo/articles/{other.id}/hide").status_code == 404
    assert client.post(base + "/restore", follow_redirects=False).status_code == 303
    db.refresh(published)
    db.refresh(article)
    assert published.status == "published" and article.status == ArtifactStatus.PUBLISHED
    assert published.external_reference == "/blog/article-one"
    assert len(list(db.scalars(select(Publication).where(Publication.artifact_id == article.id)))) == 1
    events = list(db.scalars(select(Artifact).where(Artifact.kind == "seo_visibility_event")))
    assert len(events) == 2
    assert [json.loads(e.content)["action"] for e in events] == ["hide", "restore"]

    set_enabled(db, pid, enabled=False, actor="test-owner")
    assert client.post(base + "/hide", follow_redirects=False).status_code == 303
    assert client.post(base + "/restore").status_code == 409
    assert published.status == "unpublished"
    assert other_pub.status == "published"


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


def test_critic_review_is_visible_without_version_hash_gate(env):
    """The owner sees the persisted verdict and escaped findings without a hash gate."""
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
    assert 'name="confirm_publish"' not in page.text
    assert "Согласовать публикацию" not in page.text
    assert "Статья не прошла автоматическую проверку" in page.text
    assert client.get(url(other_pid)).status_code == 200
    assert "Статья с проверкой" not in client.get(url(other_pid)).text

    # The verdict is displayed regardless of a changed or missing review hash.
    review.content = json.dumps({"status": "all_rejected", "reader_hash": "different",
                                 "findings": [{"problem": "Нужна доработка"}]})
    db.commit()
    current = seo_runs(db, pid)[0]["articles"][0]
    assert current["critic_status"] == "all_rejected"
    assert current["critic_findings"] == ["Нужна доработка"]


def test_seo_discloses_unconnected_blog_without_claiming_publication(env):
    client, db, pid, other_pid = env
    assert "персональный блог компании на ServGo" in client.get(url(pid)).text
    assert "автоматически публикуются" in client.get(url(pid, "/queue")).text
    db.get(Product, pid).slug = "servgo"
    db.commit()
    assert "Место публикации: блог ServGo" in client.get(url(pid)).text
    assert "персональный блог компании на ServGo" in client.get(url(other_pid)).text
