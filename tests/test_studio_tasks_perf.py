"""Safety and repeatability checks for read-only production Tasks timing."""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.ops.studio_tasks_perf import _percentile, probe


@pytest.fixture
def probe_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_task_probe_reports_only_aggregate_timings_without_writing_or_leaking(probe_engine):
    with Session(probe_engine) as db:
        company = Product(name="PRIVATE BUSINESS SECRET", slug="private-business")
        another = Product(name="Other company", slug="other-private-business")
        db.add_all((company, another))
        db.flush()
        for i in range(32):
            task = Task(product_id=company.id, title=f"SECRET TASK {i}",
                        assigned_agent="content_factory",
                        status=TaskStatus.COMPLETED if i % 2 else TaskStatus.RUNNING)
            db.add(task)
            db.flush()
            db.add(Artifact(product_id=company.id, task_id=task.id,
                            kind="channel_text", title="PRIVATE MATERIAL",
                            content=json.dumps({"body": "PRIVATE CONTENT " * 500})))
        db.add(Task(product_id=another.id, title="OTHER SECRET", assigned_agent="content_factory"))
        db.commit()
        owner_id = company.id
        before = int(db.query(Task).count())
        artifact_before = int(db.query(Artifact).count())

    with probe_engine.connect() as conn:
        report = probe(conn, samples=2)
    assert report["schema"] == 1
    assert report["status"] == "ok" and report["read_only"] is True
    assert set(report["scenarios"]) == {"all", "approval", "search_no_match"}
    for scenario in report["scenarios"].values():
        assert scenario["samples"] == 2
        assert scenario["total_p50_ms"] >= 0
        assert scenario["total_p95_ms"] >= scenario["total_p50_ms"]
        assert scenario["sql_calls_max"] > 0
        assert scenario["html_bytes_max"] > 0
    public = json.dumps(report)
    for forbidden in ("PRIVATE", "SECRET", str(owner_id), "SELECT", "password", "api_key"):
        assert forbidden not in public
    with Session(probe_engine) as db:
        assert db.query(Task).count() == before
        assert db.query(Artifact).count() == artifact_before


def test_task_probe_is_empty_safe_and_has_bounded_samples(probe_engine):
    with probe_engine.connect() as conn:
        assert probe(conn) == {"schema": 1, "status": "no_tasks", "read_only": True}
        for invalid in (0, 8, -1, "5"):
            with pytest.raises(ValueError, match="invalid_sample_count"):
                probe(conn, samples=invalid)


def test_task_probe_nearest_rank_is_valid_for_small_samples():
    assert _percentile([4.0, 1.0, 10.0], 0.50) == 4.0
    assert _percentile([4.0, 1.0, 10.0], 0.95) == 10.0
    assert _percentile([], 0.95) == 0.0
