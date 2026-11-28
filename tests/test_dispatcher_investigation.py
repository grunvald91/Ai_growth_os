"""Evidence-driven dispatcher investigation and fail-closed recovery tests."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.decision import DispatcherDecision, PlannedStep
from app.dispatcher.investigation import MAX_READS, NextRead, run_investigation
from app.dispatcher.outcome import Context, Outcome
from app.models.domain import Product, Task


@pytest.fixture
def product(db):
    row = Product(name="Investigation", slug="investigation")
    db.add(row)
    db.commit()
    return row


@pytest.mark.asyncio
async def test_adaptive_investigation_chooses_next_view_after_real_evidence(db, product, monkeypatch):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")
    calls = []

    async def initial(*args, recovery=False):
        assert not recovery
        return DispatcherDecision(intent="investigate", capability="list_tasks",
            params={"tab": "problems", "kind": "video"}, confidence=0.95), "test-model"

    async def act(ctx_arg, capability, params):
        assert ctx_arg.product.id == product.id
        calls.append(capability)
        if capability == "list_tasks":
            assert params["tab"] == "problems"
            return Outcome("Остановлено одно задание.", blocks=[
                {"type": "table", "columns": ["Задание", "Причина"], "rows": [["Ролик", "Ожидание модели"]]},
                {"type": "links", "items": [{"label": "Открыть", "href": "/studio/work"}]},
            ]), None, capability
        assert capability == "list_publications"
        return Outcome("Публикаций нет.", no_data=True), None, capability

    async def continuation(ctx_arg, llm, text, observations):
        assert text == "Разберись с производством и публикациями"
        assert observations[0]["tables"][0]["rows"][0][1] == "Ожидание модели"
        assert "href" not in str(observations)
        if len(observations) == 1:
            return NextRead(next_step=PlannedStep(capability="list_publications", params={"status": "problems"}))
        assert observations[1]["no_data"] is True
        return NextRead(conclusion="Известно ожидание модели; проблемные публикации не найдены.")

    monkeypatch.setattr(service, "ask_model", initial)
    monkeypatch.setattr(service, "act", act)
    monkeypatch.setattr(service, "ask_investigation", continuation)
    result = await service.handle_message(db, ctx, object(),
        text="Разберись с производством и публикациями", client_key="adaptive")
    message = result["messages"][-1]
    assert message["intent"] == "investigate"
    workflow = next(b for b in message["blocks"] if b["type"] == "workflow")
    assert [step["status"] for step in workflow["steps"]] == ["completed", "no_data"]
    assert any("Вывод ИИ" in b.get("text", "") for b in message["blocks"])
    assert calls == ["list_tasks", "list_publications"]
    assert not list(db.scalars(select(Task).where(Task.product_id == product.id)))
    replay = await service.handle_message(db, ctx, object(),
        text="Разберись с производством и публикациями", client_key="adaptive")
    assert replay["replayed"] and calls == ["list_tasks", "list_publications"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["whiteboard_video", "set_insights_depth", "schedule_post", "show_work", "invented_tool"])
async def test_followup_rejects_mutations_unknown_and_repeated_reads(target):
    calls = []
    ctx = SimpleNamespace()

    async def act(ctx_arg, capability, params):
        calls.append(capability)
        return Outcome("Фактический результат"), None, capability

    async def next_read(observations):
        return NextRead(next_step=PlannedStep(capability=target))

    result = await run_investigation(ctx, PlannedStep(capability="show_work"),
                                     act=act, next_read=next_read)
    assert calls == ["show_work"]
    assert not result.ok and "Анализ не завершён" in result.text


@pytest.mark.asyncio
async def test_investigation_preserves_evidence_if_model_fails_and_bounds_reads():
    calls = []
    ctx = SimpleNamespace()

    async def act(ctx_arg, capability, params):
        calls.append(capability)
        return Outcome("Фактический результат"), None, capability

    async def fail(observations):
        return None

    result = await run_investigation(ctx, PlannedStep(capability="show_work"),
                                     act=act, next_read=fail)
    assert not result.ok
    assert result.blocks[0]["text"] == "Фактический результат"

    async def continue_forever(observations):
        return NextRead(next_step=PlannedStep(capability="list_tasks", params={"query": str(len(observations))}))

    calls.clear()
    result = await run_investigation(ctx, PlannedStep(capability="show_work"),
                                     act=act, next_read=continue_forever)
    assert len(calls) == MAX_READS
    assert not result.ok and len(result.blocks[0]["steps"]) == MAX_READS


@pytest.mark.asyncio
async def test_empty_first_model_response_is_recovered_once(db, product, monkeypatch):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")
    calls = []

    async def initial(*args, recovery=False):
        calls.append(recovery)
        if not recovery:
            return None, None
        return DispatcherDecision(intent="answer", reply_text="Уточнённый ответ", confidence=0.95), "test-model"

    monkeypatch.setattr(service, "ask_model", initial)
    result = await service.handle_message(db, ctx, object(),
        text="Объясни, какие возможности доступны", client_key="recover-empty")
    assert calls == [False, True]
    assert result["messages"][-1]["text"] == "Уточнённый ответ"
