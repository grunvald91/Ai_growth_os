"""LLM call speed statistics and duration warnings come from real agent runs."""
from datetime import datetime, timedelta, timezone

from app.content.durations import call_speed, duration_notice, task_durations
from app.models.domain import AgentRun, Product, Task, TaskStatus
from tests.test_content_studio_activity import owner_studio  # noqa: F401

NOW = datetime.now(timezone.utc)


def _task(db, product, agent, minutes, *, runs=("whiteboard_storyboard",), status=TaskStatus.COMPLETED,
          provider="xai", model="kimi-2.7", run_status="completed"):
    task = Task(product_id=product.id, title="t", assigned_agent=agent, status=status)
    db.add(task)
    db.flush()
    start = NOW - timedelta(hours=1)
    step = timedelta(minutes=minutes / len(runs))
    for index, name in enumerate(runs):
        db.add(AgentRun(task_id=task.id, agent_name=name, provider=provider, model=model, status=run_status,
                        started_at=start + step * index, finished_at=start + step * (index + 1)))
    db.commit()
    return task


def test_call_speed_groups_by_provider_and_model(db):
    product = Product(name="P", slug="speed")
    db.add(product)
    db.commit()
    for minutes in (1, 2, 4):
        _task(db, product, "content_factory", minutes, runs=("post_strategist",))
    _task(db, product, "content_factory", 10, runs=("post_strategist",), run_status="failed", model="other")
    rows = {row["model"]: row for row in call_speed(db, days=7)}
    assert rows["kimi-2.7"]["calls"] == 3
    assert rows["kimi-2.7"]["p50"] == 120
    assert rows["kimi-2.7"]["max"] == 240
    assert rows["other"]["failed"] == 1


def test_task_durations_and_notice_use_history(db):
    product = Product(name="P", slug="durations")
    db.add(product)
    db.commit()
    for minutes in (20, 30, 40, 90):
        _task(db, product, "whiteboard_renderer", minutes, runs=("whiteboard_storyboard", "whiteboard_image"))
    _task(db, product, "content_factory", 6, runs=("post_strategist", "post_writer_editor"))
    _task(db, product, "whiteboard_renderer", 500, status=TaskStatus.FAILED)
    stats = task_durations(db)
    assert stats["whiteboard"]["count"] == 4
    assert stats["post"]["count"] == 1
    assert "package" not in stats
    notice = duration_notice(stats, "whiteboard")
    assert "обычно готово за 35 мин" in notice and "до 1 ч 30 мин" in notice
    assert "истории пока мало" in duration_notice(stats, "post")


def test_speed_page_and_forms_show_estimates(owner_studio):  # noqa: F811
    client, db, company, _ = owner_studio
    _task(db, company, "content_factory", 45, runs=("post_strategist",))
    page = client.get(f"/studio/products/{company.id}/speed")
    assert page.status_code == 200
    assert "kimi-2.7" in page.text and "p90" in page.text
    assert "Сколько ждать результат" in client.get(f"/studio/products/{company.id}/create").text
    assert "Рисованное видео: обычно" in client.get(f"/studio/products/{company.id}/whiteboard").text
