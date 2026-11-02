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
        if decision == Verdict.CONFIRM_FIRST or (capability.always_confirm and decision == Verdict.RUN):
            assert capability.id in executors.DESCRIBERS and capability.id in executors.CONFIRMERS
        else:
            assert capability.id in executors.EXECUTORS




def test_voice_story_counts_keep_requested_number():
    from app.dispatcher.fast_routes import requested_story_count
    assert requested_story_count("Сделай четыре сторис про летний уход") == 4
    assert requested_story_count("Подготовь 5 историй про волосы") == 5
    assert requested_story_count("Сделай сторис из шести экранов") == 6
    assert requested_story_count("Подготовь сторис на завтра") is None


@pytest.mark.asyncio
async def test_stories_require_confirmation_and_keep_exact_count(db, product):
    chip = {"label": "Пять сторис про услуги", "capability": "create_creative",
            "params": {"topic": "Как выбрать услугу", "format": "story",
                       "frame_count": 5}}
    result = await send(db, product, chip=chip)
    answer = result["messages"][1]
    confirm_block = next(block for block in answer["blocks"] if block["type"] == "confirm")
    assert confirm_block["confirmation_id"]
    assert "5" in str(confirm_block["details"])
    assert not tasks(db, "content_creative")
    confirmed = await service.confirm(db, context(db, product),
                                      confirm_block["confirmation_id"], accept=True)
    created = tasks(db, "content_creative")
    assert len(created) == 1
    from app.models.domain import Artifact
    item = db.scalar(select(Artifact).where(Artifact.task_id == created[0].id,
                                             Artifact.kind == "creative_request"))
    assert item is not None
    assert json.loads(item.content)["frame_count"] == 5
    assert any(b["type"] == "task" for b in confirmed["messages"][-1]["blocks"])




# ---- inline editing of a pending create card ---------------------------------------------

@pytest.mark.asyncio
async def test_edit_stories_card_updates_saved_job_without_creating_work(db, product):
    from app.dispatcher.editable import EditCreation
    result = await send(db, product, chip={
        "label": "Четыре Stories", "capability": "create_creative",
        "params": {"topic": "Уход за волосами", "format": "story", "frame_count": 4},
    })
    original = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    assert original["editable"]["frame_count"] == 4 and original["revision"] == 0
    assert tasks(db, "content_creative") == []
    updated = await service.edit_confirmation(db, context(db, product), EditCreation(
        confirmation_id=original["confirmation_id"], revision=0, format="story",
        topic="Топ-6 ошибок ухода за волосами", frame_count=6, platform="vk"))
    card = next(b for b in updated["messages"][0]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["frame_count"] == 6
    assert card["editable"]["topic"] == "Топ-6 ошибок ухода за волосами"
    assert card["editable"]["platform"] == "vk"
    assert card["revision"] == 1
    assert tasks(db, "content_creative") == []
    with pytest.raises(PermissionError):
        await service.edit_confirmation(db, context(db, product), EditCreation(
            confirmation_id=original["confirmation_id"], revision=0, format="story",
            topic="Новая тема", frame_count=5))
    confirmed = await service.confirm(db, context(db, product),
                                      original["confirmation_id"], accept=True, revision=1)
    task = tasks(db, "content_creative")[0]
    source = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request"))
    payload = json.loads(source.content)
    assert payload["topic"] == "Топ-6 ошибок ухода за волосами"
    assert payload["frame_count"] == 6 and payload["platform"] == "vk"
    assert any(block["type"] == "task" for block in confirmed["messages"][-1]["blocks"])
    with pytest.raises(PermissionError):
        await service.edit_confirmation(db, context(db, product), EditCreation(
            confirmation_id=original["confirmation_id"], revision=1,
            format="story", topic="Слишком поздно"))


@pytest.mark.asyncio
async def test_edit_can_change_stories_into_post_and_preserves_company_isolation(db, product):
    from app.dispatcher.editable import EditCreation
    result = await send(db, product, chip={
        "label": "Stories", "capability": "create_creative",
        "params": {"topic": "Как выбрать услугу", "format": "story", "frame_count": 5}})
    block = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    params = dict(confirmation_id=block["confirmation_id"], revision=0,
                  format="post", topic="Новый пост про услуги", platform="vk")
    with pytest.raises(LookupError):
        await service.edit_confirmation(db, context(db, product, "other"),
                                        EditCreation(**params))
    outcome = await service.edit_confirmation(db, context(db, product),
                                               EditCreation(**params))
    edit = next(b for b in outcome["messages"][0]["blocks"] if b["type"] == "confirm")
    assert edit["editable"]["format"] == "post"
    assert edit["editable"]["frame_count"] is None
    assert edit["editable"]["platform"] == "vk"
    await service.confirm(db, context(db, product), block["confirmation_id"],
                          accept=True, revision=1)
    assert tasks(db, "content_creative") == []
    assert len(tasks(db, "content_factory")) == 1


def test_inline_edit_contract_rejects_bad_format_combinations():
    from app.dispatcher.editable import EditCreation
    from pydantic import ValidationError
    data = {"confirmation_id": "x", "revision": 0, "format": "post", "topic": "Тема"}
    with pytest.raises(ValidationError):
        EditCreation(**data, frame_count=4)
    with pytest.raises(ValidationError):
        EditCreation(**{**data, "format": "story", "platform": "vc_ru"})
    with pytest.raises(ValidationError):
        EditCreation(**{**data, "format": "story", "frame_count": 11})
    with pytest.raises(ValidationError):
        EditCreation(**{**data, "topic": "x"})


@pytest.mark.asyncio
async def test_stale_confirmation_cannot_start_edited_request(db, product):
    from app.dispatcher.editable import EditCreation
    original = await send(db, product, chip={
        "label": "4 Stories", "capability": "create_creative",
        "params": {"topic": "Уход за волосами", "format": "story", "frame_count": 4}})
    block = next(x for x in original["messages"][1]["blocks"] if x["type"] == "confirm")
    await service.edit_confirmation(db, context(db, product), EditCreation(
        confirmation_id=block["confirmation_id"], revision=0, format="story",
        topic="Шесть советов", frame_count=6))
    with pytest.raises(PermissionError):
        await service.confirm(db, context(db, product), block["confirmation_id"],
                              accept=True, revision=0)
    with pytest.raises(PermissionError):
        await service.confirm(db, context(db, product), block["confirmation_id"],
                              accept=True)
    assert tasks(db, "content_creative") == []
    await service.confirm(db, context(db, product), block["confirmation_id"],
                          accept=True, revision=1)
    assert len(tasks(db, "content_creative")) == 1




@pytest.mark.asyncio
async def test_edit_preserves_unmodified_audience_context(db, product):
    from app.dispatcher.editable import EditCreation
    from uuid import UUID
    result = await send(db, product, chip={
        "label": "Stories for parents", "capability": "create_creative",
        "params": {"topic": "Советы для родителей", "format": "story",
                   "frame_count": "4", "audience": "Родители дошкольников"}})
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    updated = await service.edit_confirmation(db, context(db, product), EditCreation(
        confirmation_id=card["confirmation_id"], revision=0, format="story",
        topic="Пять советов", frame_count=5))
    stored = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.id == UUID(result["messages"][1]["id"])))
    assert stored.params["audience"] == "Родители дошкольников"
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True, revision=1)
    task = tasks(db, "content_creative")[0]
    artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request"))
    assert json.loads(artifact.content)["audience"] == "Родители дошкольников"



# ---- unified Create: dispatcher intent + optional manual details -------------------------

@pytest.mark.asyncio
async def test_create_options_merge_with_voice_and_survive_confirmation(db, product):
    from app.dispatcher.editable import CreateHints
    model, _ = llm({"intent": "execute", "capability": "create_creative", "confidence": 0.95,
                    "params": {"format": "story", "topic": "Как ухаживать за волосами"}})
    request = CreateHints(format="story", platform="vk", frame_count=6,
                          audience="Клиенты салона красоты",
                          editorial_brief="Не обещать медицинских результатов")
    response = await service.handle_message(
        db, context(db, product), model, text="Сделай четыре сторис про уход",
        client_key="voice-details", creation=request)
    card = next(b for b in response["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "story"
    assert card["editable"]["frame_count"] == 4  # explicit voice wins over dropdown
    assert card["editable"]["platform"] == "vk"
    assert card["editable"]["audience"] == "Клиенты салона красоты"
    assert card["editable"]["editorial_brief"] == "Не обещать медицинских результатов"
    assert tasks(db, "content_creative") == []
    await service.confirm(db, context(db, product), card["confirmation_id"],
                          accept=True, revision=0)
    job = tasks(db, "content_creative")[0]
    source = db.scalar(select(Artifact).where(
        Artifact.task_id == job.id, Artifact.kind == "creative_request"))
    request_data = json.loads(source.content)
    assert request_data["frame_count"] == 4 and request_data["platform"] == "vk"
    assert request_data["audience"] == "Клиенты салона красоты"
    assert request_data["editorial_brief"] == "Не обещать медицинских результатов"


@pytest.mark.asyncio
async def test_manual_story_selection_cannot_override_explicit_post(db, product):
    from app.dispatcher.editable import CreateHints
    model, _ = llm({"intent": "execute", "capability": "create_post", "confidence": 0.96,
                    "params": {"topic": "Как выбрать услугу"}})
    result = await service.handle_message(
        db, context(db, product), model, text="Напиши пост про услуги",
        client_key="format-override", creation=CreateHints(
            format="story", platform="vk", frame_count=6,
            audience="Новые клиенты"))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "post"
    assert card["editable"]["platform"] == "telegram"
    assert card["editable"]["audience"] == "Новые клиенты"
    assert card["editable"]["frame_count"] is None
    assert tasks(db, "content_factory") == []


@pytest.mark.asyncio
async def test_spoken_platform_overrides_existing_dropdown(db, product):
    from app.dispatcher.editable import CreateHints
    model, _ = llm({"intent": "execute", "capability": "create_creative", "confidence": 0.96,
                    "params": {"topic": "Пять советов", "format": "story", "platform": "telegram"}})
    result = await service.handle_message(
        db, context(db, product), model, text="Сделай пять сторис в Телеграм",
        client_key="platform-override", creation=CreateHints(
            format="story", platform="vk", frame_count=7))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["platform"] == "telegram"
    assert card["editable"]["frame_count"] == 5


@pytest.mark.asyncio
async def test_full_package_keeps_audience_and_editorial_brief(db, product):
    from app.dispatcher.editable import CreateHints
    model, _ = llm({"intent": "execute", "capability": "create_package", "confidence": 0.94,
                    "params": {"topic": "Онлайн-запись для салонов"}})
    result = await service.handle_message(
        db, context(db, product), model, text="Сделай полный пакет про запись",
        client_key="package-details", creation=CreateHints(
            format="package", audience="Владельцы салонов", editorial_brief="Тон экспертный"))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["audience"] == "Владельцы салонов"
    await service.confirm(db, context(db, product), card["confirmation_id"],
                          accept=True, revision=0)
    job = tasks(db, "content_factory")[0]
    source = db.scalar(select(Artifact).where(
        Artifact.task_id == job.id, Artifact.kind == "content_request"))
    request_data = json.loads(source.content)
    assert request_data["audience"] == "Владельцы салонов"
    assert request_data["editorial_brief"] == "Тон экспертный"


def test_create_details_validation_and_edit_clearing():
    from app.dispatcher.editable import CreateHints, EditCreation
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateHints(format="story", platform="youtube_shorts")
    with pytest.raises(ValidationError):
        CreateHints(format="post", frame_count=5)
    with pytest.raises(ValidationError):
        CreateHints(platform="telegram")
    with pytest.raises(ValidationError):
        CreateHints(format="story", frame_count=11)
    from app.dispatcher.editable import target_for_edit
    cap, params = target_for_edit(EditCreation(
        confirmation_id="sample", revision=0, format="story", topic="Новая тема",
        frame_count=4, audience="", editorial_brief=""),
        existing={"audience": "Старая аудитория", "editorial_brief": "Старые требования"})
    assert cap == "create_creative"
    assert params.audience is None and params.editorial_brief is None



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
    assert tasks(db, "content_factory") == []
    user, answer = result["messages"]
    assert user["role"] == "user" and answer["role"] == "assistant"
    card = next(block for block in answer["blocks"] if block["type"] == "confirm")
    assert card["editable"]["format"] == "post"
    assert card["editable"]["topic"] == "Онлайн-запись без звонков"
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    created = tasks(db, "content_factory")
    assert len(created) == 1 and created[0].status == TaskStatus.QUEUED
    call = db.scalar(select(ToolCall).where(ToolCall.operation == "create_post"))
    assert call is not None and call.task_id == created[0].id and call.status == "executed"


@pytest.mark.asyncio
async def test_retry_with_same_key_does_not_repeat_the_action(db, product):
    first = await send(db, product, chip=post_chip(), key="same")
    again = await send(db, product, chip=post_chip(), key="same")
    assert again["replayed"] is True
    assert tasks(db, "content_factory") == []
    assert [m["id"] for m in again["messages"]] == [m["id"] for m in first["messages"]]
    assert db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant").limit(1)) is not None
    assert list(db.scalars(select(ToolCall))) == []
    card = next(b for b in first["messages"][1]["blocks"] if b["type"] == "confirm")
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert len(tasks(db, "content_factory")) == 1
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
    assert tasks(db, "content_factory") == []
    assert any(b["type"] == "confirm" for b in result["messages"][1]["blocks"])
    request = provider.last_request
    assert request.metadata["team_role"] == "dispatcher"
    assert "create_post" in request.messages[-1].content
    cost = db.scalar(select(CostRecord).where(CostRecord.details["task_type"].as_string() == "dispatcher"))
    assert cost is not None and cost.input_tokens == 120 and cost.product_id == product.id
    stored = db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant"))
    assert stored.capability == "create_post" and stored.confidence == pytest.approx(0.93)
    assert stored.model == "fake-dispatcher" and stored.task_id is None
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
async def test_schedule_without_a_linked_business_is_said_plainly(db, product):
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


@pytest.mark.asyncio
async def test_two_devices_writing_at_once_do_not_break_the_thread(db, product, monkeypatch):
    """Another phone took the next number between our read and our write: we take the following one."""
    from app.dispatcher import service as dispatcher_service
    from app.dispatcher.models import DispatcherMessage
    await send(db, product, chip={"label": "Что в работе", "capability": "show_work", "params": {}}, key="a")
    real = dispatcher_service._next_seq
    clashes = {"left": 1}

    def stale(db_, conversation):
        if clashes["left"]:
            clashes["left"] -= 1
            return 1  # already taken
        return real(db_, conversation)
    monkeypatch.setattr(dispatcher_service, "_next_seq", stale)
    result = await send(db, product, chip={"label": "Что в работе", "capability": "show_work", "params": {}}, key="b")
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    seqs = [m.seq for m in db.scalars(select(DispatcherMessage).order_by(DispatcherMessage.seq))]
    assert seqs == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_plan_times_are_shown_in_the_plans_time_zone(db, product, monkeypatch):
    from datetime import datetime, timedelta, timezone as tz
    from app.dispatcher import executors
    when = (datetime.now(tz.utc) + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    plan = {"approval_state": "active", "timezone": "Europe/Moscow",
            "slots": [{"publish_at": when.isoformat(), "platform": "telegram", "topic": "Тема", "state": "planned"}]}
    monkeypatch.setattr(executors, "plans", lambda db_, pid: ((None, plan), None))
    outcome = await executors.show_plan(context(db, product), None)
    table = next(b for b in outcome.blocks if b["type"] == "table")
    assert table["rows"][0][0].endswith("10:00")  # 07:00 UTC is 10:00 in Moscow
    plan["timezone"] = "Nowhere/Invalid"
    outcome = await executors.show_plan(context(db, product), None)
    assert next(b for b in outcome.blocks if b["type"] == "table")["rows"][0][0].endswith("10:00")


@pytest.mark.asyncio
async def test_model_sees_only_the_latest_twenty_messages(db, product):
    from app.dispatcher import service as dispatcher_service
    for index in range(15):  # 30 stored messages
        await send(db, product, chip={"label": f"Что в работе {index}", "capability": "show_work", "params": {}},
                   key=f"c{index}")
    model, provider = llm({"intent": "answer", "confidence": 0.9, "reply_text": "Ок"})
    await send(db, product, text="что нового?", key="t", model=model)
    sent = provider.requests[-1].messages
    history = sent[:-1]  # the last one is the current request with the company snapshot
    assert len(history) <= dispatcher_service.HISTORY_FOR_MODEL and history[0].role == "user"
    assert "Что в работе 14" in [m.content for m in history] and "Что в работе 0" not in [m.content for m in history]
    stored = await send(db, product, text="", chip={"label": "Что в работе", "capability": "show_work", "params": {}}, key="z")
    assert len(dispatcher_service.messages(db, dispatcher_service.open_conversation(db, product.id, "owner"))) == 34


# ---- video length and closing call to action ---------------------------------------------

def _video_request(db):
    from app.content.whiteboard.jobs import records
    job = tasks(db, "whiteboard_renderer")[0]
    return job, records(db, job)[3]


@pytest.mark.asyncio
async def test_voice_request_for_a_minute_video_with_own_call(db, product):
    model, provider = llm({"intent": "execute", "capability": "whiteboard_video", "confidence": 0.9,
                           "params": {"topic": "Почему кошки видят в темноте", "length": "60",
                                      "call_to_action": "Напишите, про каких животных рассказать"},
                           "reply_text": "Готовлю видео.", "spoken": "Готовлю видео."})
    result = await send(db, product, text="Сделай минутное видео про кошачье зрение, в конце попроси "
                                          "написать, про каких животных рассказать", model=model)
    catalog = provider.requests[0].messages[-1].content
    assert '"length"' in catalog and '"call_to_action"' in catalog
    job, payload = _video_request(db)
    assert job.status == TaskStatus.WAITING_APPROVAL
    assert payload.request.scenes == 6 and payload.request.duration_seconds == 60
    assert payload.request.call_to_action == "Напишите, про каких животных рассказать"
    assert payload.max_api_calls == 26
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    assert "Длина: около минуты, 6 сцен." in card["details"]
    assert any("Напишите, про каких животных" in line for line in card["details"])
    assert any("до 26 обращений" in line for line in card["details"])


@pytest.mark.asyncio
async def test_video_chip_defaults_to_thirty_seconds_and_subscribe_call(db, product):
    chip = {"label": "Видео: тема", "capability": "whiteboard_video", "params": {"topic": "Как работает онлайн-запись"}}
    result = await send(db, product, chip=chip)
    _, payload = _video_request(db)
    assert payload.request.scenes == 3 and payload.request.call_to_action == "" and payload.max_api_calls == 14
    card = [b for b in result["messages"][1]["blocks"] if b["type"] == "confirm"][0]
    assert "Длина: около 30 секунд, 3 сцены." in card["details"]
    assert "Призыв в конце: подписаться на канал и поставить лайк." in card["details"]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"length": "45"}, {"call_to_action": "Да"}])
async def test_bad_video_length_or_call_asks_to_clarify_and_creates_nothing(db, product, params):
    chip = {"label": "Видео", "capability": "whiteboard_video",
            "params": {"topic": "Как работает онлайн-запись", **params}}
    result = await send(db, product, chip=chip)
    assert result["messages"][1]["blocks"][0]["text"].startswith("Уточните")
    assert tasks(db, "whiteboard_renderer") == []



@pytest.mark.asyncio
async def test_review_shows_typed_video_resource_not_truncated_duplicate_links(db, product):
    from app.models.domain import ArtifactStatus
    task = Task(product_id=product.id, title="WHITEBOARD",
                assigned_agent="whiteboard_renderer", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    topic = "Горячий предмет тяжелее холодного — полное научное объяснение"
    video = Artifact(
        product_id=product.id, task_id=task.id, kind="whiteboard_video",
        title="Рисованное видео: " + topic,
        status=ArtifactStatus.AWAITING_APPROVAL, content="{}",
    )
    post = Artifact(
        product_id=product.id, kind="channel_text", title="Пост о записи",
        status=ArtifactStatus.AWAITING_APPROVAL,
        content='{"channel":"telegram","body":"Черновик поста"}',
    )
    db.add_all([video, post])
    db.commit()
    result = await send(db, product, chip={"label":"Проверить готовое",
                                             "capability":"show_review","params":{}})
    answer = result["messages"][1]
    resources = [b for b in answer["blocks"] if b["type"] == "resource_list"]
    assert len(resources) == 1
    card = resources[0]["items"][0]
    assert card["kind"] == "video"
    assert card["title"] == topic
    assert card["status"] == "Готово · проверить"
    assert card["href"] == f"/studio/products/{product.id}/whiteboard/{task.id}"
    assert not [b for b in answer["blocks"] if b["type"] == "links"]
    assert len([b for b in answer["blocks"] if b["type"] == "material"]) == 1
    assert any(chip["capability"] == "approve_material"
               for block in answer["blocks"] if block["type"] == "chips"
               for chip in block["items"])


@pytest.mark.asyncio
async def test_review_many_videos_is_compact_and_keeps_full_review_route(db, product):
    from app.models.domain import ArtifactStatus
    for idx in range(12):
        task = Task(product_id=product.id, title=f"WHITEBOARD: {idx}",
                    assigned_agent="whiteboard_renderer", status=TaskStatus.COMPLETED)
        db.add(task)
        db.flush()
        db.add(Artifact(
            product_id=product.id, task_id=task.id, kind="whiteboard_video",
            title=f"Рисованное видео: Проверка темы {idx}", content="{}",
            status=ArtifactStatus.AWAITING_APPROVAL,
        ))
    db.commit()
    result = await send(db, product, chip={"label":"Проверить готовое",
                                             "capability":"show_review","params":{}})
    answer = result["messages"][1]
    resources = [b for b in answer["blocks"] if b["type"] == "resource_list"]
    assert len(resources) == 1
    assert len(resources[0]["items"]) == 8
    assert all(item["kind"] == "video" for item in resources[0]["items"])
    assert all(item["title"].startswith("Проверка темы ") for item in resources[0]["items"])
    links = [b for b in answer["blocks"] if b["type"] == "links"]
    assert len(links) == 1
    assert links[0]["items"][0]["href"] == f"/studio/products/{product.id}/review"


@pytest.mark.asyncio
async def test_illustrated_post_is_not_sent_to_text_only_generator(db, product):
    model, _ = llm({
        "intent": "execute", "capability": "create_post", "confidence": 0.98,
        "params": {"topic": "Почему стоит записываться онлайн", "channel": "vk"},
    })
    result = await send(db, product, text="Напиши пост с картинкой про онлайн-запись",
                        model=model, key="illustrated-post")
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "static_image"
    assert card["editable"]["platform"] == "vk"
    assert card["editable"]["image_model"] == "auto"
    assert card["editable"]["aspect_ratio"] is None
    assert "полноценный текст поста" in card["editable"]["editorial_brief"]
    assert tasks(db, "content_factory") == []
    await service.confirm(db, context(db, product),
                          card["confirmation_id"], accept=True, revision=0)
    created = tasks(db, "content_creative")
    assert len(created) == 1
    payload = db.scalar(select(Artifact).where(
        Artifact.task_id == created[0].id, Artifact.kind == "creative_request"))
    data = json.loads(payload.content)
    assert data["format"] == "static_image" and data["image_model"] == "auto"
    assert data["platform"] == "vk"


@pytest.mark.asyncio
async def test_plain_post_does_not_generate_an_image(db, product):
    model, _ = llm({
        "intent": "execute", "capability": "create_post", "confidence": 0.98,
        "params": {"topic": "Онлайн-запись без звонков", "channel": "telegram"},
    })
    result = await send(db, product, text="Напиши пост про онлайн-запись",
                        model=model, key="plain-post")
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "post"


def test_editing_story_aspect_and_platform_auto_defaults():
    from app.dispatcher.editable import EditCreation, CreateHints, draft_view, target_for_edit
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EditCreation(confirmation_id="x", revision=0, format="story",
                     topic="Четыре совета", aspect_ratio="4:5")
    with pytest.raises(ValidationError):
        CreateHints(format="story", aspect_ratio="1:1")
    _, edited = target_for_edit(EditCreation(
        confirmation_id="x", revision=0, format="static_image",
        topic="Баннер в соцсеть", platform="instagram_reels",
        image_model="gemini", aspect_ratio=None),
        existing={"format": "story", "aspect_ratio": "9:16"})
    assert edited.aspect_ratio is None  # platform chooses 4:5
    view = draft_view("create_creative", edited.model_dump(exclude_none=True))
    assert view["aspect_ratio"] is None
    assert view["platform"] == "instagram_reels"
