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


def test_completed_planner_opens_its_real_draft_not_a_missing_material(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, title="EDITORIAL_PLAN",
                assigned_agent="editorial_planner", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    plan = Artifact(product_id=company.id, task_id=task.id,
                    kind="editorial_plan", title="Редакторский план",
                    status="awaiting_approval",
                    content=json.dumps({
                        "approval_state":"draft",
                        "generated_at":"2026-10-12T18:00:00+03:00",
                        "timezone":"Europe/Moscow",
                        "strategy":"Подготовить публикации для аудитории",
                        "slots": [],
                    }, ensure_ascii=False))
    db.add(plan)
    db.commit()
    base=f"/studio/products/{company.id}"
    jobs=client.get(base+"/activity.json").json()["jobs"]
    assert jobs[0]["state"] == "completed"
    assert jobs[0]["artifacts"][0]["kind"] == "editorial_plan"
    page=client.get(base+f"/tasks/{task.id}")
    assert page.status_code == 200
    assert f'{base}/plan?view=draft' in page.text
    assert "Новый контент-план" in page.text
    assert "Готово материалов: 1" in page.text


def test_short_video_brief_is_never_offered_as_downloadable_mp4(owner_studio):
    client, db, company, _ = owner_studio
    task=Task(product_id=company.id, title="CREATIVE: short_video",
              assigned_agent="content_creative", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    output=Artifact(product_id=company.id, task_id=task.id,
                    kind="creative_brief", title="Сценарий объяснения",
                    status="awaiting_approval",content=json.dumps({
                        "format":"short_video", "platform":"youtube_shorts",
                        "frames":[], "preview_text":"Сценарий и раскадровка",
                    },ensure_ascii=False))
    db.add(output)
    db.commit()
    base=f"/studio/products/{company.id}"
    detail=client.get(base+f"/tasks/{task.id}")
    assert detail.status_code == 200
    assert "MP4 создаётся отдельным заданием" in detail.text
    material=client.get(base+f"/materials/{output.id}")
    assert material.status_code == 200
    assert "Скачать видео MP4" not in material.text


def test_old_running_job_does_not_disappear_behind_newer_history(owner_studio):
    from datetime import datetime, timedelta, timezone
    from app.api.content_studio_activity import work_items
    _, db, company, _ = owner_studio
    ancient = Task(product_id=company.id, title="Long running content",
                   assigned_agent="content_factory", status=TaskStatus.RUNNING,
                   created_at=datetime.now(timezone.utc)-timedelta(days=9))
    db.add(ancient)
    for idx in range(30):
        db.add(Task(
            product_id=company.id, title=f"Completed package {idx}",
            assigned_agent="content_factory", status=TaskStatus.COMPLETED,
        ))
    db.commit()
    results=work_items(db,company.id,limit=10)
    assert results[0]["id"]==str(ancient.id)
    assert results[0]["state"]=="running"


def test_agent_progress_signature_changes_when_step_finishes(owner_studio):
    from app.api.content_studio_activity import work_items
    _, db, company, _ = owner_studio
    job=Task(product_id=company.id, title="Post",
             assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add(job)
    db.flush()
    stage=AgentRun(task_id=job.id,agent_name="post_strategist",status="running")
    db.add(stage)
    db.commit()
    before=work_items(db,company.id)[0]["stage_signature"]
    stage.status="completed"
    db.commit()
    after=work_items(db,company.id)[0]["stage_signature"]
    assert before!=after
    assert "completed" in after


def test_business_review_task_opens_its_own_page_not_an_empty_material_page(owner_studio):
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, title="Маркетинг: разбор данных бизнеса", assigned_agent="business_insights",
                status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=company.id, task_id=task.id, kind="business_review", title="Разбор",
                    content=json.dumps({"summary": "Вечер пустой", "decision_ids": [], "task_ids": []},
                                       ensure_ascii=False)))
    db.commit()
    page = client.get(f"/studio/products/{company.id}/tasks/{task.id}")
    assert page.status_code == 200
    assert "Вечер пустой" in page.text and "готовый материал пока не найден" not in page.text
    assert f'data-sg-task="{task.id}"' in page.text
    assert 'data-sg-state="completed"' in page.text
    assert client.get(f"/studio/products/{other.id}/tasks/{task.id}").status_code == 404



def test_video_review_notice_has_unrepeated_title_and_real_safe_destination(owner_studio):
    from app.api.content_studio_activity import notice_items, readable_artifact_title
    from app.models.domain import ArtifactStatus
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, title="WHITEBOARD: science",
                assigned_agent="whiteboard_renderer", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    video = Artifact(
        product_id=company.id, task_id=task.id, kind="whiteboard_video",
        title="Рисованное видео: Рисованное видео: Горячий предмет тяжелее холодного",
        status=ArtifactStatus.AWAITING_APPROVAL, content="{}",
    )
    db.add(video)
    db.commit()
    assert readable_artifact_title(video) == "Горячий предмет тяжелее холодного"
    notes = notice_items(db, company.id)
    assert len(notes) == 1
    item = notes[0]
    assert item["resource_kind"] == "video"
    assert item["resource_title"] == "Горячий предмет тяжелее холодного"
    assert item["resource_status"] == "Готово · проверить"
    assert item["text"].count("Простое видео готово:") == 1
    assert "Рисованное видео: Рисованное видео:" not in item["text"]
    assert item["url"] == f"/studio/products/{company.id}/whiteboard/{task.id}"
    assert notice_items(db, other.id) == []


def test_failed_creative_with_saved_brief_can_resume_original_task(owner_studio):
    from app.content.creative import CreativeRequest
    from test_content_creative import creative_payload
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_creative",
                title="CREATIVE: story", status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Три совета", format="story",
                              platform="instagram_reels", frame_count=3)
    db.add_all([
        Artifact(product_id=company.id, task_id=task.id, kind="creative_request",
                 title="Три совета", content=request.model_dump_json()),
        Artifact(product_id=company.id, task_id=task.id, kind="creative_brief",
                 title="Раскадровка",
                 content=json.dumps(creative_payload("story", "instagram_reels"),
                                    ensure_ascii=False)),
    ])
    db.commit()
    base = f"/studio/products/{company.id}/tasks/{task.id}"
    detail = client.get(base)
    assert detail.status_code == 200
    assert "Продолжить с сохранённых этапов" in detail.text
    assert client.post(f"/studio/products/{other.id}/tasks/{task.id}/retry").status_code == 404
    unconfirmed = client.post(base + "/retry", follow_redirects=False)
    assert unconfirmed.status_code == 409
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    resumed = client.post(
        base + "/retry", data={"confirm_retry": "yes"}, follow_redirects=False)
    assert resumed.status_code == 303
    assert resumed.headers["location"] == base
    assert db.get(Task, task.id).status == TaskStatus.QUEUED
    assert client.post(base + "/retry", data={"confirm_retry": "yes"}).status_code == 409


def test_company_intake_task_routes_to_profile_not_empty_material_view(owner_studio):
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, title="Сбор данных компании",
                assigned_agent="company_intake", status=TaskStatus.WAITING_APPROVAL)
    db.add(task)
    db.commit()
    base = f"/studio/products/{company.id}"
    redirect = client.get(f"{base}/tasks/{task.id}", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == base + "/profile"
    assert client.get(f"/studio/products/{other.id}/tasks/{task.id}").status_code == 404
    board = client.get(base + "/v4/data").json()
    assert all(item["id"] != str(task.id) for item in board["tasks"])


def test_seo_proposal_is_not_misrepresented_as_ready_article_or_video(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id,
                title="Request approval to publish own-site SEO article c8aebdc2",
                assigned_agent="distribution", status=TaskStatus.WAITING_APPROVAL,
                description="Human-approval request only: do not execute.")
    db.add(task)
    db.commit()
    base = f"/studio/products/{company.id}"
    page = client.get(f"{base}/tasks/{task.id}?design=v4")
    assert page.status_code == 200
    assert "Предложение по SEO" in page.text
    assert "Предложение публикации SEO-статьи" in page.text
    assert "Предложение на согласовании" in page.text
    assert "Готовая статья к этому предложению не прикреплена" in page.text
    assert f'href="{base}/seo"' in page.text
    assert "Request approval to publish" not in page.text
    assert "Платные вызовы требуют отдельного согласия" not in page.text
    assert "Подтвердите создание видео" not in page.text
    assert "Статья подготовлена" not in page.text
    board = client.get(base + "/v4/data").json()
    record = next(row for row in board["tasks"] if row["id"] == str(task.id))
    assert record["title"] == "Предложение публикации SEO-статьи"
    assert "не запускает публикацию" in record["detail"]


def test_generic_approval_does_not_claim_video_expense(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, title="Новая задача",
                assigned_agent="content_factory", status=TaskStatus.WAITING_APPROVAL)
    db.add(task)
    db.commit()
    page = client.get(f"/studio/products/{company.id}/tasks/{task.id}")
    assert page.status_code == 200
    assert "Задание ожидает решения" in page.text
    assert "Платные вызовы требуют отдельного согласия" not in page.text
    assert "Ожидается решение о запуске" in page.text


def test_work_steps_use_theme_surface_and_ink_instead_of_white_on_dark():
    from app.api.content_studio_work_ui import CSS
    assert ".sg-work-steps li{" in CSS
    assert "background:var(--s-surface-2,#F7F9FC)" in CSS
    assert "color:var(--s-text,#344D64)" in CSS


def test_worklist_completed_artifact_requires_attention(owner_studio):
    """Approval belongs to the artifact, even after Task completes."""
    from app.api.content_studio_activity import work_items
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, title="CONTENT: Карточка на проверке",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=company.id, task_id=task.id,
                    title="Подготовленный пост", kind="channel_text",
                    status="awaiting_approval", content="{}"))
    db.commit()
    data = work_items(db, company.id)
    assert len(data) == 1
    assert data[0]["state"] == "completed"
    assert data[0]["needs_decision"] is True
    assert data[0]["label"] == "Нужно решение"
    base = f"/studio/products/{company.id}"
    attention = client.get(base + "/work?tab=approval")
    assert attention.status_code == 200
    assert f'/tasks/{task.id}' in attention.text
    assert "Материал подготовлен — требуется решение" in attention.text
    assert 'data-sg-resource-details' not in attention.text


def test_worklist_draft_without_approval_is_not_an_extra_manual_gate(owner_studio):
    """Do not force a manual decision for every completed draft."""
    from app.api.content_studio_activity import work_items
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, title="CONTENT: Готовый черновик",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=company.id, task_id=task.id,
                    title="Черновик", kind="article", status="draft",
                    content="{}"))
    db.commit()
    assert work_items(db, company.id)[0]["needs_decision"] is False
    html = client.get(f"/studio/products/{company.id}/work?tab=approval").text
    assert f'/tasks/{task.id}' not in html


def test_tasks_all_view_puts_attention_before_newer_active_job(owner_studio):
    """Priority must not rewrite the stored task timestamps or status."""
    client, db, company, _ = owner_studio
    waiting = Task(product_id=company.id, title="CONTENT: Проверить текст",
                   assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(waiting)
    db.flush()
    db.add(Artifact(product_id=company.id, task_id=waiting.id,
                    title="Проверить", kind="channel_text",
                    status="awaiting_approval", content="{}"))
    active = Task(product_id=company.id, title="CONTENT: Новый материал",
                  assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add(active)
    db.flush()
    db.add(AgentRun(task_id=active.id, agent_name="post_strategist",
                    status="running"))
    db.commit()
    html = client.get(f"/studio/products/{company.id}/work?tab=all").text
    assert html.index(f'/tasks/{waiting.id}') < html.index(f'/tasks/{active.id}')
    assert "Идея и структура поста" in html
    assert 'aria-label="Фильтр заданий"' in html


def test_worklist_compact_css_has_one_row_filters_and_finger_targets():
    from app.api.content_studio_work_ui import CSS
    assert ".sg-work-board .sg-work-tabs{display:flex;flex-wrap:nowrap" in CSS
    assert "overflow-x:auto" in CSS
    assert "min-height:44px" in CSS
    assert ".sg-work-board .sg-work-item:focus-visible" in CSS
    from app.api.content_studio_work_ui import JS
    assert "Boolean(j.needs_decision)" in JS
    assert "a.status" in JS


def test_failed_task_is_in_attention_but_cancelled_task_is_not(owner_studio):
    client, db, company, _ = owner_studio
    failed = Task(product_id=company.id, assigned_agent="content_factory",
                  title="CONTENT: Ошибка", status=TaskStatus.FAILED)
    cancelled = Task(product_id=company.id, assigned_agent="content_factory",
                     title="CONTENT: Отменено", status=TaskStatus.CANCELLED)
    db.add_all([failed, cancelled])
    db.commit()
    base = f"/studio/products/{company.id}/work"
    attention = client.get(base + "?tab=approval").text
    assert f'/tasks/{failed.id}' in attention
    assert f'/tasks/{cancelled.id}' not in attention
    problem = client.get(base + "?tab=problems").text
    assert f'/tasks/{failed.id}' in problem
    assert f'/tasks/{cancelled.id}' in problem


def test_completed_task_result_precedes_collapsible_process_log(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: Проверенная тема", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=company.id, task_id=task.id,
                    title="Пост", kind="channel_text",
                    status="awaiting_approval", content="{}"))
    db.add(AgentRun(task_id=task.id, agent_name="post_strategist",
                    status="completed"))
    db.commit()
    page = client.get(f"/studio/products/{company.id}/tasks/{task.id}")
    assert page.status_code == 200
    assert page.text.index('<div class="sg-work-results">') < page.text.index('<details class="sg-work-steps')
    assert "<summary>Этапы создания</summary>" in page.text


def test_failed_task_keeps_steps_visible_and_retry_available(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: Сбой", status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    page = client.get(f"/studio/products/{company.id}/tasks/{task.id}")
    assert page.status_code == 200
    assert "Повторить задание" in page.text
    assert "Повторная обработка может использовать платные API" in page.text
    assert '<details class="sg-work-retry-confirm">' in page.text
    assert 'name="confirm_retry" value="yes" required' in page.text
    assert '<div class="sg-work-steps">' in page.text
    assert '<details class="sg-work-steps' not in page.text


def test_work_page_pagination_keeps_old_active_jobs_visible(owner_studio):
    from datetime import datetime, timedelta, timezone
    from app.api.content_studio_activity import work_page
    client, db, company, outsider = owner_studio
    old = Task(product_id=company.id, assigned_agent="content_factory",
               title="CONTENT: Долгое задание", status=TaskStatus.RUNNING,
               created_at=datetime.now(timezone.utc) - timedelta(days=90))
    db.add(old)
    db.add(Task(product_id=outsider.id, assigned_agent="content_factory",
                title="CONTENT: Чужое задание", status=TaskStatus.RUNNING))
    for i in range(64):
        db.add(Task(product_id=company.id, assigned_agent="content_factory",
                    title=f"CONTENT: Результат {i:02}", status=TaskStatus.COMPLETED))
    db.commit()
    first = work_page(db, company.id, page=1)
    last = work_page(db, company.id, page=4)
    assert first["total"] == 65
    assert first["counts"]["active"] == 1
    assert first["items"][0]["id"] == str(old.id)
    assert len(first["items"]) == 20
    assert len(last["items"]) == 5
    url = f"/studio/products/{company.id}/work"
    html = client.get(url).text
    assert f'/tasks/{old.id}' in html
    assert "Показано 1–20 из 65" in html
    assert 'rel="next"' in html
    page_four = client.get(url + "?page=4")
    assert "Показано 61–65 из 65" in page_four.text
    assert "Страница 4 из 4" in page_four.text
    assert str(outsider.id) not in html


def test_work_search_matches_request_topic_and_is_tenant_scoped(owner_studio):
    from app.api.content_studio_activity import work_page
    client, db, company, outsider = owner_studio
    own = Task(product_id=company.id, assigned_agent="content_factory",
               title="CONTENT: generic", status=TaskStatus.COMPLETED)
    other = Task(product_id=outsider.id, assigned_agent="content_factory",
                 title="CONTENT: generic", status=TaskStatus.COMPLETED)
    db.add_all([own, other])
    db.flush()
    for task in (own, other):
        db.add(Artifact(product_id=task.product_id, task_id=task.id,
                        title="Заявка", kind="content_request",
                        status="draft",
                        content=json.dumps({"topic": "Неочевидный спрос на запись"},
                                           ensure_ascii=False)))
    db.commit()
    found = work_page(db, company.id, query="спрос")
    assert found["total"] == 1
    assert found["items"][0]["id"] == str(own.id)
    url = f"/studio/products/{company.id}/work"
    page = client.get(url, params={"q": "спрос"}).text
    assert f'/tasks/{own.id}' in page
    assert f'/tasks/{other.id}' not in page
    assert 'value="спрос"' in page


def test_work_date_filter_and_literal_wildcards(owner_studio):
    from datetime import datetime, timedelta, timezone
    from app.api.content_studio_activity import work_page
    _, db, company, _ = owner_studio
    old = Task(product_id=company.id, assigned_agent="content_factory",
               title="CONTENT: Давний материал", status=TaskStatus.COMPLETED,
               created_at=datetime.now(timezone.utc) - timedelta(days=120))
    now = Task(product_id=company.id, assigned_agent="content_factory",
               title="CONTENT: скидка 100% на услугу",
               status=TaskStatus.COMPLETED)
    plain = Task(product_id=company.id, assigned_agent="content_factory",
                 title="CONTENT: скидка 1000 на услугу",
                 status=TaskStatus.COMPLETED)
    db.add_all([old, now, plain])
    db.commit()
    assert work_page(db, company.id, period="30")["total"] == 2
    literal = work_page(db, company.id, query="100%")
    assert literal["total"] == 1
    assert literal["items"][0]["id"] == str(now.id)


def test_work_partial_fragment_is_private_and_does_not_recreate_shell(owner_studio):
    client, db, company, outsider = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: Тест обновления", status=TaskStatus.RUNNING)
    db.add(task)
    db.commit()
    url = f"/studio/products/{company.id}/work"
    response = client.get(url, params={"tab": "active", "partial": "1"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"].startswith("application/vnd.servgo.tasks+html")
    assert 'data-sg-list-signature=' in response.text
    assert 'data-sg-refresh-url=' in response.text
    assert '<html' not in response.text.lower()
    assert f'/tasks/{task.id}' in response.text
    assert f'/tasks/{task.id}' not in client.get(
        f"/studio/products/{outsider.id}/work", params={"partial": 1}).text


def test_work_fragment_signature_changes_on_artifact_approval(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: На утверждение", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    art = Artifact(product_id=company.id, task_id=task.id,
                   title="Материал", kind="channel_text",
                   status="awaiting_approval", content="{}")
    db.add(art)
    db.commit()
    url = f"/studio/products/{company.id}/work"
    page = client.get(url, params={"tab": "approval", "partial": 1})
    import re
    signature = re.search(r'data-sg-list-signature="([^"]+)"', page.text).group(1)
    assert f'/tasks/{task.id}' in page.text
    art.status = "approved"
    db.commit()
    updated = client.get(url, params={"tab": "approval", "partial": 1})
    refreshed_signature = re.search(
        r'data-sg-list-signature="([^"]+)"', updated.text).group(1)
    assert signature != refreshed_signature
    assert f'/tasks/{task.id}' not in updated.text
    assert "Нет заданий, требующих решения" in updated.text


def test_work_poll_does_not_reload_the_list_page():
    from app.api.content_studio_work_ui import JS
    assert "async function refreshBoard()" in JS
    assert "applyBoard(fresh)" in JS
    assert "pendingBoard=fresh" in JS
    assert "if(showWork&&!workBoard)" in JS


def test_task_kind_filter_preserves_query_and_count(owner_studio):
    from app.api.content_studio_activity import work_page
    client, db, company, _ = owner_studio
    post = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: Пост", status=TaskStatus.COMPLETED)
    article = Task(product_id=company.id, assigned_agent="content_factory",
                   title="CONTENT: Статья", status=TaskStatus.COMPLETED)
    video = Task(product_id=company.id, assigned_agent="whiteboard_renderer",
                 title="WHITEBOARD-JOB: video", status=TaskStatus.RUNNING)
    db.add_all([post, article, video])
    db.flush()
    for job, mode in ((post, "post"), (article, "article")):
        db.add(Artifact(product_id=company.id, task_id=job.id,
                        kind="content_request", title="Запрос", status="draft",
                        content=json.dumps({"mode": mode, "topic": "Рост продаж"},
                                           ensure_ascii=False)))
    db.commit()
    post_data = work_page(db, company.id, kind="post")
    assert post_data["total"] == 1
    assert post_data["items"][0]["id"] == str(post.id)
    article_data = work_page(db, company.id, kind="article")
    assert article_data["total"] == 1
    assert article_data["items"][0]["id"] == str(article.id)
    video_data = work_page(db, company.id, kind="video")
    assert video_data["total"] == 1
    assert video_data["items"][0]["id"] == str(video.id)
    page = client.get(f"/studio/products/{company.id}/work",
                      params={"kind": "post", "q": "Рост"}).text
    assert f'/tasks/{post.id}' in page
    assert f'/tasks/{article.id}' not in page
    assert 'name="kind"' in page
    assert 'value="post" selected' in page
    assert "kind=post" in page


def test_work_query_html_is_escaped_and_time_has_timezone(owner_studio):
    from app.api.content_studio_activity import work_page
    from app.api.content_studio_work_ui import JS
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: без HTML", status=TaskStatus.COMPLETED)
    db.add(task)
    db.commit()
    data = work_page(db, company.id)
    assert data["items"][0]["created_at"].endswith("+00:00")
    unsafe = '<svg onload="alert(1)">'
    html = client.get(f"/studio/products/{company.id}/work",
                      params={"q": unsafe}).text
    assert unsafe not in html
    assert "&lt;svg" in html
    assert 'name="q"' in html
    assert "localizeWorkTimes(workBoard)" in JS


def test_work_requested_page_beyond_end_is_clamped(owner_studio):
    client, db, company, _ = owner_studio
    db.add(Task(product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: одна запись", status=TaskStatus.COMPLETED))
    db.commit()
    page = client.get(f"/studio/products/{company.id}/work", params={"page": "99999"})
    assert page.status_code == 200
    assert "Страница 1 из 1" in page.text
    assert "Показано 1–1 из 1" in page.text


def test_seo_factory_is_visible_without_pretending_articles_are_published(owner_studio):
    """Planning finishes independently from writing / SEO aut publishing."""
    from app.api.content_studio_activity import work_page, work_items
    client, db, company, other = owner_studio
    factory = Task(product_id=company.id, assigned_agent="seo_factory",
                   title="SEO-завод: запросы, план и статьи", status=TaskStatus.COMPLETED)
    unrelated = Task(product_id=other.id, assigned_agent="seo_factory",
                     title="SEO-завод: другая компания", status=TaskStatus.COMPLETED)
    db.add_all([factory, unrelated])
    db.flush()
    db.add(Artifact(
        product_id=company.id, task_id=factory.id, kind="seo_factory_report",
        title="Отчёт планирования", status="draft",
        content=json.dumps({
            "articles_queued": 3, "articles_deferred": 2,
            "sources": [], "planning_horizon_days": 7
        }),
    ))
    db.commit()
    rows = work_page(db, company.id, kind="seo")
    assert rows["total"] == 1
    assert rows["items"][0]["id"] == str(factory.id)
    assert rows["items"][0]["kind_label"] == "SEO-завод"
    assert rows["items"][0]["needs_decision"] is False
    assert rows["items"][0]["label"] == "Планирование завершено"
    assert "Статей поставлено в очередь: 3" in rows["items"][0]["display_summary"]
    assert "опубликовано" not in rows["items"][0]["display_summary"]
    assert work_items(db, company.id)[0]["agent"] == "seo_factory"
    base = f"/studio/products/{company.id}"
    listed = client.get(base + "/work?kind=seo")
    assert f'/tasks/{factory.id}' in listed.text
    assert str(unrelated.id) not in listed.text
    page = client.get(base + f"/tasks/{factory.id}")
    assert page.status_code == 200
    assert "Статей поставлено в очередь: 3" in page.text
    assert "Отложено тем: 2" in page.text
    assert "не означает, что статьи написаны" in page.text
    assert f'href="{base}/seo"' in page.text
    assert client.get(f"/studio/products/{other.id}/tasks/{factory.id}").status_code == 404


def test_seo_running_stage_is_actual_and_no_fake_approval(owner_studio):
    from app.api.content_studio_activity import work_page
    client, db, company, _ = owner_studio
    job = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: план", status=TaskStatus.RUNNING)
    db.add(job)
    db.flush()
    db.add(Artifact(
        product_id=company.id, task_id=job.id, kind="seo_factory_stage",
        title="Этап работы", status="draft",
        content=json.dumps({
            "stage": "build_plan", "recorded_at": "2026-11-22T04:00:00+00:00"
        }),
    ))
    db.commit()
    row = work_page(db, company.id, kind="seo")["items"][0]
    assert row["current_stage"] == "Составление контент-плана"
    assert row["state"] == "running" and row["needs_decision"] is False
    assert "seo:" in row["stage_signature"]
    html = client.get(f"/studio/products/{company.id}/work?kind=seo").text
    assert "Составление контент-плана" in html
    assert "Нужно решение" not in html


def test_business_analysis_is_in_work_list_with_existing_real_detail(owner_studio):
    from app.api.content_studio_activity import work_page
    client, db, company, other = owner_studio
    task = Task(product_id=company.id, assigned_agent="business_insights",
                title="Маркетинг: разбор данных бизнеса", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    db.add(Artifact(
        product_id=company.id, task_id=task.id,
        kind="business_review", title="Разбор",
        content=json.dumps({"summary": "Слабые вечерние часы",
                            "decision_ids": [], "task_ids": []},
                           ensure_ascii=False),
    ))
    db.add(Task(product_id=other.id, assigned_agent="business_insights",
                title="Другая компания", status=TaskStatus.RUNNING))
    db.commit()
    data = work_page(db, company.id, kind="business")
    assert data["total"] == 1
    row = data["items"][0]
    assert row["kind_label"] == "Бизнес-анализ"
    assert row["needs_decision"] is False
    assert row["label"] == "Разбор готов"
    assert "выводы" in row["display_summary"]
    base = f"/studio/products/{company.id}"
    html = client.get(base + "/work?kind=business").text
    assert f'/tasks/{task.id}' in html
    assert "Бизнес-анализ" in html
    detail = client.get(base + f"/tasks/{task.id}")
    assert detail.status_code == 200
    assert "Слабые вечерние часы" in detail.text


def test_failed_seo_factory_is_attention_not_manual_publish_approval(owner_studio):
    from app.api.content_studio_activity import work_page, notice_items
    client, db, company, _ = owner_studio
    job = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: запросы", status=TaskStatus.FAILED)
    db.add(job)
    db.commit()
    data = work_page(db, company.id, tab="approval", kind="seo")
    assert data["total"] == 1
    assert data["items"][0]["state"] == "failed"
    assert data["items"][0]["needs_decision"] is False
    alerts = notice_items(db, company.id)
    assert any("SEO-завод остановился" in x["text"] for x in alerts)
    page = client.get(f"/studio/products/{company.id}/tasks/{job.id}").text
    assert "Ошибка планирования" in page
    assert "Планирование остановилось" in page
    assert "Подтвердить публикацию" not in page
    assert "Повторить" not in page  # no unreviewed costly retry button


def test_new_task_types_keep_other_content_filters_intact(owner_studio):
    from app.api.content_studio_activity import work_page
    _, db, company, _ = owner_studio
    seo_factory = Task(product_id=company.id, assigned_agent="seo_factory",
                       title="SEO-завод", status=TaskStatus.COMPLETED)
    article = Task(product_id=company.id, assigned_agent="content_factory",
                   title="SEO-ARTICLE:historical-plan:abc", status=TaskStatus.COMPLETED)
    business = Task(product_id=company.id, assigned_agent="business_insights",
                    title="Бизнес-анализ", status=TaskStatus.COMPLETED)
    db.add_all([seo_factory, article, business])
    db.commit()
    assert work_page(db, company.id, kind="seo")["total"] == 1
    assert work_page(db, company.id, kind="business")["total"] == 1
    # SEO-article creation belongs to content factory; it must not be
    # falsely counted as the SEO planning task itself.
    assert work_page(db, company.id, kind="article")["total"] == 1
    article_item = work_page(db, company.id, kind="article")["items"][0]
    assert article_item["kind_label"] == "Статья"


def test_seo_factory_failure_reason_is_human_readable_and_not_raw_provider_text(owner_studio):
    client, db, company, _ = owner_studio
    job = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: запросы", status=TaskStatus.FAILED)
    db.add(job)
    db.flush()
    db.add(Artifact(
        product_id=company.id, task_id=job.id,
        kind="seo_factory_report", title="SEO-завод остановился",
        status="draft",
        content=json.dumps({
            "error": "PrivateBackendError: secret-file-path",
            "error_code": "upstream_timeout", "stage": "collect_suggestions"
        }),
    ))
    db.commit()
    page = client.get(f"/studio/products/{company.id}/tasks/{job.id}")
    assert page.status_code == 200
    assert "Сервис не ответил вовремя" in page.text
    assert "secret-file-path" not in page.text
    assert "PrivateBackendError" not in page.text


def test_tasks_search_finds_seo_focus_and_business_review_but_not_other_tenants(owner_studio):
    """Cross-module search should use user-facing saved context, tenant-scoped."""
    from app.api.content_studio_activity import work_page
    client, db, company, other = owner_studio
    seo = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: запросы, план и статьи", status=TaskStatus.COMPLETED)
    business = Task(product_id=company.id, assigned_agent="business_insights",
                    title="Маркетинг: разбор данных бизнеса", status=TaskStatus.COMPLETED)
    foreign = Task(product_id=other.id, assigned_agent="seo_factory",
                   title="SEO-завод: запросы, план и статьи", status=TaskStatus.COMPLETED)
    db.add_all([seo, business, foreign])
    db.flush()
    db.add_all([
        Artifact(product_id=company.id, task_id=seo.id, kind="seo_factory_request",
                 title="Запрос", status="draft",
                 content=json.dumps({"focus": "доставка керамических чашек"}, ensure_ascii=False)),
        Artifact(product_id=company.id, task_id=business.id, kind="business_review",
                 title="Разбор", status="draft",
                 content=json.dumps({"summary": "Вечерняя загрузка зала"}, ensure_ascii=False)),
        Artifact(product_id=other.id, task_id=foreign.id, kind="seo_factory_request",
                 title="Запрос", status="draft",
                 content=json.dumps({"focus": "скрытый чужой заказ"}, ensure_ascii=False)),
    ])
    db.commit()
    seo_results = work_page(db, company.id, query="керамических", kind="seo")
    assert seo_results["total"] == 1
    assert seo_results["items"][0]["id"] == str(seo.id)
    business_results = work_page(db, company.id, query="Вечерняя", kind="business")
    assert business_results["total"] == 1
    assert business_results["items"][0]["id"] == str(business.id)
    assert work_page(db, company.id, query="чужой")["total"] == 0
    page = client.get(f"/studio/products/{company.id}/work",
                      params={"q": "керамических", "kind": "seo"})
    assert page.status_code == 200
    assert f'/tasks/{seo.id}' in page.text
    assert f'/tasks/{foreign.id}' not in page.text


def test_running_seo_detail_marks_same_current_stage_as_work_feed(owner_studio):
    from app.api.content_studio_activity import work_page
    from app.api.content_studio_work_ui import JS
    client, db, company, _ = owner_studio
    seo = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: запросы", status=TaskStatus.RUNNING)
    db.add(seo)
    db.flush()
    stage = Artifact(product_id=company.id, task_id=seo.id,
                     kind="seo_factory_stage", status="draft",
                     title="Этап", content=json.dumps({
                         "stage": "collect_suggestions",
                         "recorded_at": "2026-11-22T05:00:00+00:00",
                     }))
    db.add(stage)
    db.commit()
    base = f"/studio/products/{company.id}"
    task_page = client.get(base + f"/tasks/{seo.id}")
    assert task_page.status_code == 200
    assert 'data-sg-stage-mode="seo"' in task_page.text
    assert 'data-sg-stage="Сбор поисковых подсказок"' in task_page.text
    assert work_page(db, company.id, kind="seo")["items"][0]["current_stage"] == (
        "Сбор поисковых подсказок"
    )
    stage.content = json.dumps({
        "stage": "build_plan",
        "recorded_at": "2026-11-22T05:05:00+00:00",
    })
    db.commit()
    assert work_page(db, company.id, kind="seo")["items"][0]["current_stage"] == (
        "Составление контент-плана"
    )
    assert "detail.dataset.sgStageMode==='seo'" in JS
    assert "updatedStage!==detail.dataset.sgStage" in JS
    assert "stageNode.textContent=updatedStage" in JS
    assert "const stateChanged=currentJob.state!==detail.dataset.sgState" in JS
    assert 'data-sg-seo-stage-text' in task_page.text
    assert 'role="status" aria-live="polite"' in task_page.text


def test_nonrunning_seo_page_does_not_request_phase_refresh(owner_studio):
    """A finished factory must not reload every poll due to a stale stage label."""
    client, db, company, _ = owner_studio
    seo = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: запросы", status=TaskStatus.COMPLETED)
    db.add(seo)
    db.commit()
    response = client.get(f"/studio/products/{company.id}/tasks/{seo.id}")
    assert response.status_code == 200
    assert 'data-sg-stage-mode="seo"' not in response.text


def test_activity_feed_tracks_old_open_job_outside_recent_window(owner_studio):
    """The exact active detail must update even with many newer active jobs."""
    from datetime import datetime, timedelta, timezone
    from app.api.content_studio_work_ui import JS
    client, db, company, other = owner_studio
    old = Task(product_id=company.id, assigned_agent="seo_factory",
               title="SEO-завод: старая активная задача",
               status=TaskStatus.RUNNING,
               created_at=datetime.now(timezone.utc)-timedelta(days=120))
    hidden_foreign = Task(product_id=other.id, assigned_agent="seo_factory",
                          title="Другое планирование", status=TaskStatus.RUNNING)
    db.add_all([old, hidden_foreign])
    for i in range(50):
        db.add(Task(product_id=company.id, assigned_agent="content_factory",
                    title=f"CONTENT: Новый запуск {i}", status=TaskStatus.RUNNING))
    db.commit()
    url = f"/studio/products/{company.id}/activity.json"
    plain = client.get(url)
    assert plain.status_code == 200
    plain_data = plain.json()
    assert str(old.id) not in {job["id"] for job in plain_data["jobs"]}
    tracked = client.get(url, params={"task_id": str(old.id)})
    assert tracked.status_code == 200
    tracked_data = tracked.json()
    assert sum(job["id"] == str(old.id) for job in tracked_data["jobs"]) == 1
    assert tracked_data["active_count"] == plain_data["active_count"]
    outsider = client.get(url, params={"task_id": str(hidden_foreign.id)})
    assert outsider.status_code == 200
    assert str(hidden_foreign.id) not in {job["id"] for job in outsider.json()["jobs"]}
    assert "feedUrl.searchParams.set('task_id',detail.dataset.sgTask)" in JS


def test_activity_feed_does_not_duplicate_fresh_open_job(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="seo_factory",
                title="SEO-завод: актуальная задача", status=TaskStatus.RUNNING)
    db.add(task)
    db.commit()
    data = client.get(f"/studio/products/{company.id}/activity.json",
                      params={"task_id": str(task.id)}).json()
    assert sum(row["id"] == str(task.id) for row in data["jobs"]) == 1


def test_tasks_unicode_casefold_cyrillic_search(owner_studio):
    """SQLite native lower/ILIKE is ASCII-only; Russian case must be normalized."""
    from app.api.content_studio_activity import work_page
    from sqlalchemy import func
    client, db, company, other = owner_studio
    assert db.scalar(select(func.sg_casefold("ВЕЧЕРНИЕ ЁЛКИ"))) == "вечерние ёлки"
    business = Task(product_id=company.id, assigned_agent="business_insights",
                    title="Бизнес-анализ", status=TaskStatus.COMPLETED)
    own = Task(product_id=company.id, assigned_agent="content_factory",
               title="CONTENT: КЕРАМИЧЕСКИЕ ИЗДЕЛИЯ", status=TaskStatus.COMPLETED)
    foreign = Task(product_id=other.id, assigned_agent="business_insights",
                   title="Бизнес-анализ", status=TaskStatus.COMPLETED)
    db.add_all([business, own, foreign])
    db.flush()
    db.add_all([
        Artifact(product_id=company.id, task_id=business.id,
                 title="Разбор", kind="business_review", status="draft",
                 content=json.dumps({"summary": "ВЕЧЕРНИЕ ПРОДАЖИ"}, ensure_ascii=False)),
        Artifact(product_id=other.id, task_id=foreign.id,
                 title="Разбор", kind="business_review", status="draft",
                 content=json.dumps({"summary": "ЧУЖИЕ ДАННЫЕ"}, ensure_ascii=False)),
    ])
    db.commit()
    assert work_page(db, company.id, query="вечерние")["items"][0]["id"] == str(business.id)
    assert work_page(db, company.id, query="керамические")["items"][0]["id"] == str(own.id)
    assert work_page(db, company.id, query="чужие")["total"] == 0
    response = client.get(f"/studio/products/{company.id}/work",
                          params={"q": "вечерние", "kind": "business"})
    assert response.status_code == 200
    assert f'/tasks/{business.id}' in response.text


def test_running_business_analysis_detail_tracks_completion(owner_studio):
    """Results page can notice that a pending business review was completed."""
    client, db, company, _ = owner_studio
    job = Task(product_id=company.id, assigned_agent="business_insights",
               title="Разбор данных бизнеса", status=TaskStatus.RUNNING)
    db.add(job)
    db.commit()
    base = f"/studio/products/{company.id}"
    page = client.get(f"{base}/tasks/{job.id}")
    assert page.status_code == 200
    assert f'data-sg-task="{job.id}"' in page.text
    assert 'data-sg-state="running"' in page.text
    job.status = TaskStatus.COMPLETED
    db.commit()
    feed = client.get(base + "/activity.json", params={"task_id": str(job.id)})
    assert feed.status_code == 200
    assert any(x["id"] == str(job.id) and x["state"] == "completed"
               for x in feed.json()["jobs"])


def test_failed_content_retry_requires_confirmation_without_side_effect(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_editor",
                title="EDIT: Неудачное исправление", status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    endpoint = f"/studio/products/{company.id}/tasks/{task.id}/retry"
    # Missing or forged acknowledgement must never requeue a paid step.
    assert client.post(endpoint, follow_redirects=False).status_code == 409
    assert client.post(endpoint, data={"confirm_retry": "no"},
                       follow_redirects=False).status_code == 409
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    approved = client.post(endpoint, data={"confirm_retry": "yes"},
                           follow_redirects=False)
    assert approved.status_code == 303
    assert db.get(Task, task.id).status == TaskStatus.QUEUED
    assert client.post(endpoint, data={"confirm_retry": "yes"}).status_code == 409


def test_retry_disclosure_has_accessible_label_and_touch_targets():
    from app.api.content_studio_work_ui import CSS
    assert ".sg-work-retry-confirm>summary" in CSS
    assert ".sg-work-retry-consent" in CSS
    assert "min-height:44px" in CSS
    assert "accent-color:var(--s-accent" in CSS

@pytest.mark.parametrize("error,expected", [
    ("quick_post_failed:ReadTimeout:private-token", "Превышено время ожидания ответа сервиса."),
    ("creative_failed:ConnectError", "Не удалось установить соединение с сервисом."),
    ("upstream_failed:provider_request_failed:post_writer_editor", "Сервис обработки вернул ошибку."),
    ("assembly_failed:invalid_article_output:marketing_editor", "Результат генерации не прошёл проверку формата или содержимого."),
    ("local_render_failed:RuntimeError", "Не удалось собрать видеофайл."),
    ("One or more image frames could not be generated", "Не удалось создать одно или несколько изображений."),
    ("creative_failed:SecretCustomException:https://private.example/api", "Точная причина ошибки не определена по сохранённому журналу."),
])
def test_owner_failure_reason_is_conservative_and_never_echoes_provider_error(error, expected):
    from app.api.content_studio_activity import owner_failure_reason
    run = AgentRun(task_id=uuid4(), agent_name="creative_director",
                   status="failed", error=error)
    assert owner_failure_reason([run]) == expected
    assert owner_failure_reason([]) == "Подробная причина не сохранена в журнале выполнения."
    # An earlier failed step must not contaminate a successfully completed task.
    assert owner_failure_reason([AgentRun(task_id=uuid4(), agent_name="post_strategist",
                                           status="completed", error=error)]) == (
        "Подробная причина не сохранена в журнале выполнения."
    )


def test_task_failure_explanation_is_private_and_updates_after_recovery(owner_studio):
    client, db, company, foreign = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_factory",
                title="POST: Сезонная акция", status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    private_error = "quick_post_failed:ReadTimeout:secret-api-key-do-not-show"
    db.add(AgentRun(task_id=task.id, agent_name="post_writer_editor",
                    status="failed", error=private_error))
    db.commit()
    base = f"/studio/products/{company.id}"
    detail = client.get(f"{base}/tasks/{task.id}")
    assert detail.status_code == 200
    assert "Что произошло:" in detail.text
    assert "Превышено время ожидания ответа сервиса." in detail.text
    assert "secret-api-key-do-not-show" not in detail.text
    problems = client.get(base + "/work?tab=problems")
    assert problems.status_code == 200
    assert "Превышено время ожидания ответа сервиса." in problems.text
    assert "secret-api-key-do-not-show" not in problems.text
    activity = client.get(base + "/activity.json").json()
    assert private_error not in str(activity)
    assert "Превышено время ожидания ответа сервиса." in str(activity)
    assert client.get(f"/studio/products/{foreign.id}/tasks/{task.id}").status_code == 404
    assert private_error not in client.get(
        f"/studio/products/{foreign.id}/work?tab=problems"
    ).text
    task.status = TaskStatus.COMPLETED
    db.commit()
    recovered = client.get(f"{base}/tasks/{task.id}").text
    assert "Что произошло:" not in recovered
    assert "Превышено время ожидания ответа сервиса." not in recovered
    assert "Превышено время ожидания ответа сервиса." not in client.get(
        base + "/work?tab=ready"
    ).text


def test_unknown_failure_remains_unknown_without_leaking_details(owner_studio):
    client, db, company, _ = owner_studio
    task = Task(product_id=company.id, assigned_agent="content_creative",
                title="CREATIVE: Открытка", status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    db.add(AgentRun(task_id=task.id, agent_name="creative_director",
                    status="failed", error="creative_failed:PrivateBackendError:secret/path"))
    db.commit()
    base = f"/studio/products/{company.id}"
    for url in (f"{base}/tasks/{task.id}", base + "/work?tab=problems"):
        html = client.get(url).text
        assert "Точная причина ошибки не определена по сохранённому журналу." in html
        assert "PrivateBackendError" not in html
        assert "secret/path" not in html
