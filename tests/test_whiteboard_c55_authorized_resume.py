"""Hard ceiling for a specific owner-approved paid continuation."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.content.whiteboard.jobs import CallLedger, RESUME_ACTION, owner_task, records
from app.content.whiteboard.runtime import ReadinessError
from app.models.domain import AgentRun, Approval, TaskStatus
from test_whiteboard_jobs import approve, environment, new_job


def authorize_two_attempts(environment):
    factory, _, product_id, _ = environment
    task_id = new_job(environment)
    approve(environment, task_id)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        source, state, original, payload = records(db, task)
        assert original.status == "approved"
        extra = Approval(
            product_id=product_id, artifact_id=source.id,
            action_type=RESUME_ACTION, risk_level="YELLOW",
            status="approved", requested_by="owner",
            decided_by="owner-test-authorized-attempts",
        )
        db.add(extra)
        db.flush()
        saved = json.loads(state.content)
        saved["extra_calls"] = 2
        saved["owner_chat_paid_attempt_cap"] = {
            "approval_id": str(extra.id),
            "authorization": "owner-test-authorized-attempts",
            "baseline_runs": 0,
            "max_additional": 2,
        }
        state.content = json.dumps(saved)
        task.status = TaskStatus.RUNNING
        db.commit()
    return factory, product_id, task_id


def test_authorized_attempts_never_replenish_after_refunds(environment):
    factory, product_id, task_id = authorize_two_attempts(environment)
    ledger = CallLedger(factory, product_id, task_id)
    ledger.finish(ledger.begin("speech"))
    ledger.finish(ledger.begin("regions"))
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        _, state, _, _ = records(db, task)
        value = json.loads(state.content)
        value["calls"] = 0  # Simulate 502/503 credit refund.
        state.content = json.dumps(value)
        db.commit()
    with pytest.raises(ReadinessError, match="call_limit"):
        ledger.begin("speech")
    with factory() as db:
        assert len(list(db.scalars(select(AgentRun).where(AgentRun.task_id == task_id)))) == 2


def test_paid_cap_requires_same_approved_owner_grant(environment):
    factory, product_id, task_id = authorize_two_attempts(environment)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        _, state, _, _ = records(db, task)
        value = json.loads(state.content)
        value["owner_chat_paid_attempt_cap"]["approval_id"] = str(uuid4())
        state.content = json.dumps(value)
        db.commit()
    with pytest.raises(ReadinessError, match="approval_invalid"):
        CallLedger(factory, product_id, task_id).begin("speech")
    with factory() as db:
        assert db.scalar(select(AgentRun).where(AgentRun.task_id == task_id)) is None
