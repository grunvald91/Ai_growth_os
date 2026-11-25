"""No early SEO failover; a restart cannot erase a recorded outage."""
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.llm.seo_availability import AvailabilityPolicy, ModelDeferred, WAIT_KIND, defer_task, ready_filter
from app.models.domain import Artifact, Task, TaskStatus
from test_seo_factory import env  # noqa: F401


@pytest.mark.asyncio
async def test_cadence_and_confirmed_24h_failover_survive_restart(tmp_path):
    clock = SimpleNamespace(now=1_800_000_000.0)
    start = clock.now
    calls = []
    async def call(model):
        calls.append(model)
        if model == "main":
            raise httpx.ConnectError("offline")
        return "fallback result"

    def policy():
        return AvailabilityPolicy(tmp_path, "gateway", clock=lambda: clock.now)

    with pytest.raises(ModelDeferred) as first:
        await policy().generate(("main", "reserve"), call)
    assert first.value.retry_at == start + 300
    clock.now = start + 299
    with pytest.raises(ModelDeferred):
        await policy().generate(("main", "reserve"), call)
    assert calls == ["main"]
    for offset in range(300, 3601, 300):
        clock.now = start + offset
        with pytest.raises(ModelDeferred) as waiting:
            await policy().generate(("main", "reserve"), call)
        assert waiting.value.retry_at == clock.now + (300 if offset < 3600 else 900)
    for offset in range(4500, 86400, 900):
        clock.now = start + offset
        with pytest.raises(ModelDeferred):
            await policy().generate(("main", "reserve"), call)
    assert set(calls) == {"main"}
    clock.now = start + 86400
    assert await policy().generate(("main", "reserve"), call) == "fallback result"
    assert calls[-2:] == ["main", "reserve"]


@pytest.mark.asyncio
async def test_recovery_resets_outage_and_nontransient_error_never_falls_back(tmp_path):
    clock = SimpleNamespace(now=1_800_000_000.0)
    policy = AvailabilityPolicy(tmp_path, "gateway", clock=lambda: clock.now)
    calls = []
    mode = "offline"

    async def call(model):
        calls.append(model)
        if mode == "offline":
            raise httpx.ConnectError("offline")
        if mode == "invalid":
            raise ValueError("invalid configuration")
        return "primary"

    with pytest.raises(ModelDeferred):
        await policy.generate(("main", "reserve"), call)
    clock.now += 300
    mode = "online"
    assert await policy.generate(("main", "reserve"), call) == "primary"
    mode = "offline"
    clock.now += 86400
    with pytest.raises(ModelDeferred):
        await policy.generate(("main", "reserve"), call)
    clock.now += 300
    mode = "invalid"
    with pytest.raises(ValueError):
        await policy.generate(("main", "reserve"), call)
    assert set(calls) == {"main"}


def test_durable_wait_filters_only_eligible_tasks_and_keeps_publication_date(env):
    factory, _, company = env
    now = datetime.now(timezone.utc)
    with factory() as db:
        task = Task(product_id=company, title="SEO-ARTICLE:plan:a001",
            assigned_agent="content_factory", status=TaskStatus.RUNNING, due_at=now)
        db.add(task)
        db.flush()
        task_id = task.id
        due_at = task.due_at
        defer_task(db, task=task, deferred=ModelDeferred(now.timestamp() + 300))
        db.commit()
        assert task.status == TaskStatus.QUEUED and task.due_at == due_at
    with factory() as db:
        assert db.scalar(select(Task).where(Task.id == task_id, ready_filter(now))) is None
        later = datetime.fromtimestamp(now.timestamp() + 301, timezone.utc)
        assert db.scalar(select(Task).where(Task.id == task_id, ready_filter(later))) is not None
        assert db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == WAIT_KIND))
