"""Production failure reports must remain read-only and isolated."""
import json
from datetime import datetime, timezone

from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus
from app.seo.failure_report import failure_report, safe_error
from test_seo_factory import env  # noqa: F401


def test_allowlist_never_exposes_raw_provider_text():
    assert safe_error("upstream_failed:invalid_article_output:article_writer:private") == (
        "invalid_article_output", "article_writer")
    assert safe_error("provider_request_failed:customer-secret") == ("provider_request_failed", None)
    assert safe_error("secret:customer") == ("unclassified", None)
    assert safe_error(None) == ("not_recorded", None)


def test_failed_article_report_is_scoped_and_preserves_state(env):
    factory, _, company = env
    with factory() as db:
        other = Product(name="Other", slug="other-report")
        db.add(other)
        db.flush()
        task = Task(product_id=company, title="SEO-ARTICLE:plan:1",
                    assigned_agent="content_factory", status=TaskStatus.FAILED)
        other_task = Task(product_id=other.id, title="SEO-ARTICLE:plan:2",
                          assigned_agent="content_factory", status=TaskStatus.FAILED)
        ordinary = Task(product_id=company, title="Regular article",
                        assigned_agent="content_factory", status=TaskStatus.FAILED)
        db.add_all([task, other_task, ordinary])
        db.flush()
        now = datetime.now(timezone.utc)
        db.add(AgentRun(task_id=task.id, agent_name="article_writer", status="failed",
                        started_at=now, finished_at=now,
                        error="invalid_article_output:article_writer:private"))
        db.add(AgentRun(task_id=task.id, agent_name="marketing_editor", status="failed",
                        finished_at=now, error="upstream_failed:invalid_article_output:article_writer:private"))
        db.add(Artifact(product_id=company, task_id=task.id, kind="content_stage_output",
                        title="Checkpoint", content='{"private":"content"}'))
        db.commit()
        report = failure_report(db, company)
        assert report["failed_total"] == report["reported"] == 1
        row = report["tasks"][0]
        assert row["task_id"] == str(task.id)
        assert row["last_recorded_error_stage"] == "article_writer"
        assert row["last_recorded_error_family"] == "invalid_article_output"
        assert row["checkpoint_records"] == 1
        assert row["article_exists"] is False
        assert row["automatic_recovery_blocker"] == "invalid_plan_reference"
        assert "private" not in json.dumps(report)
        assert str(other_task.id) not in json.dumps(report)
        assert task.status == TaskStatus.FAILED
        assert not db.new and not db.dirty and not db.deleted


def test_report_without_agent_error_does_not_guess(env):
    factory, _, company = env
    with factory() as db:
        db.add(Task(product_id=company, title="SEO-ARTICLE:plan:1",
                    assigned_agent="content_factory", status=TaskStatus.FAILED))
        db.commit()
        row = failure_report(db, company)["tasks"][0]
        assert row["last_recorded_error_family"] == "not_recorded"
        assert row["last_recorded_error_stage"] is None


def test_planning_failure_report_allowlists_metadata(env):
    factory, _, company = env
    with factory() as db:
        task = Task(product_id=company, title="SEO plan", assigned_agent="seo_factory",
                    status=TaskStatus.FAILED)
        db.add(task)
        db.flush()
        db.add(Artifact(product_id=company, task_id=task.id, kind="seo_factory_report",
                        title="Report", content=json.dumps({
                            "stage": "build_plan", "error_code": "plan_validation_failed",
                            "plan_issue": "private value", "error": "private response",
                            "attempt": 2, "retryable": False})))
        db.commit()
        result = failure_report(db, company)["latest_planning_failure"]
        assert result == {"task_id": str(task.id), "stage": "build_plan",
                          "error_code": "plan_validation_failed", "plan_issue": "unclassified",
                          "attempt": 2, "retryable": False}
        assert "private" not in json.dumps(result)
        assert not db.new and not db.dirty and not db.deleted
