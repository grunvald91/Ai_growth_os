"""Dispatcher: policy in code, one action per turn, honest answers, audit records, isolation."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.blocks import clean_blocks, safe_href
from app.dispatcher.executors import Context
from app.dispatcher.models import DispatcherMessage
from app.dispatcher.policy import Cost, Verdict, verdict
from app.dispatcher.registry import CAPABILITIES
from app.dispatcher import executors
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import Artifact, Approval, CostRecord, Product, Task, TaskStatus, ToolCall
from app.privacy import PrivacyGateway
from app.tools import ToolOperation


@pytest.fixture
def product(db):
    row = Product(name="ServGo", slug="servgo")
    db.add(row)
    db.commit()
    return row


def settings(tmp_path=None):
    return Settings(_env_file=None, whiteboard_storyboard_model="story-model", whiteboard_image_model="google/image",
                    whiteboard_speech_model="speech-model", whiteboard_regions_model="regions-model", products_root=tmp_path or "products")


def context(db, product, account="owner"):
    return Context(db=db, product=product, settings=settings(), account=account)


def llm(decision: dict | str):
    content = decision if isinstance(decision, str) else json.dumps(decision, ensure_ascii=False)
    provider = FakeLLMProvider(LLMResponse(content=content, model="fake-dispatcher",
                                           usage=LLMUsage(input_tokens=120, output_tokens=40)))
    return LLMService(privacy_gateway=PrivacyGateway(), provider=provider, critic_enabled=False), provider


def post_chip(topic="Онлайн-запись без звонков", channel="telegram"):
    return {"label": "Пост: " + topic, "capability": "create_post", "params": {"topic": topic, "channel": channel}}


async def send(db, product, *, text="", chip=None, key="k1", model=None, account="owner"):
    return await service.handle_message(db, context(db, product, account), model, text=text, client_key=key,
                                        mode="chip" if chip else "text", chip=chip)


def tasks(db, agent):
    return list(db.scalars(select(Task).where(Task.assigned_agent == agent)))


# ---- policy -----------------------------------------------------------------------------

def test_policy_follows_risk_and_cost():
    assert verdict(ToolOperation.ANALYTICS, Cost.NONE) == Verdict.RUN
    assert verdict(ToolOperation.GENERATE_DRAFT, Cost.CHEAP) == Verdict.RUN
    assert verdict(ToolOperation.GENERATE_DRAFT, Cost.PAID_LATER) == Verdict.RUN
    assert verdict(ToolOperation.GENERATE_DRAFT, Cost.PAID) == Verdict.CONFIRM_FIRST
    assert verdict(ToolOperation.PUBLISH, Cost.NONE) == Verdict.CONFIRM_FIRST
    for red in (ToolOperation.INCREASE_BUDGET, ToolOperation.CHANGE_PRICING, ToolOperation.CHANGE_ACCESS,
                ToolOperation.DESTRUCTIVE_OPERATION, ToolOperation.CHANGE_CREDENTIALS):
        assert verdict(red, Cost.NONE) == Verdict.REFUSE


def test_every_capability_has_a_way_to_run():
    for capability in CAPABILITIES.values():
        decision = verdict(capability.operation, capability.cost)
        if decision == Verdict.CONFIRM_FIRST:
            assert capability.id in executors.DESCRIBERS and capability.id in executors.CONFIRMERS
        else:
            assert capability.id in executors.EXECUTORS


# ---- blocks -----------------------------------------------------------------------------

def test_blocks_drop_foreign_links_and_misshapen_tables():
    assert safe_href("/studio/products/x/plan")
    assert safe_href("https://servgo.ru/admin")
    assert not safe_href("https://evil.example/studio")
    assert not safe_href("//evil.example/studio")
    assert not safe_href("javascript:alert(1)")
    assert not safe_href("https://servgo.ru.evil.example/")
    kept = clean_blocks([
        {"type": "links", "items": [{"label": "ok", "href": "/studio/team"}]},
        {"type": "links", "items": [{"label": "bad", "href": "https://evil.example/"}]},
        {"type": "table", "columns": ["a", "b"], "rows": [["1"]]},
        {"type": "unknown"},
        {"type": "text", "text": "hello"},
    ])
    assert [block["type"] for block in kept] == ["links", "text"]


# ---- a hint chip: no model, one action, safe to repeat ----------------------------------

@pytest.mark.asyncio
async def test_chip_creates_post_without_calling_the_model(db, product):
    model, provider = llm({"intent": "answer", "reply_text": "x"})
    result = await send(db, product, chip=post_chip(), model=model)
    assert provider.requests == []
    created = tasks(db, "content_factory")
    assert len(created) == 1 and created[0].status == TaskStatus.QUEUED
    user, answer = result["messages"]
    assert user["role"] == "user" and answer["role"] == "assistant"
    assert answer["blocks"][0]["text"].startswith("Принял.")
    assert any(block["type"] == "task" and block["task_id"] == str(created[0].id) for block in answer["blocks"])
    call = db.scalar(select(ToolCall).where(ToolCall.operation == "create_post"))
    assert call is not None and call.task_id == created[0].id and call.status == "executed"


@pytest.mark.asyncio
async def test_retry_with_same_key_does_not_repeat_the_action(db, product):
    first = await send(db, product, chip=post_chip(), key="same")
    again = await send(db, product, chip=post_chip(), key="same")
    assert again["replayed"] is True
    assert len(tasks(db, "content_factory")) == 1
    assert [m["id"] for m in again["messages"]] == [m["id"] for m in first["messages"]]
    assert db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant").limit(1)) is not None
    assert len(list(db.scalars(select(ToolCall)))) == 1


@pytest.mark.asyncio
async def test_unknown_or_forged_chip_is_refused_not_run(db, product):
    result = await send(db, product, chip={"label": "x", "capability": "restricted_action",
                                           "params": {"kind": "budget"}})
    assert result["messages"][1]["intent"] == "unsupported"
    result = await send(db, product, chip={"label": "x", "capability": "delete_everything", "params": {}}, key="k2")
    assert result["messages"][1]["intent"] == "unsupported"
    assert list(db.scalars(select(Task))) == []


@pytest.mark.asyncio
async def test_chip_with_invalid_parameters_asks_instead_of_acting(db, product):
    result = await send(db, product, chip={"label": "Пост", "capability": "create_post", "params": {}})
    answer = result["messages"][1]
    assert "тему" in answer["text"]
    assert tasks(db, "content_factory") == []


# ---- free text: the model decides, the server checks ------------------------------------

@pytest.mark.asyncio
async def test_text_request_is_routed_by_model_decision(db, product):
    model, provider = llm({"intent": "execute", "capability": "create_post", "confidence": 0.93,
                           "params": {"topic": "Как снизить неявки", "channel": "telegram"},
                           "reply_text": "Сделаю"})
    result = await send(db, product, text="напиши пост про неявки", model=model)
    assert len(tasks(db, "content_factory")) == 1
    request = provider.last_request
    assert request.metadata["team_role"] == "dispatcher"
    assert "create_post" in request.messages[-1].content
    cost = db.scalar(select(CostRecord).where(CostRecord.details["task_type"].as_string() == "dispatcher"))
    assert cost is not None and cost.input_tokens == 120 and cost.product_id == product.id
    stored = db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant"))
    assert stored.capability == "create_post" and stored.confidence == pytest.approx(0.93)
    assert stored.model == "fake-dispatcher" and stored.task_id is not None
    assert result["messages"][1]["intent"] == "execute"


@pytest.mark.asyncio
async def test_low_confidence_asks_and_does_nothing(db, product):
    model, _ = llm({"intent": "execute", "capability": "create_post", "confidence": 0.3,
                    "params": {"topic": "что-то про салон"}, "reply_text": "Про что именно пост?"})
    result = await send(db, product, text="ну сделай что-нибудь", model=model)
    assert result["messages"][1]["intent"] == "clarify"
    assert tasks(db, "content_factory") == []


@pytest.mark.asyncio
async def test_missing_topic_is_asked_not_guessed(db, product):
    model, _ = llm({"intent": "execute", "capability": "create_post", "confidence": 0.9, "params": {}})
    result = await send(db, product, text="напиши пост", model=model)
    assert "тему" in result["messages"][1]["text"]
    assert tasks(db, "content_factory") == []


@pytest.mark.asyncio
async def test_unknown_capability_from_model_is_unsupported(db, product):
    model, _ = llm({"intent": "execute", "capability": "launch_ads", "confidence": 0.99, "params": {}})
    result = await send(db, product, text="запусти рекламу", model=model)
    assert result["messages"][1]["intent"] == "unsupported"
    assert list(db.scalars(select(Task))) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["budget", "pricing", "access", "delete", "credentials", "billing"])
async def test_red_requests_are_refused_by_code(db, product, kind):
    model, _ = llm({"intent": "execute", "capability": "restricted_action", "confidence": 1.0,
                    "params": {"kind": kind}, "reply_text": "Хорошо, сделаю"})
    result = await send(db, product, text="увеличь бюджет", model=model, key=kind)
    answer = result["messages"][1]
    assert "Хорошо, сделаю" not in answer["text"]
    assert answer["blocks"][0]["text"].endswith("вручную владельцем в настройках.")
    assert list(db.scalars(select(Task))) == [] and list(db.scalars(select(Approval))) == []


@pytest.mark.asyncio
async def test_model_failure_gives_hints_not_an_error(db, product):
    model, _ = llm("это не JSON")
    result = await send(db, product, text="сделай красиво", model=model)
    answer = result["messages"][1]
    assert answer["intent"] == "failed"
    assert any(block["type"] == "chips" for block in answer["blocks"])
    assert list(db.scalars(select(Task))) == []


@pytest.mark.asyncio
async def test_no_model_configured_still_answers(db, product):
    result = await send(db, product, text="привет", model=None)
    assert result["messages"][1]["intent"] == "failed"


@pytest.mark.asyncio
async def test_model_chips_are_kept_only_when_complete_and_known(db, product):
    model, _ = llm({"intent": "answer", "reply_text": "Могу так", "confidence": 0.9, "next_chips": [
        {"label": "Пост про неявки", "capability": "create_post", "params": {"topic": "Неявки клиентов"}},
        {"label": "Без темы", "capability": "create_post", "params": {}},
        {"label": "Чужое", "capability": "wipe_database", "params": {}},
        {"label": "Запрещённое", "capability": "restricted_action", "params": {"kind": "budget"}},
    ]})
    result = await send(db, product, text="что ты умеешь?", model=model)
    chips = [b for b in result["messages"][1]["blocks"] if b["type"] == "chips"][0]["items"]
    assert [c["label"] for c in chips] == ["Пост про неявки"]


@pytest.mark.asyncio
async def test_schedule_is_reported_as_not_connected(db, product):
    result = await send(db, product, chip={"label": "Записи на сегодня", "capability": "show_schedule", "params": {}})
    answer = result["messages"][1]
    assert "не вижу" in answer["text"]
    assert not any(block["type"] == "table" for block in answer["blocks"])
    links = [b for b in answer["blocks"] if b["type"] == "links"][0]["items"]
    assert links[0]["href"] == "https://servgo.ru/admin/schedule"


# ---- reading ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_what_is_in_work_comes_from_the_database(db, product):
    db.add(Task(product_id=product.id, title="CONTENT: x", assigned_agent="content_factory", status=TaskStatus.RUNNING))
    db.commit()
    result = await send(db, product, chip={"label": "Что в работе", "capability": "show_work", "params": {}})
    answer = result["messages"][1]
    assert answer["blocks"][0]["text"] == "В работе: 1."
    table = [b for b in answer["blocks"] if b["type"] == "table"][0]
    assert table["columns"] == ["Задание", "Статус"] and len(table["rows"]) == 1


@pytest.mark.asyncio
async def test_empty_work_is_said_plainly(db, product):
    result = await send(db, product, chip={"label": "Что в работе", "capability": "show_work", "params": {}})
    assert result["messages"][1]["blocks"][0]["text"] == "Сейчас ничего не выполняется."


@pytest.mark.asyncio
async def test_go_to_gives_a_real_link(db, product):
    result = await send(db, product, chip={"label": "Где календарь", "capability": "go_to",
                                           "params": {"target": "calendar"}})
    links = [b for b in result["messages"][1]["blocks"] if b["type"] == "links"][0]["items"]
    assert links[0]["href"] == f"/studio/products/{product.id}/calendar"


# ---- confirmations ----------------------------------------------------------------------

def draft_plan(db, product, minutes=120):
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    plan = {"generated_at": datetime.now(timezone.utc).isoformat(), "approval_state": "draft", "horizon_days": 14,
            "context_hash": "h", "slots": [{"package_key": "p1", "topic": "Тема", "platform": "telegram",
                                             "format": "post", "publish_at": when, "state": "planned"}]}
    row = Artifact(product_id=product.id, kind="editorial_plan", title="План", content=json.dumps(plan))
    db.add(row)
    db.commit()
    return row


@pytest.mark.asyncio
async def test_plan_approval_waits_for_confirmation(db, product, monkeypatch):
    row = draft_plan(db, product)
    approved = []
    import app.content.calendar as calendar
    monkeypatch.setattr(calendar, "approve_plan", lambda db_, settings_, plan_id: approved.append(plan_id))
    result = await send(db, product, chip={"label": "Утвердить контент-план", "capability": "approve_plan", "params": {}})
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    assert approved == [] and card["state"] == "pending" and card["paid"] is False
    outcome = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert approved == [row.id]
    assert outcome["messages"][0]["blocks"][-1]["state"] == "confirmed"
    approval = db.scalar(select(Approval).where(Approval.action_type == "dispatcher:approve_plan"))
    assert approval.status == "approved" and approval.decided_by == "owner" and approval.reason == "via=button"
    # A used card cannot approve twice.
    again = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert again["replayed"] is True and approved == [row.id]


@pytest.mark.asyncio
async def test_declining_a_card_changes_nothing(db, product, monkeypatch):
    draft_plan(db, product)
    import app.content.calendar as calendar
    monkeypatch.setattr(calendar, "approve_plan", lambda *a: pytest.fail("must not approve"))
    result = await send(db, product, chip={"label": "Утвердить", "capability": "approve_plan", "params": {}})
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=False)
    assert list(db.scalars(select(Approval))) == []


@pytest.mark.asyncio
async def test_no_draft_plan_means_nothing_to_confirm(db, product):
    result = await send(db, product, chip={"label": "Утвердить", "capability": "approve_plan", "params": {}})
    assert not any(b["type"] == "confirm" for b in result["messages"][1]["blocks"])


@pytest.mark.asyncio
async def test_video_is_prepared_free_and_started_only_after_confirmation(db, product, monkeypatch):
    chip = {"label": "Видео: тема", "capability": "whiteboard_video", "params": {"topic": "Как работает онлайн-запись"}}
    result = await send(db, product, chip=chip)
    job = tasks(db, "whiteboard_renderer")[0]
    assert job.status == TaskStatus.WAITING_APPROVAL
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    assert card["paid"] is True and any("платная" in line for line in card["details"])
    started = []

    async def fake_approve(db_, product_id, task_id, body, actor, settings_):
        started.append((task_id, body.confirmed, actor))

    import app.api.whiteboard as whiteboard
    monkeypatch.setattr(whiteboard, "_approve", fake_approve)
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert started == [(job.id, True, "owner")]


@pytest.mark.asyncio
async def test_declining_a_paid_video_cancels_the_draft_job(db, product):
    chip = {"label": "Видео: тема", "capability": "whiteboard_video", "params": {"topic": "Как работает онлайн-запись"}}
    result = await send(db, product, chip=chip)
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=False)
    db.expire_all()
    assert tasks(db, "whiteboard_renderer")[0].status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_voice_yes_answers_only_the_latest_open_card(db, product):
    first = await send(db, product, chip={"label": "Видео: один", "capability": "whiteboard_video",
                                          "params": {"topic": "Первое видео про запись"}}, key="a")
    await send(db, product, chip={"label": "Видео: два", "capability": "whiteboard_video",
                                  "params": {"topic": "Второе видео про запись"}}, key="b")
    old = [b for b in first["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    with pytest.raises(PermissionError):
        await service.confirm(db, context(db, product), old["confirmation_id"], accept=True, via="voice")


@pytest.mark.asyncio
async def test_cancel_video_works_without_asking(db, product):
    await send(db, product, chip={"label": "Видео", "capability": "whiteboard_video",
                                  "params": {"topic": "Как работает онлайн-запись"}}, key="v")
    result = await send(db, product, chip={"label": "Отменить видео", "capability": "cancel_video", "params": {}}, key="c")
    assert result["messages"][1]["blocks"][0]["text"] == "Видео отменено."
    db.expire_all()
    assert tasks(db, "whiteboard_renderer")[0].status == TaskStatus.CANCELLED


# ---- isolation --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accounts_have_separate_conversations_and_cannot_confirm_each_others_cards(db, product):
    chip = {"label": "Видео", "capability": "whiteboard_video", "params": {"topic": "Как работает онлайн-запись"}}
    mine = await send(db, product, chip=chip, account="owner")
    card = [b for b in mine["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    with pytest.raises(LookupError):
        await service.confirm(db, context(db, product, "other"), card["confirmation_id"], accept=True)
    other = service.open_conversation(db, product.id, "other")
    assert service.messages(db, other) == []


@pytest.mark.asyncio
async def test_other_company_cannot_be_reached_through_task_id(db, product):
    other = Product(name="Другая", slug="other")
    db.add(other)
    db.commit()
    await send(db, other, chip={"label": "Видео", "capability": "whiteboard_video",
                                "params": {"topic": "Видео чужой компании про запись"}}, key="o")
    foreign = tasks(db, "whiteboard_renderer")[0]
    result = await send(db, product, chip={"label": "Отмена", "capability": "cancel_video",
                                           "params": {"task_id": str(foreign.id)}})
    assert result["messages"][1]["blocks"][0]["text"] == "Не нашёл такое видео."
    db.expire_all()
    assert foreign.status == TaskStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_new_topic_starts_a_fresh_conversation(db, product):
    await send(db, product, chip=post_chip(), key="one")
    old = service.open_conversation(db, product.id, "owner")
    fresh = service.open_conversation(db, product.id, "owner", fresh=True)
    assert fresh.id != old.id and service.messages(db, fresh) == []
    assert service.open_conversation(db, product.id, "owner").id == fresh.id
