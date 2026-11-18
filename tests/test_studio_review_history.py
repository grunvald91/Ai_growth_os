"""Review history must be factual, tenant-scoped, read-only, and idempotent."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.content.review_dashboard import history, seo_progress
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import (
    Approval, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus,
)


@pytest.fixture
def review_env():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        own = Product(name="Компания тестового согласования", slug="history-own")
        other = Product(name="Соседняя компания", slug="history-other")
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


def material(db, product, *, title="Новый пост", status=ArtifactStatus.AWAITING_APPROVAL):
    row = Artifact(
        product_id=product.id, kind="channel_text", title=title,
        status=status, content=json.dumps({"channel": "vk", "body": "Полный проверяемый текст"}),
    )
    db.add(row)
    db.commit()
    return row


def approval(db, product, row, *, action="studio:approve_material",
             status="approved", source="studio", decided=True):
    obj = Approval(
        product_id=product.id, artifact_id=row.id,
        action_type=action, risk_level="YELLOW", status=status,
        requested_by=source, decided_by="owner:confirmed" if decided else None,
        decided_at=datetime.now(timezone.utc) if decided else None,
    )
    db.add(obj)
    db.commit()
    return obj


def test_browser_approval_writes_exactly_one_atomic_audit_event(review_env):
    db, client, own, other = review_env
    row = material(db, own)
    foreign = material(db, other, title="Чужой пост")
    base = f"/studio/products/{own.id}"
    denied = client.post(
        base + f"/review/{foreign.id}/approve", follow_redirects=False,
    )
    assert denied.status_code == 404
    assert list(db.scalars(select(Approval))) == []

    response = client.post(base + f"/review/{row.id}/approve",
                           follow_redirects=False)
    assert response.status_code == 303, response.text
    db.refresh(row)
    assert row.status == ArtifactStatus.APPROVED
    audit = list(db.scalars(select(Approval)))
    assert len(audit) == 1
    assert audit[0].product_id == own.id
    assert audit[0].artifact_id == row.id
    assert audit[0].action_type == "studio:approve_material"
    assert audit[0].status == "approved"
    assert audit[0].decided_at is not None

    repeated = client.post(base + f"/review/{row.id}/approve",
                           follow_redirects=False)
    assert repeated.status_code == 303
    assert len(list(db.scalars(select(Approval)))) == 1
    assert row.version == 2
    h = client.get(base + "/review?section=history")
    assert h.status_code == 200
    assert "История решений" in h.text
    assert "Новый пост" in h.text
    assert "Чужой пост" not in h.text
    assert 'data-sg-resource-widget="material"' in h.text
    assert 'action="' + base + '/review/' not in h.text
    assert h.headers["Cache-Control"] == "private, no-store"


def test_fact_check_refusal_does_not_write_false_audit(review_env):
    db, client, own, _ = review_env
    row = material(db, own)
    row.content = json.dumps({"body": "Заявлен доход 1 миллион", "fact_check_required": True})
    db.commit()
    base = f"/studio/products/{own.id}"
    refused = client.post(base + f"/review/{row.id}/approve",
                          data={"facts_confirmed": "no"}, follow_redirects=False)
    assert refused.status_code == 422
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL
    assert list(db.scalars(select(Approval))) == []
    allowed = client.post(base + f"/review/{row.id}/approve",
                          data={"facts_confirmed": "yes"}, follow_redirects=False)
    assert allowed.status_code == 303
    assert len(list(db.scalars(select(Approval)))) == 1


def test_history_uses_only_decided_events_from_own_product_and_review_types(review_env):
    db, client, own, other = review_env
    own_row = material(db, own, title='<img src=x onerror="alert(1)">')
    foreign_row = material(db, other, title="FOREIGN SECRET")
    approval(db, own, own_row, source="operator")
    approval(db, other, foreign_row)
    approval(db, own, own_row, action="publish", source="seo_factory", decided=False,
             status="pending")
    approval(db, own, own_row, action="dispatcher:schedule_post", source="operator")
    approval(db, own, foreign_row)  # Tampered company-artifact association.
    records = history(db, own.id)
    assert records.total == 1
    assert records.records[0][1].id == own_row.id
    page = client.get(f"/studio/products/{own.id}/review?section=history")
    assert page.status_code == 200
    assert "FOREIGN SECRET" not in page.text
    assert '<img src=x onerror="alert(1)">' not in page.text
    assert "&lt;img" in page.text


def test_history_paginates_and_shows_only_recorded_seo_release_decisions(review_env):
    db, client, own, _ = review_env
    rows = []
    for index in range(34):
        row = material(db, own, title=f"История решения {index:02d}")
        approval(db, own, row)
        rows.append(row)
    seo = Artifact(
        product_id=own.id, kind="article", title="SEO выпуск подтверждён",
        content='{}', status=ArtifactStatus.PUBLISHED,
    )
    db.add(seo)
    db.commit()
    approval(db, own, seo, action="publish", source="seo_factory")
    result = history(db, own.id, page=2)
    assert result.total == 35 and result.page_count == 2
    assert len(result.records) == 5
    base = f"/studio/products/{own.id}/review"
    first = client.get(base + "?section=history")
    second = client.get(base + "?section=history&page=2")
    assert first.text.count('data-sg-resource-widget="material"') == 30
    assert second.text.count('data-sg-resource-widget="material"') == 5
    assert "SEO: публикация подтверждена" in first.text
    assert 'href="' + base + '?section=history&page=2"' in first.text


def test_seo_progress_must_verify_artifact_and_publication_for_current_company(review_env):
    db, client, own, other = review_env
    def job(product, label, status):
        t = Task(product_id=product.id, title="SEO-ARTICLE:" + label,
                 assigned_agent="content_factory", status=status)
        db.add(t)
        db.flush()
        return t
    queued = job(own, "queued", TaskStatus.QUEUED)
    working = job(own, "working", TaskStatus.RUNNING)
    failed = job(own, "failed", TaskStatus.FAILED)
    held = job(own, "held", TaskStatus.COMPLETED)
    approved = job(own, "approved", TaskStatus.COMPLETED)
    published = job(own, "published", TaskStatus.COMPLETED)
    wrong = job(own, "inconsistent", TaskStatus.COMPLETED)
    foreign = job(other, "foreign", TaskStatus.COMPLETED)
    regular = Task(product_id=own.id, title="CONTENT: обычная задача",
                   assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(regular)
    db.flush()
    for t, state, title in (
        (held, ArtifactStatus.AWAITING_APPROVAL, "SEO-на проверке"),
        (approved, ArtifactStatus.APPROVED, "SEO-утверждён"),
        (published, ArtifactStatus.PUBLISHED, "SEO-в блоге"),
        (wrong, ArtifactStatus.PUBLISHED, "SEO-без подтверждения"),
        (foreign, ArtifactStatus.PUBLISHED, "FOREIGN-SEO-SECRET"),
    ):
        art = Artifact(product_id=t.product_id, task_id=t.id, kind="article", title=title,
                       content='{}', status=state)
        db.add(art)
        db.flush()
        if t.id == published.id:
            db.add(Publication(product_id=own.id, artifact_id=art.id,
                               channel="own_site", status="published"))
        elif t.id == foreign.id:
            db.add(Publication(product_id=other.id, artifact_id=art.id,
                               channel="own_site", status="published"))
    db.add(Artifact(product_id=own.id, task_id=held.id, kind="seo_final_review",
                    title="Запись ИИ-проверки", content='{}',
                    status=ArtifactStatus.DRAFT))
    db.commit()
    result = seo_progress(db, own.id)
    assert result.total == 7
    statuses = {entry["task"].id: entry for entry in result.records}
    assert statuses[queued.id]["status"] == "В очереди"
    assert statuses[working.id]["status"] == "Создаётся"
    assert statuses[failed.id]["status"] == "Ошибка подготовки"
    assert statuses[held.id]["status"] == "Ждёт решения владельца"
    assert statuses[approved.id]["status"] == "Согласовано, не опубликовано"
    assert statuses[published.id]["status"] == "Опубликовано"
    assert statuses[wrong.id]["status"] == "Сведения о публикации расходятся"
    assert statuses[held.id]["critic_recorded"] is True
    assert statuses[held.id]["published"] is False
    assert statuses[published.id]["published"] is True
    html = client.get(f"/studio/products/{own.id}/review?section=seo")
    assert html.status_code == 200
    assert "Заданий: 7" in html.text
    assert "FOREIGN-SEO-SECRET" not in html.text
    assert "Статусы SEO-статей" in html.text
    assert "Публикация не подтверждена" in html.text
    assert "Есть запись проверки (не означает допуск)" in html.text


def test_seo_progress_pagination_is_read_only(review_env):
    db, client, own, other = review_env
    for index in range(33):
        db.add(Task(product_id=own.id, title=f"SEO-ARTICLE:task-{index}",
                    assigned_agent="content_factory", status=TaskStatus.QUEUED))
    db.add(Task(product_id=other.id, title="SEO-ARTICLE:OTHER-SECRET",
                assigned_agent="content_factory", status=TaskStatus.QUEUED))
    db.commit()
    base = f"/studio/products/{own.id}/review?section=seo"
    first = client.get(base)
    second = client.get(base + "&page=2")
    assert first.status_code == 200 and second.status_code == 200
    assert first.text.count('data-sg-resource-widget="material"') == 30
    assert second.text.count('data-sg-resource-widget="material"') == 3
    assert "Заданий: 33" in first.text
    assert "OTHER-SECRET" not in first.text + second.text
    assert len(list(db.scalars(select(Approval)))) == 0
    assert len(list(db.scalars(select(Publication)))) == 0
