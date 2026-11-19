"""Evidence-based SEO diagnostics: read-only, company-scoped and auditable."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.diagnostics import audit_seo
from app.content.review_dashboard import seo_progress


@pytest.fixture
def diagnostic_env():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        own = Product(name="SEO аудит", slug="seo-diagnostics-own")
        other = Product(name="Чужая компания", slug="seo-diagnostics-other")
        db.add_all([own, other])
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        with TestClient(app) as client:
            yield db, client, own, other
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def job(db, product, label, status, *, due_at=None, updated_at=None):
    task = Task(product_id=product.id, title="SEO-ARTICLE:" + label,
                assigned_agent="content_factory", status=status, due_at=due_at)
    db.add(task)
    db.flush()
    if updated_at is not None:
        task.updated_at = updated_at
    return task


def article(db, product, task, title, status):
    row = Artifact(product_id=product.id, task_id=task.id, kind="article", title=title,
                   content='{}', status=status)
    db.add(row)
    db.flush()
    return row


def receipt(db, product, row, status="published"):
    pub = Publication(product_id=product.id, artifact_id=row.id,
                      channel="own_site", status=status)
    db.add(pub)
    db.flush()
    return pub


def test_audit_classifies_real_overdue_and_publication_mismatches(diagnostic_env):
    db, client, own, other = diagnostic_env
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=4)
    future = now + timedelta(days=2)

    good_task = job(db, own, "good", TaskStatus.COMPLETED)
    good = article(db, own, good_task, "Проверенная статья", ArtifactStatus.PUBLISHED)
    receipt(db, own, good)

    no_receipt = job(db, own, "no-receipt", TaskStatus.COMPLETED)
    article(db, own, no_receipt, "Публикация без квитанции", ArtifactStatus.PUBLISHED)

    cross_receipt = job(db, own, "foreign-receipt", TaskStatus.COMPLETED)
    cross_article = article(db, own, cross_receipt, "Неправильный владелец квитанции",
                            ArtifactStatus.PUBLISHED)
    receipt(db, other, cross_article)

    wrong_artifact = job(db, own, "approval-conflict", TaskStatus.COMPLETED)
    inconsistent = article(db, own, wrong_artifact, "В базе ещё не опубликована",
                           ArtifactStatus.APPROVED)
    receipt(db, own, inconsistent)

    conflicting = job(db, own, "withdrawn", TaskStatus.COMPLETED)
    conflict_article = article(db, own, conflicting, "Противоречащие квитанции",
                               ArtifactStatus.PUBLISHED)
    receipt(db, own, conflict_article, "published")
    receipt(db, own, conflict_article, "unpublished")

    failed = job(db, own, "failed", TaskStatus.FAILED)
    running = job(db, own, "long-running", TaskStatus.RUNNING, updated_at=old)
    waiting_start = job(db, own, "late-start", TaskStatus.QUEUED, due_at=old, updated_at=old)
    no_article = job(db, own, "missing", TaskStatus.COMPLETED, updated_at=old)
    draft = job(db, own, "draft", TaskStatus.COMPLETED, due_at=old)
    article(db, own, draft, "Черновик после срока", ArtifactStatus.DRAFT)
    late_approved = job(db, own, "approved", TaskStatus.COMPLETED, due_at=old)
    article(db, own, late_approved, "Согласована без выкладки", ArtifactStatus.APPROVED)

    waiting_owner = job(db, own, "owner", TaskStatus.COMPLETED, due_at=old)
    article(db, own, waiting_owner, "Владелец ещё не согласовал", ArtifactStatus.AWAITING_APPROVAL)
    future_task = job(db, own, "future", TaskStatus.QUEUED, due_at=future)
    recent = job(db, own, "recent", TaskStatus.COMPLETED)
    foreign = job(db, other, "foreign-secret", TaskStatus.FAILED)
    db.commit()
    # SQLAlchemy can update updated_at during the commit; restore the exact
    # synthetic source clock to prove this audit's threshold classification.
    for task in (running, waiting_start, no_article):
        db.query(Task).filter(Task.id == task.id).update(
            {Task.updated_at: old}, synchronize_session=False)
    db.commit()

    audit = audit_seo(db, own.id, now=now)
    statuses = {entry["task"].id: entry for entry in audit.rows}
    assert len(audit.rows) == 14
    assert statuses[good_task.id]["published"] is True
    assert statuses[good_task.id]["issue_code"] is None
    for task in (no_receipt, cross_receipt, wrong_artifact, conflicting):
        assert statuses[task.id]["issue_code"] == "publication_mismatch"
        assert statuses[task.id]["published"] is False
    assert statuses[failed.id]["issue_code"] == "generation_failed"
    assert statuses[running.id]["issue_code"] == "running_long"
    assert statuses[waiting_start.id]["issue_code"] == "start_overdue"
    assert statuses[no_article.id]["issue_code"] == "missing_article"
    assert statuses[draft.id]["issue_code"] == "draft_overdue"
    assert statuses[late_approved.id]["issue_code"] == "approved_overdue"
    for task in (waiting_owner, future_task, recent):
        assert statuses[task.id]["issue_code"] is None
    assert statuses[waiting_owner.id]["status"] == "Ждёт решения владельца"
    assert foreign.id not in statuses
    assert len(audit.issues) == 10
    assert audit.counts["publication_mismatch"] == 4
    assert audit.issues[0].code == "publication_mismatch"
    assert list(db.scalars(select(Publication).where(
        Publication.product_id == own.id, Publication.status == "unpublished",
    )))  # read-only audit does not overwrite an explicit withdrawal


def test_seo_issue_filter_pagination_and_owner_daily_summary(diagnostic_env):
    db, client, own, other = diagnostic_env
    now = datetime.now(timezone.utc)
    for i in range(36):
        job(db, own, f"failed-{i}", TaskStatus.FAILED)
    safe = job(db, own, "future", TaskStatus.QUEUED, due_at=now + timedelta(days=3))
    foreign = job(db, other, "SECRET COMPANY", TaskStatus.FAILED)
    db.commit()

    base = f"/studio/products/{own.id}"
    page1 = client.get(base + "/review?section=seo&view=issues")
    page2 = client.get(base + "/review?section=seo&view=issues&page=2")
    assert page1.status_code == 200 and page2.status_code == 200
    assert "Проблемных заданий: 36" in page1.text
    assert page1.text.count('data-sg-resource-widget="material"') == 30
    assert page2.text.count('data-sg-resource-widget="material"') == 6
    assert "SECRET COMPANY" not in page1.text + page2.text
    assert f'{base}/review?section=seo&view=issues&page=2' in page1.text
    assert "Начало подготовки просрочено" not in page1.text
    assert f'{base}/review?section=seo&view=issues' in page1.text
    assert "Это не внешняя проверка доступности страницы" in page1.text

    all_view = seo_progress(db, own.id)
    issues_view = seo_progress(db, own.id, issues_only=True)
    assert all_view.total == 37
    assert all_view.issue_total == 36
    assert issues_view.total == 36
    assert any(
        entry["task"].id == safe.id and entry["status"] == "В очереди"
        for entry in all_view.records
    )  # The future scheduled item remains normal, not an issue.

    summary = client.get(base + "/home-summary.json")
    assert summary.status_code == 200
    state = summary.json()
    assert state["seo_issues"] == 36
    assert state["seo_issues_scanned"] == 37
    assert state["attention"][0]["url"] == base + "/review?section=seo&view=issues"
    assert state["attention"][0]["kind"] == "error"
    assert all(str(other.id) not in item["url"] for item in state["attention"])
    # Querying review/statuses never queues a retry or changes the publication.
    assert list(db.scalars(select(Publication))) == []


def test_seo_audit_does_not_flag_future_publications_or_pending_owner_review(diagnostic_env):
    db, client, own, _ = diagnostic_env
    future = datetime.now(timezone.utc) + timedelta(days=2)
    draft = job(db, own, "draft-future", TaskStatus.COMPLETED, due_at=future)
    article(db, own, draft, "Черновик к будущей дате", ArtifactStatus.DRAFT)
    accepted = job(db, own, "approved-future", TaskStatus.COMPLETED, due_at=future)
    article(db, own, accepted, "Согласована к будущей дате", ArtifactStatus.APPROVED)
    pending = job(db, own, "owner-review", TaskStatus.COMPLETED)
    article(db, own, pending, "Ожидает владельца", ArtifactStatus.AWAITING_APPROVAL)
    db.commit()
    audit = audit_seo(db, own.id)
    assert not audit.issues
    assert all(not row["issue_code"] for row in audit.rows)
    page = client.get(f"/studio/products/{own.id}/review?section=seo&view=issues")
    assert page.status_code == 200
    assert "Проблемных заданий: 0" in page.text
    assert "Заданий с обнаруженными отклонениями нет" in page.text
    assert "Ожидает владельца" not in page.text
