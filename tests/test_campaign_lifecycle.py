"""Lifecycle commands operate on evidence, not a cosmetic Experiment.status flip."""
import json

from sqlalchemy import select

from app.models.domain import (
    Artifact, Experiment, Product, Task, TaskStatus, ToolCall, Prospect,
    Publication, Decision,
)
from app.services.campaign_lifecycle import (
    command, reconcile_pending, blockers, task_halted, CampaignLifecycleError,
)


def campaign(db, product, key="EXP-LIFECYCLE-001"):
    row = Experiment(product_id=product.id, experiment_key=key,
                     name="Measured marketing work", hypothesis="Test",
                     status="running")
    db.add(row)
    db.flush()
    return row


def linked_task(db, product, experiment, status=TaskStatus.QUEUED):
    task = Task(product_id=product.id, title=f"{experiment.experiment_key}: prepare experiment",
                assigned_agent="research_pmm", status=status)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product.id, task_id=task.id, kind="task_brief",
                    title=task.title, content=json.dumps({
                        "experiment_id": str(experiment.id),
                        "experiment_key": experiment.experiment_key,
                        "task_type": "research",
                    })))
    db.flush()
    return task


def test_stop_cancels_only_verified_queued_tasks_is_idempotent_and_audited(db):
    owner = Product(name="Business A", slug="stop-business-a")
    other = Product(name="Business B", slug="stop-business-b")
    db.add_all([owner, other])
    db.flush()
    row = campaign(db, owner)
    other_row = campaign(db, other, "EXP-LIFECYCLE-002")
    task = linked_task(db, owner, row)
    child = Task(product_id=owner.id, parent_id=task.id,
                 title="research child", status=TaskStatus.QUEUED)
    db.add(child)
    stranger = linked_task(db, other, other_row)
    free = Task(product_id=owner.id, title="Unrelated work", status=TaskStatus.QUEUED)
    db.add(free)
    db.commit()
    status, pending = command(db, product_id=owner.id, experiment_id=row.id,
                              action="stop", actor="studio:test")
    assert status == "stopped" and pending.clear
    db.refresh(task)
    db.refresh(child)
    assert task.status == child.status == TaskStatus.CANCELLED
    assert stranger.status == free.status == TaskStatus.QUEUED
    assert db.get(Experiment, other_row.id).status == "running"
    audits = list(db.scalars(select(Artifact).where(
        Artifact.product_id == owner.id, Artifact.kind == "campaign_lifecycle_event")))
    assert len(audits) == 2
    assert {json.loads(a.content)["command"] for a in audits} == {"stop", "stopped"}
    assert task_halted(db, task, {"experiment_id": str(row.id)})
    assert not task_halted(db, free, {"experiment_id": str(row.id)})
    status, pending = command(db, product_id=owner.id, experiment_id=row.id,
                              action="stop", actor="studio:again")
    assert status == "stopped" and pending.clear
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.product_id == owner.id, Artifact.kind == "campaign_lifecycle_event")))) == 2


def test_running_tasks_smtp_and_publication_keep_stop_pending_until_resolved(db):
    p = Product(name="Business pending", slug="pending-ops-business")
    db.add(p)
    db.flush()
    row = campaign(db, p)
    parent = linked_task(db, p, row, TaskStatus.RUNNING)
    child = Task(product_id=p.id, parent_id=parent.id, title="Research in flight",
                 status=TaskStatus.RUNNING)
    material = Artifact(product_id=p.id, kind="channel_text",
                        title="Post linked to campaign", content="{}")
    db.add_all([child, material])
    db.flush()
    db.add(Artifact(product_id=p.id, kind="campaign_workspace",
                    title=f"Campaign resources {row.id}",
                    content=json.dumps({"experiment_id": str(row.id),
                                        "material_ids": [str(material.id)]})))
    post = Publication(product_id=p.id, artifact_id=material.id,
                       channel="telegram", status="scheduled")
    prospect = Prospect(product_id=p.id, experiment_id=row.id, lead_key="p1",
                        business_name="Customer")
    db.add_all([post, prospect])
    db.flush()
    send = ToolCall(product_id=p.id, tool_name="smtp",
                    operation="send_outreach_message",
                    request_data={"prospect_id": str(prospect.id)},
                    status="sending")
    db.add(send)
    db.commit()

    status, pending = command(db, product_id=p.id, experiment_id=row.id,
                              action="stop", actor="owner")
    assert status == "stopping"
    assert pending.active_tasks == 2
    assert pending.sending_emails == 1
    assert pending.pending_publications == 1
    assert reconcile_pending(db) == 0

    parent.status = child.status = TaskStatus.COMPLETED
    post.status = "cancelled"
    send.status = "delivery_unknown"
    db.commit()
    assert reconcile_pending(db) == 0
    assert blockers(db, row).uncertain_emails == 1
    db.refresh(row)
    assert row.status == "stopping"
    send.status = "smtp_accepted"  # operator / sender actually reconciled outcome
    db.commit()
    assert reconcile_pending(db) == 1
    db.refresh(row)
    assert row.status == "stopped"
    assert reconcile_pending(db) == 0


def test_finish_preserves_evidence_and_note_without_inventing_decision(db):
    p = Product(name="Finished", slug="finished-business")
    db.add(p)
    db.flush()
    row = campaign(db, p)
    db.commit()
    status, pending = command(
        db, product_id=p.id, experiment_id=row.id, action="finish",
        actor="studio:owner", note="Пока нет достаточно заявок для вывода",
    )
    assert status == "completed" and pending.clear
    db.refresh(row)
    assert row.conclusion == "Вывод владельца: Пока нет достаточно заявок для вывода"
    assert list(db.scalars(select(Decision))) == []
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.kind == "campaign_lifecycle_event",
        Artifact.product_id == p.id)))) == 2
    assert command(db, product_id=p.id, experiment_id=row.id,
                   action="finish", actor="owner")[0] == "completed"
    import pytest
    with pytest.raises(CampaignLifecycleError):
        command(db, product_id=p.id, experiment_id=row.id, action="stop", actor="owner")
    with pytest.raises(LookupError):
        command(db, product_id=p.id, experiment_id=row.id,
                action="finish", actor="other",
                note="", ) if False else command(
                    db, product_id=p.id, experiment_id=other_uuid(),
                    action="finish", actor="other",
                )


def other_uuid():
    from uuid import uuid4
    return uuid4()
