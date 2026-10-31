"""SEO status comes only from persisted company-scoped tasks and artifacts."""
import json
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.growth_live import seo_run_card, seo_status
from app.api.seo_run_view import seo_run_summary, seo_runs
from app.db.models import Base
from app.models.domain import Artifact, Product, Task, TaskStatus


def test_no_artifacts_never_implies_finished_stages_or_zero_provider_cost():
    task = Task(id=uuid4(), product_id=uuid4(), title="SEO-завод",
                status=TaskStatus.QUEUED, assigned_agent="seo_factory")
    row = seo_run_summary(task, [])
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["semantic_ready"] is False
    assert row["plan_ready"] is False
    assert row["articles_queued"] is None
    assert row["cost_amount"] is None
    assert row["cost_status"] == "not_recorded"


def test_run_summary_uses_actual_report_counts_and_escapes_error_markup():
    pid = uuid4()
    tid = uuid4()
    task = Task(id=tid, product_id=pid, title="<script>alert(1)</script>",
                status=TaskStatus.FAILED, assigned_agent="seo_factory")
    artifacts = [
        Artifact(product_id=pid, task_id=tid, kind="semantic_core",
                 title="Семантика", content="{}"),
        Artifact(product_id=pid, task_id=tid, kind="seo_factory_attempt",
                 title="Попытка", content="{}"),
        Artifact(product_id=pid, task_id=tid, kind="seo_factory_report",
                 title="Отчёт", content=json.dumps({
                     "articles_queued": 3, "sources": [{"name": "search"}],
                     "error": "<img src=x onerror=alert(1)>",
                 })),
    ]
    row = seo_run_summary(task, artifacts)
    assert row["semantic_ready"] is True
    assert row["plan_ready"] is False
    assert row["articles_queued"] == 3
    assert row["sources_confirmed"] == 1
    assert row["attempts"] == 1
    page = seo_run_card(row)
    assert "Статей поставлено в очередь: <b>3</b>" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img src=x onerror=alert(1)>" not in page


def test_database_seo_status_is_scoped_to_current_company_and_read_only():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        one = Product(name="One", slug="one-seo")
        two = Product(name="Two", slug="two-seo")
        db.add_all([one, two])
        db.commit()
        a = Task(product_id=one.id, assigned_agent="seo_factory",
                 title="SEO company one", status=TaskStatus.RUNNING)
        b = Task(product_id=two.id, assigned_agent="seo_factory",
                 title="SEO company two", status=TaskStatus.QUEUED)
        unrelated = Task(product_id=one.id, assigned_agent="another_agent",
                         title="Not factory", status=TaskStatus.QUEUED)
        db.add_all([a, b, unrelated])
        db.commit()
        db.add(Artifact(product_id=one.id, task_id=a.id, kind="seo_content_plan",
                        title="SEO-план", content="{}"))
        db.commit()
        count_before = len(list(db.query(Task).all()))
        report = seo_runs(db, one.id)
        assert len(report) == 1
        assert report[0]["id"] == str(a.id)
        assert report[0]["plan_ready"] is True
        assert report[0]["status"] == "running"
        result = seo_status(one.id, db)
        payload = json.loads(result.body)
        assert result.headers["Cache-Control"] == "private, no-store"
        assert len(payload["runs"]) == 1
        assert len(list(db.query(Task).all())) == count_before
    engine.dispose()
