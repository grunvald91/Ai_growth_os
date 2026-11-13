"""Draft plan package edits must be atomic, tenant-safe and never rewrite publications."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.content.plan_edit import update_draft_package
from app.models.domain import Artifact, Product
from test_content_calendar import draft


def make_company_plan(db, *, state="draft", started=False):
    company = Product(name="ProResultat", slug="proresultat")
    db.add(company)
    db.flush()
    plan = draft()
    plan["approval_state"] = state
    plan["generated_at"] = datetime.now(timezone.utc).isoformat()
    plan["starts_at"] = datetime.now(timezone.utc).isoformat()
    for i, slot in enumerate(plan["slots"]):
        when = datetime.now(timezone.utc) + timedelta(days=3+i)
        slot["slot_id"] = str(i+1)
        slot["publish_at"] = when.isoformat()
        slot["prepare_at"] = (when-timedelta(hours=24)).isoformat()
        slot["state"] = "queued" if started else "planned"
        slot["task_id"] = "123" if started else None
    row = Artifact(product_id=company.id, kind="editorial_plan", title="Контент-план",
                   content=json.dumps(plan, ensure_ascii=False))
    db.add(row)
    db.commit()
    return company, row


def test_edits_all_platforms_of_one_package_and_preserves_schedule(db):
    company, row = make_company_plan(db)
    before = json.loads(row.content)
    assert update_draft_package(db, product_id=company.id, plan_id=row.id, package_key="booking",
                                topic="Удобная запись на услуги", objective="Получить новые обращения",
                                audience="Владельцы салонов")
    new = json.loads(row.content)
    assert len(new["slots"]) == 2
    for old, slot in zip(before["slots"], new["slots"]):
        assert slot["topic"] == "Удобная запись на услуги"
        assert slot["audience"] == "Владельцы салонов"
        assert slot["objective"] == "Получить новые обращения"
        for preserved in ("publish_at", "prepare_at", "slot_id", "evidence_refs", "task_id", "state"):
            assert slot.get(preserved) == old.get(preserved)
    assert new["approval_state"] == "draft"
    assert not update_draft_package(db, product_id=company.id, plan_id=row.id, package_key="booking",
                                   topic="Удобная запись на услуги", objective="Получить новые обращения",
                                   audience="Владельцы салонов")


@pytest.mark.parametrize("state,started", [("active", False), ("draft", True)])
def test_rejects_edit_after_approval_or_work_started(db, state, started):
    company, row = make_company_plan(db, state=state, started=started)
    original = row.content
    with pytest.raises(ValueError):
        update_draft_package(db, product_id=company.id, plan_id=row.id, package_key="booking",
                             topic="Новая тема", objective="Новые обращения", audience="Мастера салона")
    assert row.content == original


def test_wrong_tenant_or_missing_package_never_changes_plan(db):
    company, row = make_company_plan(db)
    other = Product(name="Other", slug="other")
    db.add(other)
    db.commit()
    with pytest.raises(LookupError):
        update_draft_package(db, product_id=other.id, plan_id=row.id, package_key="booking",
                             topic="Новая тема", objective="Новые обращения", audience="Мастера")
    with pytest.raises(LookupError):
        update_draft_package(db, product_id=company.id, plan_id=row.id, package_key="unknown",
                             topic="Новая тема", objective="Новые обращения", audience="Мастера")
