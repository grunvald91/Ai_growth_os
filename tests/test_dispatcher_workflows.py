"""Durable dispatcher chains: explicit approvals, safe resume and tenant isolation."""
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import workflows
from app.dispatcher.decision import PlannedStep
from app.dispatcher.models import DispatcherWorkflow, DispatcherWorkflowStep
from app.dispatcher.outcome import Context
from app.dispatcher.service import open_conversation
from app.models.domain import Artifact, Product, Task, TaskStatus


@pytest.fixture
def company(db):
    row = Product(name="ServGo", slug="servgo")
    db.add(row)
    db.commit()
    return row


def context(db, company, account="owner", role=None):
    return Context(db=db, product=company, account=account, studio_role=role,
                   settings=Settings(_env_file=None))


def chain(*items):
    return [PlannedStep(capability=capability, params=params) for capability, params in items]


FIRST = ("create_post", {"topic": "Зачем нужна онлайн-запись", "channel": "telegram"})
SECOND = ("create_post", {"topic": "Пять причин неявок", "channel": "vk"})
READ = ("show_work", {})


@pytest.mark.asyncio
async def test_durable_workflow_separate_confirmations_and_decline(db, company):
    ctx = context(db, company)
    conversation = open_conversation(db, company.id, ctx.account)
    initial = await workflows.create(ctx, conversation, chain(FIRST, SECOND, READ), "workflow-1")
    wid = UUID(initial["workflow_id"])
    assert initial["status"] == "waiting_approval"
    assert initial["approval"]["position"] == 0
    assert initial["steps"][0]["status"] == "waiting_approval"
    assert initial["steps"][1]["status"] == "pending"
    assert not list(db.scalars(select(Task)))

    first = await workflows.decide(ctx, wid, position=0, revision=initial["revision"], accept=True)
    assert first["status"] == "waiting_approval"
    assert first["approval"]["position"] == 1
    assert first["steps"][0]["status"] == "completed"
    assert first["steps"][0]["kind"] == "queued_post"
    assert first["steps"][1]["status"] == "waiting_approval"
    tasks = list(db.scalars(select(Task)))
    assert len(tasks) == 1
    assert tasks[0].product_id == company.id
    assert tasks[0].title.startswith("DISPATCHER-WORKFLOW:")
    assert first["steps"][0]["blocks"][0]["task_id"] == str(tasks[0].id)

    with pytest.raises(PermissionError):
        await workflows.decide(ctx, wid, position=0, revision=initial["revision"], accept=True)
    assert len(list(db.scalars(select(Task)))) == 1

    declined = await workflows.decide(ctx, wid, position=1, revision=first["revision"], accept=False)
    assert declined["status"] == "cancelled"
    assert [s["status"] for s in declined["steps"]] == [
        "completed", "declined", "skipped"]
    assert len(list(db.scalars(select(Task)))) == 1

    receipt = list(db.scalars(select(DispatcherWorkflowStep).where(
        DispatcherWorkflowStep.workflow_id == wid,
    ).order_by(DispatcherWorkflowStep.position)))[0]
    assert receipt.task_id == tasks[0].id
    assert receipt.decided_at is not None
    assert receipt.status == "completed"
    assert list(db.scalars(select(DispatcherWorkflow)))[0].revision == declined["revision"]


@pytest.mark.asyncio
async def test_durable_workflow_two_confirmations_then_read_and_resume(db, company):
    ctx = context(db, company)
    conv = open_conversation(db, company.id, ctx.account)
    started = await workflows.create(ctx, conv, chain(FIRST, SECOND, READ), "chain-2")
    assert not list(db.scalars(select(Task)))
    wid = UUID(started["workflow_id"])
    one = await workflows.decide(ctx, wid, position=0, revision=started["revision"], accept=True)
    done = await workflows.decide(ctx, wid, position=1, revision=one["revision"], accept=True)
    assert done["status"] == "completed"
    assert [s["status"] for s in done["steps"]] == [
        "completed", "completed", "completed"]
    assert [s["kind"] for s in done["steps"]] == ["queued_post", "queued_post", "read"]
    assert len(list(db.scalars(select(Task)))) == 2
    resumed = await workflows.resume(ctx, wid)
    assert resumed["steps"] == done["steps"]
    assert len(list(db.scalars(select(Task)))) == 2
    assert await workflows.create(ctx, conv, chain(FIRST, SECOND, READ), "chain-2") == resumed


@pytest.mark.asyncio
async def test_preflight_rejects_unsupported_plans_and_role(db, company):
    ctx = context(db, company)
    for items in (
        chain(READ, ("seo_factory", {"horizon_days": "30"})),
        chain(READ, ("make_plan", {})),
        chain(READ, ("restricted_action", {"kind": "delete"})),
        chain(READ, ("create_post", {})),
        chain(FIRST, FIRST),
        chain(FIRST),
    ):
        with pytest.raises(ValueError):
            workflows.preflight(items, ctx)
    with pytest.raises(PermissionError):
        workflows.preflight(chain(FIRST, READ), context(db, company, role="administrator"))
    assert not list(db.scalars(select(DispatcherWorkflow)))
    assert not list(db.scalars(select(Task)))


@pytest.mark.asyncio
async def test_user_and_company_isolation(db, company):
    other = Product(name="Other", slug="other")
    db.add(other)
    db.commit()
    ctx = context(db, company)
    started = await workflows.create(ctx, open_conversation(db, company.id, ctx.account),
                                     chain(FIRST, READ), "tenant-1")
    wid = UUID(started["workflow_id"])
    other_account = context(db, company, account="colleague")
    other_company = context(db, other)
    for different in (other_account, other_company):
        with pytest.raises(LookupError):
            workflows.snapshot(different, wid)
        with pytest.raises(LookupError):
            await workflows.decide(different, wid, position=0,
                                   revision=started["revision"], accept=True)
        assert workflows.recent(different) == []
    assert not list(db.scalars(select(Task)))
    assert len(workflows.recent(ctx)) == 1


@pytest.mark.asyncio
async def test_safe_read_failure_retries_without_replaying_approved_write(db, company, monkeypatch):
    ctx = context(db, company)
    from app.dispatcher import executors
    from app.dispatcher.outcome import Outcome
    attempted = []
    original = executors.EXECUTORS["show_work"]

    async def unstable(ctx, params):
        attempted.append("read")
        if len(attempted) == 1:
            raise RuntimeError("Temporary database lookup error")
        return Outcome("Открытых заданий: 1.")

    monkeypatch.setitem(executors.EXECUTORS, "show_work", unstable)
    started = await workflows.create(ctx, open_conversation(db, company.id, ctx.account),
                                     chain(FIRST, READ), "retry-safe-read")
    wid = UUID(started["workflow_id"])
    after = await workflows.decide(ctx, wid, position=0,
                                   revision=started["revision"], accept=True)
    assert after["status"] == "failed"
    assert after["can_retry_read"] is True
    assert [s["status"] for s in after["steps"]] == ["completed", "failed"]
    assert len(list(db.scalars(select(Task)))) == 1
    recovered = await workflows.retry_read(ctx, wid, after["revision"])
    assert recovered["status"] == "completed"
    assert [s["status"] for s in recovered["steps"]] == ["completed", "completed"]
    assert len(list(db.scalars(select(Task)))) == 1
    assert attempted == ["read", "read"]
    monkeypatch.setitem(executors.EXECUTORS, "show_work", original)


@pytest.mark.asyncio
async def test_write_failure_is_terminal_and_never_auto_retried(db, company, monkeypatch):
    ctx = context(db, company)
    conv = open_conversation(db, company.id, ctx.account)
    started = await workflows.create(ctx, conv, chain(FIRST, READ), "failure")
    from app.content import service as content_service

    original_queue = content_service.queue_content

    def unavailable(*args, **kwargs):
        created = original_queue(*args, **kwargs)
        assert created.id is not None
        # The Task and its content_request exist only in this open transaction.
        assert len(list(db.scalars(select(Task)))) == 1
        assert len(list(db.scalars(select(Artifact)))) == 1
        raise RuntimeError("Injected failure AFTER inserts, BEFORE workflow commit")

    monkeypatch.setattr(content_service, "queue_content", unavailable)
    failed = await workflows.decide(ctx, UUID(started["workflow_id"]), position=0,
                                    revision=started["revision"], accept=True)
    assert failed["status"] == "failed"
    assert failed["can_retry_read"] is False
    assert [s["status"] for s in failed["steps"]] == ["failed", "skipped"]
    assert not list(db.scalars(select(Task)))
    assert not list(db.scalars(select(Artifact)))
    with pytest.raises(PermissionError):
        await workflows.retry_read(ctx, UUID(started["workflow_id"]), failed["revision"])
    with pytest.raises(PermissionError):
        await workflows.decide(ctx, UUID(started["workflow_id"]), position=0,
                               revision=failed["revision"], accept=True)


@pytest.mark.asyncio
async def test_read_before_write_runs_first_without_starting_generation(db, company):
    ctx = context(db, company)
    start = await workflows.create(
        ctx, open_conversation(db, company.id, ctx.account),
        chain(READ, FIRST), "read-first")
    assert start["status"] == "waiting_approval"
    assert start["approval"]["position"] == 1
    assert [s["status"] for s in start["steps"]] == ["completed", "waiting_approval"]
    assert not list(db.scalars(select(Task)))


@pytest.mark.asyncio
async def test_internal_create_must_match_conversation_owner(db, company):
    other = Product(name="Other company", slug="other")
    db.add(other)
    db.commit()
    ctx = context(db, company)
    foreign = open_conversation(db, other.id, "owner")
    with pytest.raises(PermissionError):
        await workflows.create(ctx, foreign, chain(FIRST, READ), "forged-conversation")
    foreign_account = open_conversation(db, company.id, "colleague")
    with pytest.raises(PermissionError):
        await workflows.create(ctx, foreign_account, chain(FIRST, READ), "forged-account")
    assert not list(db.scalars(select(DispatcherWorkflow)))
    assert not list(db.scalars(select(Task)))


@pytest.mark.asyncio
async def test_completed_dispatch_keeps_live_queued_post_visible_until_task_finishes(db, company):
    """A workflow receipt does not falsely claim that generated content is ready."""
    ctx = context(db, company)
    conversation = open_conversation(db, company.id, ctx.account)
    initial = await workflows.create(ctx, conversation, chain(FIRST, READ), "live-tasks")
    wid = UUID(initial["workflow_id"])
    sent = await workflows.decide(ctx, wid, position=0, revision=initial["revision"], accept=True)
    assert sent["status"] == "completed"  # Dispatch finished, not generation.
    assert sent["task_summary"]["queued"] == 1
    assert sent["task_summary"]["total"] == 1
    assert sent["steps"][0]["task_status"] == "queued"
    assert sent["steps"][0]["blocks"][0]["status"] == "queued"
    assert any(row["workflow_id"] == str(wid) for row in workflows.recent(ctx))

    task = db.scalar(select(Task).where(Task.product_id == company.id))
    for new_status, exposed in (
        (TaskStatus.RUNNING, "running"),
        (TaskStatus.WAITING_APPROVAL, "waiting_approval"),
        (TaskStatus.FAILED, "failed"),
    ):
        task.status = new_status
        db.commit()
        refreshed = workflows.snapshot(ctx, wid)
        assert refreshed["status"] == "completed"
        assert refreshed["steps"][0]["task_status"] == exposed
        assert refreshed["steps"][0]["blocks"][0]["status"] == exposed
        assert refreshed["task_summary"][exposed] == 1
        assert any(row["workflow_id"] == str(wid) for row in workflows.recent(ctx))
        assert len(list(db.scalars(select(Task)))) == 1

    task.status = TaskStatus.COMPLETED
    db.commit()
    done = workflows.snapshot(ctx, wid)
    assert done["steps"][0]["task_status"] == "completed"
    assert done["task_summary"]["completed"] == 1
    assert all(row["workflow_id"] != str(wid) for row in workflows.recent(ctx))
    # Refreshing task state is a read, never a retry of the already approved action.
    assert len(list(db.scalars(select(Task)))) == 1


@pytest.mark.asyncio
async def test_workflow_does_not_expose_foreign_or_missing_task_receipts(db, company):
    other = Product(name="Other", slug="other")
    db.add(other)
    db.commit()
    ctx = context(db, company)
    started = await workflows.create(
        ctx, open_conversation(db, company.id, ctx.account),
        chain(FIRST, READ), "foreign-receipt")
    wid = UUID(started["workflow_id"])
    sent = await workflows.decide(ctx, wid, position=0,
                                  revision=started["revision"], accept=True)
    original = db.scalar(select(Task).where(Task.product_id == company.id))
    foreign = Task(product_id=other.id, title="FOREIGN SECRET POST",
                   assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add(foreign)
    db.flush()
    step = db.scalar(select(DispatcherWorkflowStep).where(
        DispatcherWorkflowStep.workflow_id == wid,
        DispatcherWorkflowStep.position == 0))
    step.task_id = foreign.id
    db.commit()
    snapshot = workflows.snapshot(ctx, wid)
    assert snapshot["steps"][0]["task_status"] == "unavailable"
    assert snapshot["steps"][0]["blocks"] == []
    assert "FOREIGN SECRET POST" not in str(snapshot)
    assert snapshot["task_summary"]["unavailable"] == 1
    assert all(row["workflow_id"] != str(wid) for row in workflows.recent(ctx))
    assert original.id != foreign.id


@pytest.mark.asyncio
async def test_waiting_confirmations_are_recovered_while_prior_post_runs(db, company):
    ctx = context(db, company)
    started = await workflows.create(
        ctx, open_conversation(db, company.id, ctx.account),
        chain(FIRST, SECOND, READ), "two-live-posts")
    wid = UUID(started["workflow_id"])
    one = await workflows.decide(ctx, wid, position=0,
                                 revision=started["revision"], accept=True)
    assert one["status"] == "waiting_approval"
    assert one["task_summary"]["total"] == 1
    assert one["task_summary"]["queued"] == 1
    assert one["steps"][1]["task_status"] is None
    recovered = workflows.recent(ctx)
    assert len(recovered) == 1
    assert recovered[0]["approval"]["position"] == 1
    assert recovered[0]["steps"][0]["task_status"] == "queued"
    assert len(list(db.scalars(select(Task)))) == 1
