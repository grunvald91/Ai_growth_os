"""Evidence-driven dispatcher investigation and fail-closed recovery tests."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.decision import DispatcherDecision, PlannedStep
from app.dispatcher.investigation import MAX_READS, NextRead, evidence, run_investigation
from app.dispatcher.outcome import Context, Outcome
from app.models.domain import Product, Task, TaskStatus


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


@pytest.mark.asyncio
async def test_real_search_evidence_has_scoped_task_id_and_no_links(db, product):
    from app.dispatcher import executors
    from app.dispatcher.registry import TaskListParams
    foreign = Product(name="Other company", slug="other-investigation")
    db.add(foreign)
    db.flush()
    own = Task(product_id=product.id, title="Own failed video", assigned_agent="whiteboard_renderer",
               status=TaskStatus.FAILED)
    other = Task(product_id=foreign.id, title="Foreign failed video", assigned_agent="whiteboard_renderer",
                 status=TaskStatus.FAILED)
    db.add_all([own, other])
    db.commit()
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")
    params = TaskListParams(tab="problems", kind="video")
    result = await executors.list_tasks(ctx, params)
    observed = evidence(result, "list_tasks", params.model_dump(mode="json"))
    assert observed["params"]["tab"] == "problems"
    assert [row["task_id"] for row in observed["tasks"]] == [str(own.id)]
    assert str(other.id) not in str(observed)
    assert "href" not in str(observed)
    empty = await executors.list_tasks(ctx, TaskListParams(tab="active", kind="video"))
    assert empty.ok and empty.no_data


@pytest.mark.asyncio
async def test_empty_conclusion_not_reported_as_finished():
    async def act(ctx, capability, params):
        return Outcome("Известный результат"), None, capability

    async def blank(observations):
        return NextRead(conclusion="  ")

    result = await run_investigation(SimpleNamespace(), PlannedStep(capability="show_work"),
                                     act=act, next_read=blank)
    assert not result.ok and "Анализ не завершён" in result.text
    assert result.blocks[0]["text"] == "Известный результат"


@pytest.mark.asyncio
async def test_read_failure_yields_only_partial_conclusion(db, product):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")

    async def act(ctx_arg, capability, params):
        if capability == "list_tasks":
            return Outcome("Не удалось проверить задания", ok=False), None, capability
        return Outcome("Нет проблемных публикаций", no_data=True), None, capability

    async def next_read(observations):
        if len(observations) == 1:
            return NextRead(next_step=PlannedStep(capability="list_publications"))
        return NextRead(conclusion="Причина сбоя заданий неизвестна.")

    result = await run_investigation(ctx, PlannedStep(capability="list_tasks"),
                                     act=act, next_read=next_read)
    assert not result.ok and "Анализ не завершён" in result.text
    assert result.blocks[-1]["text"].startswith("Предварительный вывод ИИ")


@pytest.mark.asyncio
async def test_recovery_cannot_extend_original_deadline(db, product, monkeypatch):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")
    calls = []

    async def initial(*args, recovery=False):
        calls.append(recovery)
        if recovery:
            await asyncio.sleep(2)
            raise AssertionError("Recovery must be cancelled")
        return None, None

    monkeypatch.setattr(service, "ask_model", initial)
    monkeypatch.setattr(service, "DECISION_TIMEOUT_SECONDS", 0.1)
    start = time.monotonic()
    result = await service.handle_message(db, ctx, object(), text="Как лучше начать?",
                                          client_key="deadline-recovery")
    assert calls == [False, True]
    assert time.monotonic() - start < 0.5
    assert result["messages"][-1]["intent"] == "failed"


@pytest.mark.asyncio
async def test_followup_timeout_keeps_first_read(db, product):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")

    async def act(ctx_arg, capability, params):
        return Outcome("Первый результат"), None, capability

    async def slow(observations):
        await asyncio.sleep(2)
        return NextRead(conclusion="Не должен быть показан")

    start = time.monotonic()
    result = await run_investigation(ctx, PlannedStep(capability="show_work"), act=act,
                                     next_read=slow, deadline=start + 0.05)
    assert time.monotonic() - start < 0.5
    assert not result.ok and "Анализ не завершён" in result.text
    assert result.blocks[0]["text"] == "Первый результат"


@pytest.mark.asyncio
async def test_invalid_initial_capability_does_not_escape_validation(db, product, monkeypatch):
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")

    async def initial(*args, recovery=False):
        return DispatcherDecision(intent="investigate", capability="x" * 100,
                                  confidence=0.95), "test-model"

    monkeypatch.setattr(service, "ask_model", initial)
    result = await service.handle_message(db, ctx, object(), text="Разберись с проблемой",
                                          client_key="invalid-initial")
    assert result["messages"][-1]["intent"] == "clarify"
    assert not list(db.scalars(select(Task)))


def test_investigation_uses_new_resource_cards_without_links_or_actions():
    result = Outcome("Задание остановлено", blocks=[
        {"type": "resource_list", "title": "Задания", "filterable": True, "items": [
            {"kind": "task", "title": "Видео", "status": "Ошибка", "detail": "Озвучка недоступна",
             "href": "/studio/products/demo/tasks/one", "link_label": "Открыть задание"}]},
        {"type": "chips", "items": [{"label": "Повторить", "capability": "resume_video", "params": {}}]},
    ])
    view = evidence(result, "list_tasks", {"list_presentation": "list"})
    assert view["resources"] == [{"title": "Видео", "status": "Ошибка", "detail": "Озвучка недоступна"}]
    assert "/studio/" not in str(view) and "resume_video" not in str(view)
