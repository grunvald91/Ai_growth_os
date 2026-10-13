"""Non-spending integration checks: the existing Animator job is visible in Studio."""
import json

from sqlalchemy import select
from app.api.content_studio_activity import work_items, notice_items, activity_html
from app.models.domain import Artifact, Product, Task, TaskStatus


def test_animator_queue_appears_in_common_worklist(db):
    company = Product(name="Белая доска", slug="animator-company-integration")
    db.add(company)
    db.flush()
    task = Task(
        product_id=company.id, title="WHITEBOARD-JOB: 123abc",
        description="Рисованное видео: зачем нужна запись",
        assigned_agent="whiteboard_renderer", status=TaskStatus.WAITING_APPROVAL,
    )
    db.add(task)
    db.flush()
    db.add(Artifact(
        product_id=company.id, task_id=task.id, kind="whiteboard_job_state",
        title="Ход генерации", content=json.dumps({
            "stage": "waiting_approval", "calls": 0, "error": None,
        }),
    ))
    db.commit()

    items = work_items(db, company.id)
    assert items[0]["agent"] == "whiteboard_renderer"
    assert items[0]["state"] == "waiting_approval"
    assert items[0]["animator_stage"] == "waiting_approval"
    assert items[0]["animator_calls"] == 0
    rendered = activity_html(company.id, items, tab="approval")
    assert f"/studio/products/{company.id}/whiteboard/{task.id}" in rendered
    notes = notice_items(db, company.id)
    assert any(note["url"].endswith(f"/whiteboard/{task.id}") for note in notes)


def test_completed_animator_notifies_and_links_to_existing_player(db):
    company = Product(name="Видео компания", slug="animator-completed-integration")
    db.add(company)
    db.flush()
    task = Task(product_id=company.id, assigned_agent="whiteboard_renderer",
                title="WHITEBOARD-JOB: abc123", description="Рисованное видео: польза кофе",
                status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    video = Artifact(product_id=company.id, task_id=task.id,
                     kind="whiteboard_video", title="Рисованное видео: польза кофе",
                     status="awaiting_approval",
                     content=json.dumps({"task_id": str(task.id), "duration_seconds": 30}))
    db.add(video)
    db.commit()
    items = work_items(db, company.id)
    assert any(row["kind"] == "whiteboard_video" for row in items[0]["artifacts"])
    href = f"/studio/products/{company.id}/whiteboard/{task.id}"
    assert href in activity_html(company.id, items, tab="ready")
    assert any(note["url"] == href for note in notice_items(db, company.id))


def test_animator_job_visibility_is_scoped_by_company(db):
    left = Product(name="Первый бизнес", slug="animator-scope-left")
    right = Product(name="Второй бизнес", slug="animator-scope-right")
    db.add_all([left, right])
    db.flush()
    db.add(Task(product_id=left.id, title="WHITEBOARD-JOB: x",
                assigned_agent="whiteboard_renderer", status=TaskStatus.RUNNING))
    db.commit()
    assert any(row["agent"] == "whiteboard_renderer" for row in work_items(db, left.id))
    assert not work_items(db, right.id)
    assert not notice_items(db, right.id)
