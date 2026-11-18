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
        response=client.get(f"/studio/products/{product.id}/classic")
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
        response=client.get(f"/studio/products/{product.id}/classic")
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
        response=client.get(f"/studio/products/{product.id}/classic")
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
        assert "Ваши компании" in chooser.text
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
        overview=client.get(base+"/classic")
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
        assert "Статусы публикационных слотов" in analytics_page.text
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
        assert "Сроки черновика истекли" in draft_page.text
        assert f'/plans/{draft_row.id}/approve' not in draft_page.text
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
    assert [c['href'] for c in nav.controls[:4]] == [base, base + '/create', base + '/work', base + '/materials']
    assert nav.controls[2].get('class') == 'active'
    # Every other section is one tap away in the bottom "Ещё" sheet.
    assert 'class="m-more"' in mobile_nav_html(product_id) and base + '/accounts' in mobile_nav_html(product_id)


def test_desktop_sidebar_shows_accounts_link_without_opening_the_extras():
    from uuid import uuid4
    from app.api.content_studio import sidebar_html
    product_id = uuid4()
    html = sidebar_html(product_id)
    main, extras = html.split('<details class="sg-side-extras">')
    assert f'/studio/products/{product_id}/accounts' in main and 'Аккаунты' in main
    assert 'Аккаунты' not in extras


def test_planner_failed_run_is_visible_only_to_its_company(studio_db):
    from app.api.content_studio import planner_notice_html
    from app.models.domain import AgentRun

    a = Product(name="ProResultat", slug="proresultat-planner-status")
    b = Product(name="Other", slug="other-planner-status")
    studio_db.add_all([a, b])
    studio_db.flush()
    task = Task(product_id=a.id, title="Редактор: контент-план",
                assigned_agent="editorial_planner", status=TaskStatus.FAILED)
    studio_db.add(task)
    studio_db.flush()
    studio_db.add(AgentRun(task_id=task.id, agent_name="editorial_planner",
                           status="failed",
                           error="editorial_plan_failed:schema:ValidationError:<script>alert(1)</script>"))
    studio_db.commit()

    notice = planner_notice_html(studio_db, a.id)
    assert "Не удалось создать новый контент-план" in notice
    assert "не прошёл проверку структуры" in notice
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in notice
    assert "<script>alert(1)</script>" not in notice
    assert planner_notice_html(studio_db, b.id) == ""

    client = client_for(studio_db)
    try:
        page = client.get(f"/studio/products/{a.id}/plan")
        assert page.status_code == 200
        assert "Не удалось создать новый контент-план" in page.text
        assert "Подготовить новый контент-план" in page.text
    finally:
        app.dependency_overrides.clear()


def test_completed_proresultat_plan_is_visible_as_draft_and_needs_approval(studio_db):
    from app.api.content_studio import planner_notice_html
    db = studio_db
    company = Product(name="ProResultat", slug="proresultat-plan-ready")
    db.add(company)
    db.flush()
    task = Task(product_id=company.id, title="Редактор: контент-план",
                assigned_agent="editorial_planner", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    payload = make_plan(approval_state="draft")
    payload["generated_at"] = "2026-11-05T18:30:26+00:00"
    plan = Artifact(product_id=company.id, task_id=task.id, kind="editorial_plan",
                    title="Новый контент-план",
                    content=json.dumps(payload, ensure_ascii=False),
                    status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(plan)
    db.commit()
    notice = planner_notice_html(db, company.id)
    assert "Новый контент-план готов" in notice
    assert f"/studio/products/{company.id}/plan?view=draft" in notice
    client = client_for(db)
    try:
        page = client.get(f"/studio/products/{company.id}/plan")
        assert page.status_code == 200
        assert "Новый контент-план готов" in page.text
        assert "Черновик плана" in page.text
        assert "Сроки черновика истекли" in page.text
        assert f"/plans/{plan.id}/approve" not in page.text
    finally:
        app.dependency_overrides.clear()


def test_calendar_ui_sdk_preview_uses_existing_scoped_move_form_and_can_be_disabled(studio_db):
    db = studio_db
    company = Product(name="Календарь SDK", slug="calendar-ui-sdk-demo")
    db.add(company)
    db.flush()
    plan_data = make_plan(approval_state="active")
    plan_data["timezone"] = "Europe/Moscow"
    plan_data["slots"][0].update({
        "slot_id": "slot-1", "day_offset": 2, "hour": 12, "minute": 0,
        "prepare_lead_hours": 24,
    })
    plan = Artifact(product_id=company.id, kind="editorial_plan", title="Контент-план",
                    content=json.dumps(plan_data, ensure_ascii=False))
    db.add(plan)
    db.commit()
    client = client_for(db)
    try:
        route = f"/studio/products/{company.id}/calendar"
        page = client.get(route)
        assert page.status_code == 200
        assert "/studio/assets/ui-sdk-pilot.bundle.js?v=4" in page.text
        assert "/studio/assets/calendar-ui-sdk.css?v=1" in page.text
        assert 'class="sg-calendar-move"' in page.text
        assert 'name="expected_publish_at" value="2026-10-06T12:00:00+03:00"' in page.text
        assert f'/plans/{plan.id}/slots/slot-1/move' in page.text
        assert "Europe/Moscow" in page.text
        old = client.get(route + "?ui_sdk=0")
        assert old.status_code == 200
        assert "sg-calendar-move" in old.text
        assert 'ui-sdk-pilot.bundle.js?v=4' not in old.text
        assert "calendar-ui-sdk.css?v=1" not in old.text
        css = client.get("/studio/assets/calendar-ui-sdk.css")
        assert css.status_code == 200 and ".sg-calendar-sdk-move" in css.text

        # The route rejects stale client state before changing a slot.
        response = client.post(
            f"/studio/products/{company.id}/plans/{plan.id}/slots/slot-1/move",
            data={"when": "2026-10-07T13:00",
                  "expected_publish_at": "2026-10-06T11:00:00+03:00"},
            follow_redirects=False,
        )
        assert response.status_code == 409
        db.refresh(plan)
        assert json.loads(plan.content)["slots"][0]["publish_at"] == "2026-10-06T12:00:00+03:00"
    finally:
        app.dependency_overrides.clear()


def test_unified_review_queue_filters_all_reviewable_types_and_scopes_company(studio_db):
    own = Product(name="Review company", slug="review-queue-own")
    foreign = Product(name="Foreign", slug="review-queue-foreign")
    studio_db.add_all([own, foreign])
    studio_db.flush()
    kinds = [
        ("channel_text", "Пост компании", {"channel": "telegram", "body": "Текст поста"}),
        ("article", "SEO-статья компании", {"seo_brief": {"primary_query": "пример"}, "full_article": "Текст статьи"}),
        ("creative_brief", "Креатив компании", {"preview_text": "Описание изображения"}),
        ("video_script", "Сценарий компании", {"preview_text": "Сценарий короткого видео"}),
        ("video_render", "Готовое видео компании", {"preview_text": "Сценарий и монтаж"}),
    ]
    for kind, title, data in kinds:
        studio_db.add(Artifact(
            product_id=own.id, kind=kind, title=title,
            status=ArtifactStatus.AWAITING_APPROVAL,
            content=json.dumps(data, ensure_ascii=False),
        ))
    studio_db.add_all([
        Artifact(product_id=foreign.id, kind="channel_text",
                 title="ЧУЖОЙ СЕКРЕТ", content='{"body":"Другой аккаунт"}',
                 status=ArtifactStatus.AWAITING_APPROVAL),
        Artifact(product_id=own.id, kind="channel_text",
                 title="Уже утверждённый пост", content='{"body":"Не в очереди"}',
                 status=ArtifactStatus.APPROVED),
    ])
    studio_db.commit()
    client = client_for(studio_db)
    base = f"/studio/products/{own.id}"
    try:
        all_reviews = client.get(base + "/review")
        assert all_reviews.status_code == 200
        assert "Очередь согласования" in all_reviews.text
        assert "ЕДИНЫЙ ЦЕНТР СОГЛАСОВАНИЯ" in all_reviews.text
        assert "5 на проверке" in all_reviews.text
        assert "ЧУЖОЙ СЕКРЕТ" not in all_reviews.text
        assert "Уже утверждённый пост" not in all_reviews.text
        assert all(title in all_reviews.text for _, title, _ in kinds)
        assert all_reviews.text.count('data-sg-resource-widget="material"') == 5
        assert 'src="/studio/assets/resource-widgets.js?v=2"' in all_reviews.text
        assert 'href="/studio/assets/resource-widgets.css?v=6"' in all_reviews.text
        assert 'href="' + base + '/review?kind=posts"' in all_reviews.text
        # A truncated preview never contains a direct unsafe approval form.
        assert f'action="{base}/review/' not in all_reviews.text
        assert 'Утвердить можно только после проверки полной версии' in all_reviews.text

        for category, visible, hidden in (
            ("posts", "Пост компании", "SEO-статья компании"),
            ("articles", "SEO-статья компании", "Пост компании"),
            ("creative", "Креатив компании", "Пост компании"),
            ("video", "Сценарий компании", "SEO-статья компании"),
            ("text", "Пост компании", "Креатив компании"),
        ):
            response = client.get(base + "/review?kind=" + category)
            assert response.status_code == 200
            assert visible in response.text
            assert hidden not in response.text

        unknown = client.get(base + "/review?kind=unexpected")
        assert unknown.status_code == 200 and "5 на проверке" in unknown.text
        sidebar = all_reviews.text.split('<aside class="sidebar">', 1)[1].split('</aside>', 1)[0]
        assert 'href="' + base + '/review"' in sidebar.split('<details class="sg-side-extras">', 1)[0]
        assert 'aria-current="page"' in sidebar
    finally:
        app.dependency_overrides.clear()


def test_unified_review_queue_paginates_without_losing_cards_or_leaking_other_tenant(studio_db):
    own = Product(name="Paged review", slug="paged-review-own")
    foreign = Product(name="Other review", slug="paged-review-foreign")
    studio_db.add_all([own, foreign])
    studio_db.flush()
    own_rows = [Artifact(
        product_id=own.id, kind="channel_text",
        title=f"Пост согласования {index:02d}",
        content=json.dumps({"body": f"Текст {index}"}),
        status=ArtifactStatus.AWAITING_APPROVAL,
    ) for index in range(37)]
    studio_db.add_all(own_rows + [Artifact(
        product_id=foreign.id, kind="channel_text", title="Пост другой компании",
        content='{"body":"Секрет"}', status=ArtifactStatus.AWAITING_APPROVAL,
    )])
    studio_db.commit()
    client = client_for(studio_db)
    base = f"/studio/products/{own.id}"
    try:
        first = client.get(base + "/review?kind=posts")
        second = client.get(base + "/review?kind=posts&page=2")
        high = client.get(base + "/review?kind=posts&page=999999")
        low = client.get(base + "/review?kind=posts&page=-50")
        assert all(r.status_code == 200 for r in (first, second, high, low))
        assert "37 на проверке" in first.text
        assert "В категории: 37. Показано: 30." in first.text
        assert "В категории: 37. Показано: 7." in second.text
        assert first.text.count('data-sg-resource-widget="material"') == 30
        assert second.text.count('data-sg-resource-widget="material"') == 7
        assert 'href="' + base + '/review?kind=posts&page=2"' in first.text
        assert 'href="' + base + '/review?kind=posts&page=1"' in second.text
        assert 'aria-label="Страницы согласования"' in second.text
        assert high.text.count('data-sg-resource-widget="material"') == 7
        assert low.text.count('data-sg-resource-widget="material"') == 30
        assert "Пост другой компании" not in first.text + second.text
        # No post is approved simply by browsing or filtering the queue.
        assert all(row.status == ArtifactStatus.AWAITING_APPROVAL for row in own_rows)
    finally:
        app.dependency_overrides.clear()


def test_unified_review_escapes_markup_and_fact_checks_require_detail(studio_db):
    own = Product(name="Safe review", slug="safe-review-own")
    studio_db.add(own)
    studio_db.flush()
    malicious = Artifact(product_id=own.id, kind="article",
                         title='<img src=x onerror="alert(1)">',
                         content=json.dumps({
                             "full_article": '<script>alert("bad")</script>',
                             "fact_check_required": True,
                             "quality_warnings": ["Проверить источник"],
                         }), status=ArtifactStatus.AWAITING_APPROVAL)
    malformed = Artifact(product_id=own.id, kind="creative_brief",
                         title="Некорректный ответ", content='["unexpected", "list"]',
                         status=ArtifactStatus.AWAITING_APPROVAL)
    studio_db.add_all([malicious, malformed])
    studio_db.commit()
    client = client_for(studio_db)
    base = f"/studio/products/{own.id}"
    try:
        response = client.get(base + "/review")
        assert response.status_code == 200
        assert '<img src=x onerror="alert(1)">' not in response.text
        assert '<script>alert("bad")</script>' not in response.text
        assert "&lt;img" in response.text
        assert "Проверьте факты" in response.text
        assert "Некорректный ответ" in response.text
        # No shortcut around the existing owner fact-check safeguard.
        post = client.post(base + f"/review/{malicious.id}/approve",
                           data={"facts_confirmed": "no"}, follow_redirects=False)
        assert post.status_code == 422
        studio_db.refresh(malicious)
        assert malicious.status == ArtifactStatus.AWAITING_APPROVAL
    finally:
        app.dependency_overrides.clear()
