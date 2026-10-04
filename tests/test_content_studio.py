import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product, Task, TaskStatus


def client_for(db):
    def override():
        yield db
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    return client


def make_plan(product_id, task_id=None):
    return {
        "generated_at": "2026-10-04T12:00:00+00:00",
        "approval_state": "draft",
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


def test_studio_lists_products_and_renders_calendar(db):
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.commit()
    db.add(Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(product.id),ensure_ascii=False)))
    db.commit()
    client=client_for(db)
    try:
        assert client.get("/studio").status_code==200
        response=client.get(f"/studio/products/{product.id}")
        assert response.status_code==200
        assert "Content Studio" in response.text
        assert "Instagram Reels" in response.text
        assert "Тема один" in response.text
        assert response.headers["cache-control"]=="no-store"
    finally:
        app.dependency_overrides.clear()


def test_studio_renders_storyboard_only_video_brief(db):
    product=Product(name="ServGo",slug="servgo")
    task=Task(product_id=product.id,title="CONTENT",assigned_agent="content_factory",status=TaskStatus.COMPLETED)
    db.add_all([product,task]);db.flush()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(product.id,task.id),ensure_ascii=False))
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
        assert "AI Growth OS" in response.text
    finally:
        app.dependency_overrides.clear()


def test_studio_can_queue_planned_package_without_publishing(db):
    product=Product(name="ServGo",slug="servgo")
    db.add(product);db.commit()
    plan=Artifact(product_id=product.id,kind="editorial_plan",title="План",content=json.dumps(make_plan(product.id),ensure_ascii=False))
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
