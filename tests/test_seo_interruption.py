"""Interruption recovery never steals an active job or regenerates an existing article."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.seo.interruption import article_lease, record_lease, recover_interrupted_articles, RECOVERY
from test_seo_factory import env  # noqa: F401


@pytest.mark.parametrize("case", ["interrupted", "live", "existing", "legacy", "limit"])
def test_recovery_requires_released_durable_claim(env, tmp_path, case):
    factory, _, company = env
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=3)
    with factory() as db:
        task = Task(product_id=company, title="SEO-ARTICLE:plan:a001",
                    assigned_agent="content_factory", status=TaskStatus.RUNNING, due_at=old)
        db.add(task)
        db.flush()
        task_id = task.id
        if case != "legacy":
            record_lease(db, task)
        db.add_all([
            AgentRun(task_id=task_id, agent_name="content_strategist", status="completed",
                     started_at=old, finished_at=old),
            AgentRun(task_id=task_id, agent_name="article_writer", status="running", started_at=old),
            AgentRun(task_id=task_id, agent_name="marketing_editor", status="queued"),
            Artifact(product_id=company, task_id=task_id, kind="content_stage_output",
                     title="Checkpoint", content="{}"),
        ])
        if case == "existing":
            db.add(Artifact(product_id=company, task_id=task_id, kind="article",
                            title="Existing article", content="{}"))
        if case == "limit":
            for _ in range(2):
                db.add(Artifact(product_id=company, task_id=task_id, kind=RECOVERY,
                                title="Previous recovery", content="{}"))
        db.commit()
        db.execute(update(Task).where(Task.id == task_id).values(updated_at=old))
        db.commit()
    if case == "live":
        with article_lease(tmp_path, company, task_id):
            assert recover_interrupted_articles(factory, tmp_path, now=now) == 0
    else:
        assert recover_interrupted_articles(factory, tmp_path, now=now) == int(case != "legacy")
    with factory() as db:
        task = db.get(Task, task_id)
        assert task.status == (TaskStatus.RUNNING if case in {"live", "legacy"} else
            TaskStatus.QUEUED if case == "interrupted" else TaskStatus.FAILED)
        assert task.due_at.replace(tzinfo=timezone.utc) == old
        assert db.scalar(select(Artifact.id).where(Artifact.task_id == task_id,
                    Artifact.kind == "content_stage_output"))
        runs = {r.agent_name: r for r in db.scalars(select(AgentRun).where(AgentRun.task_id == task_id))}
        assert runs["content_strategist"].status == "completed"
        assert runs["article_writer"].status == ("running" if case in {"live", "legacy"} else "failed")
    if case == "interrupted":
        assert recover_interrupted_articles(factory, tmp_path, now=now) == 0
