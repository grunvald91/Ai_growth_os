"""Automatic schema recovery uses the original task and never grants owner bypass."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.seo.activation import set_enabled
from app.seo.article_retry import AUTO_RECOVERY, recover_failed_schema_once, automatic_recovery_reason
from app.workflows.article_contracts import ArticleProductionInput
from test_seo_article import BRIEF
from test_seo_factory import env  # noqa: F401


@pytest.mark.parametrize("case", ["schema", "grounding", "unknown_assembly", "provider", "article_exists", "disabled", "manual", "recent"])
def test_auto_recovery_is_bounded_and_preserves_schedule(env, case):
    factory, _, company = env
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    with factory() as db:
        plan = Artifact(product_id=company, kind="seo_content_plan", title="Plan", content='{"items":[]}')
        db.add(plan)
        db.flush()
        task = Task(product_id=company, title=f"SEO-ARTICLE:{plan.id}:{BRIEF.plan_item_id}",
                    assigned_agent="content_factory", status=TaskStatus.FAILED, due_at=old)
        db.add(task)
        db.flush()
        task_id = task.id
        db.add(Artifact(product_id=company, task_id=task_id, kind="content_request", title="Request",
            content=ArticleProductionInput(product_id=company, task_id=task_id, topic="Запись клиентов",
                channel="own_site", light=True, seo_brief=BRIEF).model_dump_json()))
        db.add(AgentRun(task_id=task_id, agent_name="article_writer", status="failed",
            started_at=old, finished_at=old, error=("provider_request_failed:article_writer"
                if case == "provider" else "assembly_failed:invalid_marketing_editor_grounding"
                if case == "grounding" else "assembly_failed:invalid_derivative_assembly"
                if case == "unknown_assembly" else "invalid_article_output:article_writer:sections.0.kind")))
        if case in {"article_exists", "manual"}:
            db.add(Artifact(product_id=company, task_id=task_id,
                kind="article" if case == "article_exists" else "seo_article_manual_retry",
                title="Existing", content="{}"))
        db.commit()
        if case != "recent":
            db.execute(update(Task).where(Task.id == task_id).values(updated_at=old))
            db.commit()
        if case == "disabled":
            set_enabled(db, company, enabled=False, actor="test")
        reason = automatic_recovery_reason(db, product_id=company, task_id=task_id)
        if case in {"schema", "grounding"}:
            assert reason == "eligible"
        else:
            assert reason != "eligible"
        assert db.get(Task, task_id).status == TaskStatus.FAILED
        assert not db.scalar(select(Artifact).where(
            Artifact.task_id == task_id, Artifact.kind == AUTO_RECOVERY))
        assert recover_failed_schema_once(db, enabled=False) == 0
        assert recover_failed_schema_once(db, enabled=True) == int(case in {"schema", "grounding"})
        task = db.get(Task, task_id)
        assert task.status == (TaskStatus.QUEUED if case in {"schema", "grounding"} else TaskStatus.FAILED)
        assert task.due_at.replace(tzinfo=timezone.utc) == old
        if case in {"schema", "grounding"}:
            assert db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == AUTO_RECOVERY))
            assert not db.scalar(select(Artifact).where(
                Artifact.task_id == task_id, Artifact.kind == "seo_article_manual_retry"))
            task.status = TaskStatus.FAILED
            db.commit()
            db.execute(update(Task).where(Task.id == task_id).values(updated_at=old))
            db.commit()
        assert recover_failed_schema_once(db, enabled=True) == 0
