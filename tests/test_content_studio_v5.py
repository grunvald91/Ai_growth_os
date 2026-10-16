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
    for label in ["Пост", "Сценарий видео", "Stories", "Картинка", "Полный пакет"]:
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
    assert post.headers["location"].split("/")[-2] == "tasks"
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


def test_web_planner_can_receive_requirements_without_replacing_current_plan(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    plan = client.get(base + "/plan")
    assert plan.status_code == 200
    assert "Нужен новый план?" in plan.text
    result = client.post(base + "/plan/generate", data={
        "requirements": "Нужен план на 14 дней: полезные посты и две идеи короткого видео.",
    }, follow_redirects=False)
    assert result.status_code == 303
    preference = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id,
        Artifact.kind == "editorial_preferences",
    ))
    assert preference is not None
    task = db.scalar(select(Task).where(
        Task.product_id == company.id, Task.assigned_agent == "editorial_planner",
    ))
    assert task is not None and task.status == TaskStatus.QUEUED
    assert list(db.scalars(select(Publication))) == []


def test_storyboard_page_is_clearly_an_unrendered_brief(studio_v5):
    client, db, company, _ = studio_v5
    scene = {
        "number": 1, "start_second": 0, "end_second": 8,
        "purpose": "Показать основу композиции",
        "on_screen_text": "Красота в деталях",
        "visual_prompt": "Вертикальный кадр мастерской: стол, цветы, естественный свет",
        "action_or_animation": "Медленное движение камеры",
        "spoken_text": "Покажем детали подготовки",
    }
    creative = Artifact(
        product_id=company.id, kind="creative_brief", title="Stories о цветах",
        status="awaiting_approval", content=json.dumps({
            "format":"story", "platform":"telegram",
            "frames":[scene], "preview_text":"Черновик Stories, не готовое изображение",
        }, ensure_ascii=False),
    )
    db.add(creative)
    db.commit()
    base=f"/studio/products/{company.id}"
    response=client.get(base + f"/materials/{creative.id}")
    assert response.status_code == 200
    assert "Кадр 1" in response.text
    assert "не создавались" in response.text
    assert "Утвердить материал" in response.text
    assert "sg-storyboard-screen" in response.text


def test_library_pagination_limits_page_size_and_preserves_filters(studio_v5):
    client, db, company, _ = studio_v5
    for i in range(28):
        db.add(Artifact(
            product_id=company.id, kind="channel_text",
            title=f"Материал {i:02d}",
            created_at=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            content=json.dumps({"channel":"telegram",
                                "body": f"Текст публикации с номером {i:02d}"}, ensure_ascii=False),
        ))
    db.commit()
    base = f"/studio/products/{company.id}/materials"
    first = client.get(base + "?kind=post")
    assert first.status_code == 200
    assert "Страница 1" in first.text
    assert "Следующая" in first.text
    assert first.text.count('class="material-card') <= 24
    second = client.get(base + "?kind=post&page=2")
    assert second.status_code == 200
    assert "Страница 2" in second.text
    assert "Материал 00" in second.text
    assert "Материал 27" not in second.text
    searched = client.get(base + "?kind=post&q=Материал%2007")
    assert searched.status_code == 200
    assert "Материал 07" in searched.text
    assert "Материал 06" not in searched.text



def test_official_servgo_brand_matches_booking_component(studio_v5):
    client, db, company, _ = studio_v5
    logo = client.get("/studio/assets/servgo.svg")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/svg+xml")
    assert 'viewBox="0 0 48 48"' in logo.text
    assert 'fill="#0D1B2A"' in logo.text
    assert 'fill="#2F5BEB"' in logo.text
    assert 'L44 23 L31.5 35.5' in logo.text
    base = f"/studio/products/{company.id}"
    main = client.get(base)
    assert main.status_code == 200
    assert "/studio/assets/servgo.svg" in main.text
    assert 'sg-official-brand-name">Serv<span>Go</span>' in main.text
    assert '<span class="brand-mark">S</span>' not in main.text
    assert 'studio.css?v=26' in main.text

    css = client.get("/studio/assets/studio.css").text
    assert '--sg-blue:#2F5BEB' in css
    assert '--sg-navy:#0D1B2A' in css
    assert '.sg-official-brand-icon' in css


def test_mobile_navigation_does_not_overlay_cards_and_sidebar_has_no_duplicate_calendar(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    for route in ("", "/create", "/review", "/calendar", "/materials", "/channels", "/plan"):
        page = client.get(base + route)
        assert page.status_code == 200, route
        markup = page.text
        assert markup.count('class="mobile-nav"') == 1
        assert markup.count('class="sg-mobile-shell-head"') == 1
        assert markup.index('class="sg-mobile-shell-head"') < markup.index('class="mobile-nav"')
        assert markup.index('class="mobile-nav"') < markup.index('<main class="main')
        sidebar = markup.split('<aside class="sidebar">', 1)[1].split('</aside>', 1)[0]
        assert sidebar.count(f'href="{base}/calendar"') == 1
        # The advanced Comments tool stays discoverable, but isn't one of
        # the four primary navigation actions.
        core = sidebar.split('aria-label="Основная навигация"',1)[1].split('</nav>',1)[0]
        assert "Комментарии" not in core
        assert "Комментарии" in sidebar
    css = client.get("/studio/assets/studio.css").text
    assert 'position:sticky!important' in css
    assert 'bottom:auto!important' in css
    assert '.sg-quick-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))' in css


def test_recent_materials_use_russian_status_and_readable_titles(studio_v5):
    client, db, company, _ = studio_v5
    item = Artifact(
        product_id=company.id,
        title="telegram derivative brief",
        kind="telegram_brief", status="draft",
        content=json.dumps({"brief": "Текстовую публикацию нужно подготовить"}),
    )
    post = Artifact(
        product_id=company.id, kind="channel_text",
        title="Новый пост", status="awaiting_approval",
        content=json.dumps({"channel":"telegram","body":"Текст сообщения"}, ensure_ascii=False),
    )
    db.add_all([item, post])
    db.commit()
    page = client.get(f"/studio/products/{company.id}")
    assert page.status_code == 200
    assert "Бриф для Telegram" in page.text
    assert "На проверке" in page.text
    assert "awaiting_approval</small>" not in page.text
    assert "telegram derivative brief</b>" not in page.text


def test_new_draft_approval_is_accessible_responsive_card_not_bare_panel(studio_v5):
    """Reproduce the cramped/overflowing notice reported on Android."""
    client, db, company, _ = studio_v5
    now = datetime.now(timezone.utc)
    active = Artifact(
        product_id=company.id, kind="editorial_plan", title="Рабочий контент-план",
        status="approved",
        content=json.dumps({
            "approval_state": "active",
            "generated_at": (now - timedelta(days=2)).isoformat(),
            "timezone": "Europe/Moscow", "horizon_days": 14, "slots": [],
        }, ensure_ascii=False),
    )
    draft = Artifact(
        product_id=company.id, kind="editorial_plan", title="Новый вариант плана",
        status="draft",
        content=json.dumps({
            "approval_state": "draft",
            "generated_at": now.isoformat(),
            "timezone": "Europe/Moscow", "horizon_days": 14, "slots": [],
        }, ensure_ascii=False),
    )
    db.add_all([active, draft])
    db.commit()
    page = client.get(f"/studio/products/{company.id}")
    assert page.status_code == 200
    assert page.text.count('class="sg-draft-alert"') == 1
    assert page.text.count('id="sg-draft-title"') == 1
    assert "Новый контент-план готов" in page.text
    assert "Текущий план продолжит работать" in page.text
    assert f'/studio/products/{company.id}/plan?view=draft' in page.text
    assert page.text.index('class="sg-draft-alert"') < page.text.index('class="sg-dashboard-feed"')
    assert 'class="panel" role="status"><b>Новый контент-план' not in page.text
    css = client.get("/studio/assets/studio.css").text
    assert ".sg-draft-alert{" in css
    assert "grid-template-columns:38px minmax(0,1fr)" in css
    assert 'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto' in css


def test_v8_typography_consistent_across_all_studio_views(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    css = client.get("/studio/assets/studio.css").text
    assert css.rfind("/* v8: comprehensive ServGo typography") > css.rfind("/* v6.1:")
    for needle in (
        "--sg-font-ui:",
        "--sg-fs-body:15px",
        ".sg-dashboard-feed .sg-section-head h2",
        ".sg-mini-row b",
        ".material-body h4",
        ".calendar-topic b",
        ".sg-review-item h3",
        ".sg-storyboard-card p",
        ".sg-post-preview",
        ".studio-page input",
        ".sg-draft-alert h2",
        "font-weight:600",
        "font-size:16px;min-height:44px",
    ):
        assert needle in css, needle
    for route in ("", "/create", "/review", "/calendar", "/materials", "/channels", "/plan"):
        result = client.get(base + route)
        assert result.status_code == 200, route
        assert "studio.css?v=26" in result.text
        assert 'data-studio-build="20261013-unified10"' in result.text
        assert "/studio/assets/servgo.svg" in result.text
