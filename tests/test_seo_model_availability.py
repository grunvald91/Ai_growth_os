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


@pytest.mark.asyncio
async def test_article_wait_is_not_failed_and_retains_previous_stage(env, tmp_path):
    from app.models.domain import Product
    from test_articles import SequenceProvider, Settings, build_article_production
    from test_seo_article import request_for, outputs_with_geo
    factory, _, company = env
    now = datetime.now(timezone.utc)
    with factory() as db:
        product = db.get(Product, company)
        task = Task(product_id=company, title="SEO-ARTICLE:plan:a001",
            assigned_agent="content_factory", status=TaskStatus.QUEUED, due_at=now)
        db.add(task)
        db.commit()
        task_id = task.id
        due = task.due_at
        provider = SequenceProvider([outputs_with_geo()[0], ModelDeferred(now.timestamp() + 300)])
        workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
        result = await workflow.run(request_for(product).model_copy(update={"task_id": task_id, "light": True}))
        assert result.status == "deferred" and not result.retryable
        assert len(result.checkpoint_artifact_ids) == 1
        assert db.get(Task, task_id).status == TaskStatus.QUEUED
        assert db.get(Task, task_id).due_at.replace(tzinfo=timezone.utc) == due.replace(tzinfo=timezone.utc)
        assert db.scalar(select(Task).where(Task.id == task_id, ready_filter(now))) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("bad response"),
    __import__("app.llm.errors", fromlist=["ProviderEmptyResponseError"]).ProviderEmptyResponseError("empty"),
    __import__("app.llm.errors", fromlist=["ProviderMalformedResponseError"]).ProviderMalformedResponseError("malformed")])
async def test_response_error_does_not_start_unavailability_or_select_reserve(tmp_path, error):
    calls = []
    policy = AvailabilityPolicy(tmp_path, "gateway")
    async def call(model):
        calls.append(model)
        raise error
    with pytest.raises(type(error)):
        await policy.generate(("any-new-model", "reserve"), call)
    assert calls == ["any-new-model"]
    assert not list(policy.root.glob("*.json"))


@pytest.mark.asyncio
async def test_reserve_itself_must_wait_before_third_model(tmp_path):
    clock = SimpleNamespace(now=1_800_000_000.0)
    start = clock.now
    calls = []
    async def call(model):
        calls.append(model)
        if model != "third":
            raise httpx.ConnectError("offline")
        return "third"
    def policy():
        return AvailabilityPolicy(tmp_path, "gateway", clock=lambda: clock.now)
    chain = ("main", "reserve", "third")
    with pytest.raises(ModelDeferred):
        await policy().generate(chain, call)
    clock.now = start + 86400
    with pytest.raises(ModelDeferred) as wait:
        await policy().generate(chain, call)
    assert calls == ["main", "main", "reserve"]
    assert wait.value.retry_at == start + 86400 + 300
    clock.now += 300
    with pytest.raises(ModelDeferred):
        await policy().generate(chain, call)
    assert calls[-1] == "reserve"
    assert "third" not in calls
    clock.now = start + 2 * 86400
    assert await policy().generate(chain, call) == "third"


@pytest.mark.asyncio
async def test_configuration_error_breaks_previous_outage(tmp_path):
    clock = SimpleNamespace(now=1_800_000_000.0)
    mode = "offline"
    async def call(model):
        if mode == "invalid":
            raise ValueError("invalid configuration")
        if model == "main":
            raise httpx.ConnectError("offline")
        return "reserve"
    p = AvailabilityPolicy(tmp_path, "gateway", clock=lambda: clock.now)
    with pytest.raises(ModelDeferred):
        await p.generate(("main", "reserve"), call)
    clock.now += 86400
    mode = "invalid"
    with pytest.raises(ValueError):
        await p.generate(("main", "reserve"), call)
    mode = "offline"
    with pytest.raises(ModelDeferred) as wait:
        await p.generate(("main", "reserve"), call)
    assert wait.value.retry_at == clock.now + 300
