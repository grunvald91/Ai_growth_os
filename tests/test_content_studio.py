import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.api.content_studio import latest_plan, latest_draft, state_for_group, reconcile_plan_states
from app.models.domain import Artifact, Product, Task, TaskStatus, ArtifactStatus


@pytest.fixture
def studio_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def client_for(db):
    def override():
        yield db
    app.dependency_overrides[get_db] = override
    return TestClient(app)


def make_plan(task_id=None, approval_state="draft"):
    return {
        "generated_at": "2026-10-04T12:00:00+00:00",
        "approval_state": approval_state,
        "horizon_days": 14,
        "review_at": "2026-10-12T00:00:00+03:00",
        "slots": [
            {
                "package_key": "pkg-1", "topic": "Тема один", "platform": "instagram_reels",
                "format": "short_video", "publish_at": "2026-10-06T12:00:00+03:00",
                "prepare_at": "2026-10-05T12:00:00+03:00", "state": "ready_for_review",
                "task_id": str(task_id) if task_id else None, "audience": "Владельцы бизнеса",
                "objective": "Получить обращения"
            },
            {
                "package_key": "pkg-1", "topic": "Тема один", "platform": "dzen",
                "format": "article", "publish_at": "2026-10-06T10:00:00+03:00",
                "prepare_at": "2026-10-05T10:00:00+03:00", "state": "planned",
                "task_id": str(task_id) if task_id else None, "audience": "Владельцы бизнеса",
                "objective": "Получить обращения"
            }
        ]
    }


def test_studio_lists_products_and_renders_calendar(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    db.add(Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(),ensure_ascii=False)))
    db.commit()
    client=client_for(db)
    try:
        assert client.get("/studio").status_code==200
        response=client.get(f"/studio/products/{product.id}")
        assert response.status_code==200
        assert "Growth Studio" in response.text
        assert "Контент-календарь" in response.text
        assert "Подготовленные материалы" in response.text
        assert "mobile-nav" in response.text
        assert "mobile-only-dashboard" in response.text
        assert "m-brand" in response.text
        assert "m-kpis" in response.text
        assert "m-timeline" in response.text
        assert "m-package-carousel" in response.text
        assert "m-material-strip" in response.text
        assert "Instagram Reels" in response.text
        assert "Тема один" in response.text
        assert response.headers["cache-control"]=="no-store"
    finally:
        app.dependency_overrides.clear()


def test_studio_renders_storyboard_only_video_brief(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    task=Task(product_id=product.id,title="CONTENT",assigned_agent="content_factory",status=TaskStatus.COMPLETED)
    db.add(task);db.flush()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(task.id),ensure_ascii=False))
    brief={
        "article_id": str(uuid4()), "hook": "Хук", "production_brief": {
            "target_platform":"instagram_reels","objective":"Объяснить процесс",
            "hook_variants":["Хук 1","Хук 2"],"voiceover_or_dialogue_script":"Текст ролика",
            "shots":[{"start_second":0,"end_second":5,"visual_description":"Ведущий в кадре",
                      "camera_and_movement":"Средний план","on_screen_text":"Текст",
                      "transition":"Склейка","generation_prompt":"Vertical realistic presenter shot"}],
            "editing_mix_notes":"Голос впереди музыки","cover_frame":"Обложка"
        }
    }
    video=Artifact(product_id=product.id,task_id=task.id,kind="video_script",title="Instagram Reels",content=json.dumps(brief,ensure_ascii=False))
    db.add_all([plan,video]);db.commit()
    client=client_for(db)
    try:
        response=client.get(f"/studio/products/{product.id}")
        assert response.status_code==200
        assert "Промпт" in response.text
        assert "Vertical realistic presenter shot" in response.text
        assert "Instagram Reels" in response.text
    finally:
        app.dependency_overrides.clear()


def test_studio_can_queue_planned_package_without_publishing(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(approval_state="active"),ensure_ascii=False))
    db.add(plan);db.commit()
    client=client_for(db)
    try:
        response=client.post(f"/studio/products/{product.id}/plans/{plan.id}/packages/pkg-1/queue",follow_redirects=False)
        assert response.status_code==303
        task=db.query(Task).filter(Task.assigned_agent=="content_factory").one()
        assert task.status==TaskStatus.QUEUED
        assert db.query(Artifact).filter(Artifact.kind=="content_request",Artifact.task_id==task.id).count()==1
    finally:
        app.dependency_overrides.clear()


def test_studio_rejects_queue_from_unapproved_plan(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(),ensure_ascii=False))
    db.add(plan);db.commit()
    client=client_for(db)
    try:
        response=client.post(f"/studio/products/{product.id}/plans/{plan.id}/packages/pkg-1/queue",follow_redirects=False)
        assert response.status_code==409
        assert db.query(Task).filter(Task.assigned_agent=="content_factory").count()==0
    finally:
        app.dependency_overrides.clear()


def test_studio_library_includes_content_outside_current_plan(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(),ensure_ascii=False))
    manual=Task(product_id=product.id,title="CONTENT: Внеплановая тема",assigned_agent="content_factory",status=TaskStatus.COMPLETED)
    db.add_all([plan,manual]);db.flush()
    article=Artifact(product_id=product.id,task_id=manual.id,kind="article",title="Внеплановая статья",
        content=json.dumps({"full_article":"Полный внеплановый материал"},ensure_ascii=False))
    db.add(article);db.commit()
    client=client_for(db)
    try:
        response=client.get(f"/studio/products/{product.id}")
        assert response.status_code==200
        assert "Подготовленные материалы" in response.text
        assert "Внеплановая статья" in response.text
        assert "Полный внеплановый материал" in response.text
    finally:
        app.dependency_overrides.clear()


def test_studio_assets_expose_servgo_design_system(studio_db):
    client=client_for(studio_db)
    try:
        css=client.get("/studio/assets/studio.css")
        js=client.get("/studio/assets/studio.js")
        assert css.status_code==200
        assert js.status_code==200
        assert "--sg-blue:#0b7cff" in css.text
        assert ".mobile-nav" in css.text
        assert ".material-grid" in css.text
        assert ".m-kpis" in css.text
        assert ".m-day.active" in css.text
        assert ".m-package-carousel" in css.text
        assert ".m-material-strip" in css.text
        assert "data-open-dialog" in js.text
    finally:
        app.dependency_overrides.clear()


def test_studio_root_redirects_to_servgo_dashboard(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.commit()
    client=client_for(db)
    try:
        response=client.get("/studio",follow_redirects=False)
        assert response.status_code==302
        assert response.headers["location"]==f"/studio/products/{product.id}"
        chooser=client.get("/studio?choose=true")
        assert chooser.status_code==200
        assert "Выберите проект" in chooser.text
    finally:
        app.dependency_overrides.clear()


def test_studio_all_navigation_targets_are_real_pages(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    task=Task(product_id=product.id,title="CONTENT",assigned_agent="content_factory",status=TaskStatus.COMPLETED)
    db.add(task);db.flush()
    plan=Artifact(
        product_id=product.id,kind="editorial_plan",title="План",
        content=json.dumps(make_plan(task.id),ensure_ascii=False)
    )
    article=Artifact(
        product_id=product.id,task_id=task.id,kind="article",title="Рабочая статья",
        content=json.dumps({"full_article":"Полный рабочий текст статьи"},ensure_ascii=False)
    )
    db.add_all([plan,article]);db.commit()
    client=client_for(db)
    try:
        base=f"/studio/products/{product.id}"
        overview=client.get(base)
        assert overview.status_code==200
        assert f'href="{base}/plan"' in overview.text
        assert f'href="{base}/calendar"' in overview.text
        assert f'href="{base}/materials"' in overview.text
        assert f'href="{base}/analytics"' in overview.text
        assert f'href="{base}/packages/pkg-1"' in overview.text
        assert f'href="{base}/calendar"' in overview.text
        assert 'href="#calendar"' not in overview.text
        assert 'href="#plan"' not in overview.text
        assert 'href="#materials"' not in overview.text
        assert 'href="#analytics"' not in overview.text
        assert f'href="{base}/plan?status=ready"' in overview.text
        assert f'href="{base}/plan?status=failed"' in overview.text
        assert f'href="{base}/analytics" aria-label="Аналитика"' in overview.text
        assert 'href="/studio?choose=1" aria-label="Выбрать проект"' in overview.text
        assert 'href="/studio/team"' in overview.text
        for route in ["comments", "channels", "whiteboard", "review"]:
            assert f'href="{base}/{route}"' in overview.text

        plan_page=client.get(base+"/plan")
        calendar_page=client.get(base+"/calendar")
        materials_page=client.get(base+"/materials")
        analytics_page=client.get(base+"/analytics")
        package_page=client.get(base+"/packages/pkg-1")
        material_page=client.get(base+f"/materials/{article.id}")
        for response in (plan_page,calendar_page,materials_page,analytics_page,package_page,material_page):
            assert response.status_code==200

        assert "Контент-план" in plan_page.text
        assert "Расписание публикаций" in calendar_page.text
        assert "Библиотека" in materials_page.text
        assert "Статусы публикаций" in analytics_page.text
        assert "Тема один" in package_page.text
        assert "Полный рабочий текст статьи" in material_page.text
        assert 'data-copy="material-copy"' in material_page.text
    finally:
        app.dependency_overrides.clear()


def test_studio_calendar_date_filter_and_material_search_work(studio_db):
    db=studio_db
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.flush()
    task=Task(product_id=product.id,title="CONTENT",assigned_agent="content_factory",status=TaskStatus.COMPLETED)
    db.add(task);db.flush()
    db.add(Artifact(
        product_id=product.id,kind="editorial_plan",title="План",
        content=json.dumps(make_plan(task.id),ensure_ascii=False)
    ))
    db.add_all([
        Artifact(product_id=product.id,task_id=task.id,kind="article",title="Нужная статья",
                 content=json.dumps({"full_article":"Редкое ключевое слово альбатрос"},ensure_ascii=False)),
        Artifact(product_id=product.id,task_id=task.id,kind="channel_text",title="Другой пост",
                 content=json.dumps({"channel":"telegram","body":"Обычный текст"},ensure_ascii=False)),
    ])
    db.commit()
    client=client_for(db)
    try:
        base=f"/studio/products/{product.id}"
        calendar=client.get(base+"/calendar?date=2026-10-06")
        assert calendar.status_code==200
        assert "Тема один" in calendar.text

        search=client.get(base+"/materials?q=альбатрос")
        assert search.status_code==200
        assert "Нужная статья" in search.text
        assert "Другой пост" not in search.text

        posts=client.get(base+"/materials?kind=post")
        assert posts.status_code==200
        assert "Другой пост" in posts.text
        assert "Нужная статья" not in posts.text
    finally:
        app.dependency_overrides.clear()


def test_active_plan_is_stable_when_new_draft_is_generated(studio_db):
    db = studio_db
    product = Product(name="Пекарня", slug="bakery-plan")
    db.add(product)
    db.flush()
    active = make_plan(approval_state="active")
    active["generated_at"] = "2026-10-01T12:00:00+00:00"
    active["slots"][0]["topic"] = active["slots"][1]["topic"] = "Действующий календарь"
    draft = make_plan(approval_state="draft")
    draft["generated_at"] = "2026-10-11T12:00:00+00:00"
    draft["slots"][0]["topic"] = draft["slots"][1]["topic"] = "Новый план"
    current_row = Artifact(product_id=product.id, kind="editorial_plan", title="Действует",
                           content=json.dumps(active, ensure_ascii=False))
    draft_row = Artifact(product_id=product.id, kind="editorial_plan", title="Черновик",
                         content=json.dumps(draft, ensure_ascii=False))
    db.add_all([current_row, draft_row])
    db.commit()

    selected, _ = latest_plan(db, product.id)
    newest_draft, _ = latest_draft(db, product.id)
    assert selected.id == current_row.id
    assert newest_draft.id == draft_row.id

    client = client_for(db)
    try:
        base = f"/studio/products/{product.id}"
        dashboard = client.get(base)
        assert dashboard.status_code == 200
        assert "Действующий календарь" in dashboard.text
        assert "Новый контент-план готов" in dashboard.text
        assert f'href="{base}/plan?view=draft"' in dashboard.text

        current_page = client.get(base + "/plan")
        assert "Действующий календарь" in current_page.text
        assert "Новый план" not in current_page.text

        draft_page = client.get(base + "/plan?view=draft")
        assert "Новый план" in draft_page.text
        assert f'/plans/{draft_row.id}/approve' in draft_page.text
        assert "Действующий план" in draft_page.text
    finally:
        app.dependency_overrides.clear()


def test_failed_package_can_retry_without_duplicate_or_replacing_active_job(studio_db):
    db = studio_db
    product = Product(name="Салон", slug="salon-retry")
    db.add(product)
    db.flush()
    previous_task = Task(product_id=product.id, title="calendar:legacy",
                         assigned_agent="content_factory", status=TaskStatus.FAILED)
    db.add(previous_task)
    db.flush()
    plan = make_plan(previous_task.id, approval_state="active")
    for slot in plan["slots"]:
        slot["state"] = "failed"
    plan_row = Artifact(product_id=product.id, kind="editorial_plan", title="План",
                        content=json.dumps(plan, ensure_ascii=False))
    db.add(plan_row)
    db.commit()

    client = client_for(db)
    try:
        base = f"/studio/products/{product.id}"
        detail = client.get(base + "/packages/pkg-1")
        assert detail.status_code == 200
        assert "Повторить подготовку" in detail.text
        response = client.post(
            base + f"/plans/{plan_row.id}/packages/pkg-1/queue", follow_redirects=False
        )
        assert response.status_code == 303
        db.refresh(plan_row)
        refreshed_slots = json.loads(plan_row.content)["slots"]
        ids = {slot["task_id"] for slot in refreshed_slots}
        assert len(ids) == 1
        assert str(previous_task.id) not in ids
        assert {slot["state"] for slot in refreshed_slots} == {"queued"}
        new_task = db.get(Task, UUID(next(iter(ids))))
        assert new_task is not None
        assert new_task.status == TaskStatus.QUEUED
        assert new_task.product_id == product.id
        assert db.get(Task, previous_task.id).status == TaskStatus.FAILED

        duplicate = client.post(
            base + f"/plans/{plan_row.id}/packages/pkg-1/queue", follow_redirects=False
        )
        assert duplicate.status_code == 409
        assert len(list(db.scalars(select(Task).where(
            Task.product_id == product.id, Task.assigned_agent == "content_factory"
        )))) == 2
    finally:
        app.dependency_overrides.clear()


def test_studio_group_states_include_completed_and_cancelled():
    assert state_for_group([{"state": "completed"}]) == "completed"
    assert state_for_group([{"state": "cancelled"}]) == "cancelled"
    assert state_for_group([{"state": "ready_for_review"}, {"state": "planned"}]) == "ready_for_review"
    assert state_for_group([{"state": "failed"}, {"state": "completed"}]) == "failed"


def test_failed_task_is_visible_before_scheduler_tick_without_persisting_derived_status(studio_db):
    db = studio_db
    product = Product(name="Компания", slug="company-stale-state")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, title="CONTENT: current",
                assigned_agent="content_factory", status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    plan = make_plan(task.id, approval_state="active")
    for slot in plan["slots"]:
        slot["state"] = "queued"
    row = Artifact(product_id=product.id, kind="editorial_plan", title="План",
                   content=json.dumps(plan, ensure_ascii=False))
    db.add(row)
    db.commit()

    reconciled = reconcile_plan_states(db, product.id, json.loads(row.content))
    assert {slot["state"] for slot in reconciled["slots"]} == {"failed"}
    assert {slot["state"] for slot in json.loads(row.content)["slots"]} == {"queued"}
    client = client_for(db)
    try:
        detail = client.get(f"/studio/products/{product.id}/packages/pkg-1")
        assert detail.status_code == 200
        assert "Повторить подготовку" in detail.text
    finally:
        app.dependency_overrides.clear()


def test_studio_post_detail_shows_version_history_and_editorial_warnings(studio_db):
    db = studio_db
    product = Product(name="Студия цветов", slug="flowers-versioned")
    db.add(product)
    db.flush()
    origin = Artifact(
        product_id=product.id, kind="channel_text",
        title="Первый вариант",
        content=json.dumps({
            "origin": "quick_post", "title": "Первый вариант",
            "channel": "telegram", "body": "Старая версия",
            "quality_warnings": ["Уберите общие слова"],
        }, ensure_ascii=False),
        status=ArtifactStatus.REJECTED,
    )
    db.add(origin)
    db.flush()
    revision = Artifact(
        product_id=product.id, kind="channel_text",
        title="Второй вариант",
        content=json.dumps({
            "origin": "owner_revision", "title": "Второй вариант",
            "channel": "telegram", "body": "Новая версия",
            "revision_of": str(origin.id),
            "revision_root_id": str(origin.id),
            "revision_number": 2,
            "quality_warnings": ["Уточните доказательства"],
        }, ensure_ascii=False),
        status=ArtifactStatus.AWAITING_APPROVAL,
    )
    db.add(revision)
    db.commit()
    client = client_for(db)
    try:
        response = client.get(f"/studio/products/{product.id}/materials/{revision.id}")
        assert response.status_code == 200
        assert "История текста" in response.text
        assert "Проверить перед публикацией" in response.text
        assert "Уточните доказательства" in response.text
        assert f"/materials/{origin.id}" in response.text
        assert f"/materials/{revision.id}" in response.text
        assert "Новая версия" in response.text
    finally:
        app.dependency_overrides.clear()
def test_mobile_navigation_has_four_primary_controls():
    from html.parser import HTMLParser
    from uuid import uuid4
    from app.api.content_studio import mobile_nav_html

    class Navigation(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_bottom = False
            self.controls = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'nav' and attrs.get('class') == 'mobile-nav':
                self.in_bottom = True
            if self.in_bottom and tag in ('a', 'button'):
                self.controls.append(attrs)
        def handle_endtag(self, tag):
            if tag == 'nav':
                self.in_bottom = False

    product_id = uuid4()
    nav = Navigation(); nav.feed(mobile_nav_html(product_id, 'work'))
    base = f'/studio/products/{product_id}'
    assert [c['href'] for c in nav.controls] == [base, base + '/create', base + '/work', base + '/materials']
    assert nav.controls[2].get('class') == 'active'
    # Unified v10 removed the old "Все разделы" dialog; secondary sections live in page links.
    assert 'mobile-sections' not in mobile_nav_html(product_id)
