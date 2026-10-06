import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product, Task, TaskStatus


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
