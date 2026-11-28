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



# ---- selected Create format is an actual routing instruction, not an LLM guess ---------


@pytest.mark.asyncio
async def test_selected_vk_post_topic_routes_without_classifier_and_waits_for_confirmation(db, product):
    from app.dispatcher.editable import CreateHints

    # This is the exact topic from the owner screenshot. A missing/broken LLM
    # cannot turn an explicitly selected format into an unsupported request.
    topic = "Зачем мне CRM?"
    response = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="selected-crm-post", creation=CreateHints(
            format="post", platform="vk", topic=topic))
    assistant = response["messages"][-1]
    assert assistant["intent"] == "execute"
    assert "Этого я пока не умею" not in assistant["text"]
    confirmation = next(b for b in assistant["blocks"] if b["type"] == "confirm")
    assert confirmation["editable"]["format"] == "post"
    assert confirmation["editable"]["topic"] == topic
    assert confirmation["editable"]["platform"] == "vk"
    assert tasks(db, "content_factory") == []

    # The normal confirmation workflow remains mandatory.
    await service.confirm(db, context(db, product),
                          confirmation["confirmation_id"], accept=True, revision=0)
    jobs = tasks(db, "content_factory")
    assert len(jobs) == 1
    request = db.scalar(select(Artifact).where(
        Artifact.task_id == jobs[0].id, Artifact.kind == "content_request"))
    assert json.loads(request.content)["topic"] == topic
    assert json.loads(request.content)["channel"] == "vk"


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,capability,platform", [
    ("post", "create_post", "telegram"),
    ("story", "create_creative", "vk"),
    ("short_video", "create_creative", "vk_clips"),
    ("static_image", "create_creative", "telegram"),
    ("package", "create_package", None),
])
async def test_selected_create_subject_never_needs_llm(db, product, fmt, capability, platform):
    from app.dispatcher.editable import CreateHints

    selected = CreateHints(format=fmt, topic="Как привлечь больше клиентов",
                           platform=platform, editorial_brief="Без выдуманных обещаний")
    result = await service.handle_message(
        db, context(db, product), None, text=selected.topic,
        client_key="format-" + fmt, creation=selected)
    answer = result["messages"][-1]
    assert answer["intent"] == "execute"
    card = next(b for b in answer["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == fmt
    assert card["editable"]["topic"] == selected.topic
    assert tasks(db, "content_factory") == []
    assert tasks(db, "content_creative") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,platform,agent,artifact_kind", [
    ("post", "vk", "content_factory", "content_request"),
    ("story", "vk", "content_creative", "creative_request"),
    ("short_video", "vk_clips", "content_creative", "creative_request"),
    ("static_image", "instagram_reels", "content_creative", "creative_request"),
    ("package", None, "content_factory", "content_request"),
])
async def test_all_create_formats_queue_only_after_user_approval(
        db, product, fmt, platform, agent, artifact_kind):
    """The same bare Create topic works for every non-paid material type."""
    from app.dispatcher.editable import CreateHints

    topic = "Почему теряются заявки в маленьком бизнесе"
    preferences = {"format": fmt, "platform": platform, "topic": topic,
                   "audience": "Владельцы бизнеса",
                   "editorial_brief": "Без неподтверждённых обещаний"}
    if fmt == "story":
        preferences.update(frame_count=4, image_model="auto", aspect_ratio="9:16")
    if fmt == "static_image":
        preferences.update(image_model="gemini", aspect_ratio="4:5")

    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="approval-" + fmt, creation=CreateHints(**preferences))
    answer = result["messages"][-1]
    assert answer["intent"] == "execute"
    confirmation = next(block for block in answer["blocks"] if block["type"] == "confirm")
    assert confirmation["editable"]["format"] == fmt
    assert confirmation["editable"]["topic"] == topic
    assert confirmation["editable"]["audience"] == "Владельцы бизнеса"
    assert tasks(db, agent) == []  # no paid work, generation, or publishing
    if fmt == "story":
        assert confirmation["editable"]["frame_count"] == 4
        assert confirmation["editable"]["image_model"] == "auto"
    if fmt == "static_image":
        assert confirmation["editable"]["aspect_ratio"] == "4:5"
        assert confirmation["editable"]["image_model"] == "gemini"

    await service.confirm(
        db, context(db, product), confirmation["confirmation_id"],
        accept=True, revision=0)
    job = tasks(db, agent)[0]
    source = db.scalar(select(Artifact).where(
        Artifact.task_id == job.id, Artifact.kind == artifact_kind))
    request = json.loads(source.content)
    assert request["topic"] == topic
    assert request["audience"] == "Владельцы бизнеса"
    assert request["editorial_brief"] == "Без неподтверждённых обещаний"
    if fmt == "post":
        assert request["channel"] == platform
    elif fmt in {"story", "short_video", "static_image"}:
        assert request["format"] == fmt
        assert request["platform"] == platform
        if fmt == "story":
            assert request["frame_count"] == 4
        if fmt == "static_image":
            assert request["aspect_ratio"] == "4:5"
            assert request["image_model"] == "gemini"


@pytest.mark.asyncio
async def test_create_story_subject_does_not_override_selected_frame_count(db, product):
    from app.dispatcher.editable import CreateHints

    topic = "Как придумать 10 историй для клиентов"
    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="subject-mentions-ten-stories", creation=CreateHints(
            format="story", platform="vk", frame_count=4, topic=topic))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "story"
    assert card["editable"]["frame_count"] == 4
    assert card["editable"]["platform"] == "vk"


@pytest.mark.asyncio
async def test_create_image_subject_does_not_override_selected_ratio_or_platform(db, product):
    from app.dispatcher.editable import CreateHints

    topic = "Почему вертикальное видео во ВКонтакте привлекает клиентов"
    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="subject-mentions-vk-vertical", creation=CreateHints(
            format="static_image", topic=topic, platform="telegram",
            aspect_ratio="1:1", image_model="auto"))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "static_image"
    assert card["editable"]["platform"] == "telegram"
    assert card["editable"]["aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_create_post_subject_about_illustrated_posts_remains_selected_post(db, product):
    from app.dispatcher.editable import CreateHints

    topic = "Почему посты с картинками лучше продают"
    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="subject-illustrated-posts", creation=CreateHints(
            format="post", topic=topic, platform="vk"))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["editable"]["format"] == "post"
    assert card["editable"]["platform"] == "vk"
    assert card["editable"]["topic"] == topic


@pytest.mark.asyncio
async def test_simple_video_selected_in_create_uses_paid_approval_without_llm(db, product):
    from app.dispatcher.editable import CreateHints

    topic = "Как CRM помогает не потерять заявки"
    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="whiteboard-create", creation=CreateHints(
            format="whiteboard", topic=topic))
    answer = result["messages"][-1]
    assert answer["intent"] == "execute"
    card = next(block for block in answer["blocks"] if block["type"] == "confirm")
    assert card["paid"] is True
    assert any("платная" in line.lower() for line in card["details"])
    assert tasks(db, "content_factory") == []
    assert tasks(db, "content_creative") == []
    videos = tasks(db, "whiteboard_renderer")
    assert len(videos) == 1
    assert videos[0].status == TaskStatus.WAITING_APPROVAL
    await service.confirm(
        db, context(db, product), card["confirmation_id"],
        accept=False)
    db.expire_all()
    assert videos[0].status == TaskStatus.CANCELLED


def test_create_whiteboard_short_topic_rejected_before_dispatch(db, product):
    from app.dispatcher.editable import CreateHints
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="минимум 8 символов"):
        CreateHints(format="whiteboard", topic="CRM?")
    assert tasks(db, "whiteboard_renderer") == []


def test_create_topic_limits_apply_consistently_to_all_formats():
    from app.dispatcher.editable import CreateHints
    from pydantic import ValidationError

    for fmt in ("post", "story", "short_video", "static_image", "package"):
        assert CreateHints(format=fmt, topic="CRM").topic == "CRM"
        with pytest.raises(ValidationError):
            CreateHints(format=fmt, topic="ab")
        with pytest.raises(ValidationError):
            CreateHints(format=fmt, topic="а" * 301)
    assert CreateHints(format="whiteboard", topic="Про CRM?").topic == "Про CRM?"
    with pytest.raises(ValidationError):
        CreateHints(format="whiteboard", topic="а" * 301)


def test_create_whiteboard_cannot_accept_unrelated_image_and_platform_options():
    from app.dispatcher.editable import CreateHints
    from pydantic import ValidationError

    for data in ({"platform": "vk"},
                 {"frame_count": 4},
                 {"image_model": "openai"},
                 {"aspect_ratio": "4:5"}):
        with pytest.raises(ValidationError):
            CreateHints(format="whiteboard", topic="Как увеличить повторные продажи", **data)


@pytest.mark.asyncio
async def test_topic_hint_cannot_override_different_text_or_launch_work(db, product):
    from app.dispatcher.editable import CreateHints

    result = await service.handle_message(
        db, context(db, product), None, text="Покажи мои материалы",
        client_key="not-a-create-command", creation=CreateHints(
            format="post", platform="vk", topic="Зачем мне CRM?"))
    assert "Этого я пока не умею" not in result["messages"][-1]["text"]
    assert not any(b["type"] == "confirm" for b in result["messages"][-1]["blocks"])
    assert tasks(db, "content_factory") == []


def test_selected_create_topic_requires_format_and_is_bounded():
    from pydantic import ValidationError
    from app.dispatcher.editable import CreateHints

    with pytest.raises(ValidationError):
        CreateHints(topic="Зачем мне CRM?")
    with pytest.raises(ValidationError):
        CreateHints(format="post", topic="x" * 301)


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



# ---- bounded multi-step dispatcher: safe read-only batch --------------------------------

@pytest.mark.asyncio
async def test_readonly_plan_is_sequential_persisted_and_replay_safe(db, product):
    model, provider = llm({
        "intent": "plan", "confidence": 0.97, "reply_text": "Уже всё сделал",
        "steps": [
            {"capability": "show_work", "params": {}},
            {"capability": "show_plan", "params": {}},
        ],
    })
    result = await send(db, product, text="Покажи задания и контент-план", model=model, key="plan-1")
    answer = result["messages"][1]
    assert answer["intent"] == "plan"
    assert "Уже всё сделал" not in answer["text"]  # server owns truthful status
    workflow = next(b for b in answer["blocks"] if b["type"] == "workflow")
    assert len(workflow["steps"]) == 2
    assert [s["status"] for s in workflow["steps"]] == ["completed", "completed"]
    assert [s["title"] for s in workflow["steps"]] == ["Что в работе", "Показать контент-план"]
    assert len(list(db.scalars(select(ToolCall)))) == 0
    assert len(list(db.scalars(select(Task)))) == 0
    replay = await send(db, product, text="Покажи задания и контент-план", model=model, key="plan-1")
    assert replay["replayed"] is True
    assert replay["messages"][1]["blocks"] == answer["blocks"]
    assert len(list(db.scalars(select(DispatcherMessage)))) == 2
    assert "plan" in provider.last_request.messages[-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [
    [{"capability": "show_work"}, {"capability": "create_creative", "params": {"topic": "Салон", "format": "story"}}],
    [{"capability": "show_work"}, {"capability": "set_insights_depth", "params": {"days": "30"}}],
    [{"capability": "show_work"}, {"capability": "erase_database"}],
    [{"capability": "show_work"}, {"capability": "show_work"}],
    [{"capability": "show_work"}],
    [{"capability": "go_to", "params": {"target": "nonexistent"}}, {"capability": "show_work"}],
])
async def test_readonly_plan_preflight_rejects_every_unsafe_or_incomplete_batch(db, product, steps):
    model, _ = llm({"intent": "plan", "confidence": 0.99, "steps": steps})
    response = await send(db, product, text="Сделай несколько действий", model=model, key="unsafe-" + str(len(steps)))
    assistant = response["messages"][1]
    assert assistant["intent"] == "clarify"
    assert not any(b["type"] == "workflow" for b in assistant["blocks"])
    assert not list(db.scalars(select(Task)))
    assert not list(db.scalars(select(ToolCall)))


@pytest.mark.asyncio
async def test_readonly_plan_stops_after_failed_step_without_running_rest(db, product, monkeypatch):
    from app.dispatcher.outcome import Outcome
    seen = []
    async def first(ctx, params):
        seen.append("first")
        return Outcome("Недоступно.", ok=False)
    async def second(ctx, params):
        seen.append("second")
        return Outcome("Никогда.")
    monkeypatch.setitem(executors.EXECUTORS, "show_work", first)
    monkeypatch.setitem(executors.EXECUTORS, "show_plan", second)
    model, _ = llm({"intent": "plan", "confidence": 0.9, "steps": [
        {"capability": "show_work"}, {"capability": "show_plan"}]})
    result = await send(db, product, text="Что в работе и как план?", model=model)
    assert seen == ["first"]
    workflow = next(b for b in result["messages"][1]["blocks"] if b["type"] == "workflow")
    assert [s["status"] for s in workflow["steps"]] == ["failed", "skipped"]
    assert "Остальные остановлены" in result["messages"][1]["text"]


@pytest.mark.asyncio
async def test_readonly_plan_keeps_company_data_scoped(db, product):
    other = Product(name="Другая компания", slug="other-business")
    db.add(other)
    db.flush()
    db.add(Task(product_id=other.id, title="ЧУЖОЙ СЕКРЕТ", assigned_agent="content_factory",
                status=TaskStatus.QUEUED))
    db.commit()
    model, _ = llm({"intent": "plan", "confidence": 0.9, "steps": [
        {"capability": "show_work"}, {"capability": "show_review"}]})
    response = await send(db, product, text="Задания и материалы", model=model)
    assert "ЧУЖОЙ СЕКРЕТ" not in json.dumps(response, ensure_ascii=False)
    assert "workflow" in json.dumps(response, ensure_ascii=False)


@pytest.mark.asyncio
async def test_readonly_plan_rejects_low_confidence_before_any_execution(db, product):
    model, _ = llm({"intent": "plan", "confidence": 0.2, "reply_text": "Что смотреть?", "steps": [
        {"capability": "show_work"}, {"capability": "show_review"}]})
    response = await send(db, product, text="Сделай", model=model)
    assert response["messages"][1]["intent"] == "clarify"
    assert not any(b["type"] == "workflow" for b in response["messages"][1]["blocks"])


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
async def test_unknown_capability_from_model_is_clarified_after_recovery(db, product):
    model, _ = llm({"intent": "execute", "capability": "launch_ads", "confidence": 0.99, "params": {}})
    result = await send(db, product, text="запусти рекламу", model=model)
    assert result["messages"][1]["intent"] == "clarify"
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


@pytest.mark.asyncio
async def test_illustrated_post_uses_spoken_instagram_platform_and_portrait_ratio(db, product):
    model, _ = llm({
        "intent": "execute", "capability": "create_post", "confidence": 0.96,
        "params": {"topic": "Осенние букеты", "channel": "telegram"},
    })
    result = await send(db, product, text="Сделай пост с картинкой для Instagram про букеты",
                        model=model, key="instagram-illustrated")
    card = next(x for x in result["messages"][-1]["blocks"] if x["type"] == "confirm")
    assert card["editable"]["format"] == "static_image"
    assert card["editable"]["platform"] == "instagram_reels"
    assert card["editable"]["aspect_ratio"] is None
    await service.confirm(db, context(db, product), card["confirmation_id"],
                          accept=True, revision=0)
    task = tasks(db, "content_creative")[0]
    artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request"))
    payload = json.loads(artifact.content)
    assert payload["platform"] == "instagram_reels"
    assert payload["image_model"] == "auto"


@pytest.mark.asyncio
async def test_illustrated_post_explicit_landscape_overrides_platform_default(db, product):
    model, _ = llm({
        "intent": "execute", "capability": "create_post", "confidence": 0.96,
        "params": {"topic": "Новые услуги", "channel": "telegram"},
    })
    result = await send(db, product,
                        text="Нарисуй горизонтальную картинку 16:9 для поста ВКонтакте",
                        model=model, key="vk-wide-image")
    card = next(x for x in result["messages"][-1]["blocks"] if x["type"] == "confirm")
    assert card["editable"]["format"] == "static_image"
    assert card["editable"]["platform"] == "vk"
    assert card["editable"]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_create_video_settings_reach_saved_paid_draft(db, product):
    from app.dispatcher.editable import CreateHints
    from app.api.whiteboard import job_state

    topic = "Как CRM помогает не потерять заявки"
    result = await service.handle_message(
        db, context(db, product), None, text=topic,
        client_key="whiteboard-create-settings", creation=CreateHints(
            format="whiteboard", topic=topic, video={
                "length": "60", "profile": "gemini_ru", "visual_review": True,
                "image_model": "gpt-image-2.5-sunburst",
                "max_api_calls": 45, "science_mode": "scientific",
                "call_to_action": "Подпишитесь на канал",
                "source_script": "Исходный сценарий",
                "background_notes": "Контекст", "reference_materials": "Источник"}))
    card = next(b for b in result["messages"][-1]["blocks"] if b["type"] == "confirm")
    task = tasks(db, "whiteboard_renderer")[0]
    assert task.status == TaskStatus.WAITING_APPROVAL
    saved = job_state(db, product.id, task.id)
    assert saved["request"]["duration_seconds"] == 60
    assert saved["request"]["scenes"] == 6
    assert saved["request"]["source_script"] == "Исходный сценарий"
    assert saved["request"]["reference_materials"] == "Источник"
    assert saved["request"]["call_to_action"] == "Подпишитесь на канал"
    assert saved["request"]["science_mode"] == "scientific"
    assert saved["max_api_calls"] == 45
    assert saved["visual_review_enabled"] is True
    assert saved["models"]["image"] == "gpt-image-2.5-sunburst"
    assert saved["models"]["speech"] == "x-ai/grok-voice-tts-1.0"
    assert card["paid"] is True
    assert any("6 сцен" in line for line in card["details"])
    assert any("grok-voice-tts" in line for line in card["details"])


def test_video_settings_reject_wrong_format_and_unknown_profile():
    from app.dispatcher.editable import CreateHints
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateHints(format="post", topic="Тема", video={"length": "60"})
    with pytest.raises(ValidationError):
        CreateHints(format="whiteboard", topic="Тема для видео",
                    video={"profile": "unverified"})


def test_navigation_catalog_matches_page_schema():
    from typing import get_args
    from app.dispatcher.registry import PAGES, PLATFORM_GUIDE, PageKey, page_href
    assert set(PAGES) == set(PLATFORM_GUIDE) == set(get_args(PageKey))
    assert all(safe_href(page_href(key, "00000000-0000-0000-0000-000000000000")) for key in PAGES)
    assert "mail" not in PAGES and "intelligence" not in PAGES
    assert "repair_video_images" in executors.CONFIRMERS


@pytest.mark.asyncio
async def test_task_diagnostics_are_filtered_and_tenant_scoped(db, product):
    other = Product(name="Other", slug="other-task-diagnostics")
    db.add(other)
    db.flush()
    own = Task(product_id=product.id, title="OWN VIDEO", assigned_agent="whiteboard_renderer",
               status=TaskStatus.FAILED)
    foreign = Task(product_id=other.id, title="FOREIGN VIDEO", assigned_agent="whiteboard_renderer",
                   status=TaskStatus.FAILED)
    db.add_all([own, foreign])
    db.commit()
    result = await send(db, product, chip={"label": "Ошибки видео", "capability": "list_tasks",
                        "params": {"tab": "problems", "kind": "video"}})
    blocks = result["messages"][1]["blocks"]
    assert str(own.id) in json.dumps(blocks)
    assert str(foreign.id) not in json.dumps(blocks)
    response = await send(db, product, key="foreign-id",
                          chip={"label": "Проверить", "capability": "show_task",
                                "params": {"task_id": str(foreign.id)}})
    assert "Не нашёл" in response["messages"][1]["text"]
    assert str(foreign.id) not in json.dumps(response["messages"][1]["blocks"])
    assert not list(db.scalars(select(ToolCall)))


@pytest.mark.asyncio
async def test_repair_dispatcher_requires_paid_confirmation_and_reuses_service(db, product, monkeypatch):
    from app.api import whiteboard
    from app.content.whiteboard import repair
    source = Task(product_id=product.id, title="SOURCE", assigned_agent="whiteboard_renderer",
                  status=TaskStatus.COMPLETED)
    draft = Task(product_id=product.id, title="REPAIR", assigned_agent="whiteboard_renderer",
                 status=TaskStatus.WAITING_APPROVAL)
    db.add_all([source, draft])
    db.commit()
    seen = []
    def queue(db_arg, settings_arg, product_id, source_id, *, image_model=None):
        assert product_id == product.id and source_id == source.id
        assert image_model == "gpt-image-2.5-sunburst"
        seen.append("draft")
        return draft
    monkeypatch.setattr(repair, "queue_visual_repair", queue)
    monkeypatch.setattr(whiteboard, "job_state", lambda *a: {
        "status": "waiting_approval", "models": {"image": "gpt-image-2.5-sunburst"},
        "max_api_calls": 13, "request_digest": "a" * 64})
    async def approve(*args, **kwargs):
        seen.append("approved")
    monkeypatch.setattr(whiteboard, "_approve", approve)
    result = await send(db, product, chip={"label": "Перерисовать", "capability": "repair_video_images",
                        "params": {"task_id": str(source.id), "image_model": "gpt-image-2.5-sunburst"}})
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    assert card["paid"] and seen == ["draft"]
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert seen == ["draft", "approved"]


def test_fast_routes_do_not_turn_diagnostics_or_repairs_into_creation():
    from app.dispatcher.fast_routes import quick_capability, quick_video_request
    assert quick_capability("Покажи видео с ошибками") is None
    assert quick_capability("Покажи последние видео") == "show_recent_videos"
    assert quick_video_request("Хочу переделать рисованное видео на тему энергии") is None


@pytest.mark.asyncio
async def test_dispatcher_retry_uses_shared_gate_and_confirmation(db, product):
    task = Task(product_id=product.id, title="POST: retry test", assigned_agent="content_factory",
                status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    result = await send(db, product, chip={"label": "Повторить", "capability": "retry_content_task",
                        "params": {"task_id": str(task.id)}})
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    assert card["paid"] and task.status == TaskStatus.FAILED
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    db.refresh(task)
    assert task.status == TaskStatus.QUEUED
    replay = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert replay["replayed"] is True


@pytest.mark.asyncio
async def test_dispatcher_retry_rejects_foreign_task_and_role(db, product):
    other = Product(name="Other", slug="other-retry")
    db.add(other)
    db.flush()
    task = Task(product_id=other.id, title="Foreign", assigned_agent="content_factory",
                status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    outcome, confirmation, _ = await service.act(context(db, product), "retry_content_task",
                                                  {"task_id": str(task.id)})
    assert not outcome.ok and confirmation is None
    ctx = context(db, other)
    ctx.studio_role = "staff"
    outcome, confirmation, _ = await service.act(ctx, "retry_content_task", {"task_id": str(task.id)})
    assert not outcome.ok and confirmation is None
    assert task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_dispatcher_retry_stale_card_does_not_requeue(db, product):
    task = Task(product_id=product.id, title="POST: stale retry", assigned_agent="content_factory",
                status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    result = await send(db, product, chip={"label": "Повторить", "capability": "retry_content_task",
                        "params": {"task_id": str(task.id)}})
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    task.updated_at = task.updated_at + timedelta(seconds=60)
    db.commit()
    response = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert "изменилось" in response["messages"][-1]["text"]
    db.refresh(task)
    assert task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_dispatcher_resume_pins_state_and_only_calls_existing_resume_after_approval(db, product, monkeypatch):
    from types import SimpleNamespace
    from app.content.whiteboard import jobs
    task = Task(product_id=product.id, title="VIDEO", assigned_agent="whiteboard_renderer",
                status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    source = SimpleNamespace(content="request")
    state = SimpleNamespace(content=json.dumps({"calls": 3, "extra_calls": 0}))
    approval = SimpleNamespace(status="approved")
    payload = SimpleNamespace(request=SimpleNamespace(topic="Тема видео"), max_api_calls=25)
    monkeypatch.setattr(jobs, "records", lambda *args, **kwargs: (source, state, approval, payload))
    monkeypatch.setattr(jobs, "can_resume", lambda *args: True)
    seen = []
    def resume(*args, **kwargs):
        assert kwargs["confirmed"] and kwargs["extra_calls"] == 0
        seen.append("resume")
        task.status = TaskStatus.QUEUED
        db.commit()
        return task
    monkeypatch.setattr(jobs, "resume_job", resume)
    chip = {"label": "Продолжить", "capability": "resume_video", "params": {"task_id": str(task.id)}}
    first = await send(db, product, chip=chip)
    card = next(b for b in first["messages"][1]["blocks"] if b["type"] == "confirm")
    assert card["paid"] and seen == []
    state.content = json.dumps({"calls": 4, "extra_calls": 0})
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert seen == [] and task.status == TaskStatus.FAILED
    second = await send(db, product, chip=chip, key="fresh")
    card = next(b for b in second["messages"][1]["blocks"] if b["type"] == "confirm")
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert seen == ["resume"] and task.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_dispatcher_vk_redraw_preserves_text_and_shows_job(db, product):
    from app.models.domain import ArtifactStatus
    from app.api.content_studio_activity import work_page
    origin = Task(product_id=product.id, title="POST: vk", assigned_agent="content_factory",
                  status=TaskStatus.COMPLETED)
    db.add(origin)
    db.flush()
    post = Artifact(product_id=product.id, task_id=origin.id, kind="channel_text",
                    title="VK post", status=ArtifactStatus.AWAITING_APPROVAL,
                    content=json.dumps({"channel": "vk", "title": "VK post", "body": "Original text", "image_model": "gemini",
                                        "image_ratio": "4:5", "image_state": "failed"}))
    db.add(post)
    db.commit()
    chip = {"label": "Переделать картинку", "capability": "redraw_post_image",
            "params": {"material_id": str(post.id), "feedback": "Сделай фон светлее"}}
    result = await send(db, product, chip=chip)
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    assert card["paid"] and not tasks(db, "content_image_regen")
    response = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert "Поставил новую картинку в очередь" in response["messages"][-1]["text"]
    db.refresh(post)
    assert json.loads(post.content)["body"] == "Original text"
    assert json.loads(post.content)["image_state"] == "regenerating"
    job = tasks(db, "content_image_regen")[0]
    assert job.status == TaskStatus.QUEUED
    assert any(row["id"] == str(job.id) for row in work_page(db, product.id, kind="edit")["items"])


@pytest.mark.asyncio
async def test_failed_confirmation_is_consumed_even_when_handler_rolls_back(db, product, monkeypatch):
    task = Task(product_id=product.id, title="POST: rollback", assigned_agent="content_factory",
                status=TaskStatus.FAILED)
    db.add(task)
    db.commit()
    answer = await send(db, product, chip={"label": "Повторить", "capability": "retry_content_task",
                        "params": {"task_id": str(task.id)}})
    card = next(b for b in answer["messages"][1]["blocks"] if b["type"] == "confirm")
    attempts = []
    async def failed(ctx, stored, via):
        from app.dispatcher.outcome import Outcome
        attempts.append(1)
        ctx.db.rollback()
        return Outcome("Задание изменилось", ok=False)
    monkeypatch.setitem(executors.CONFIRMERS, "retry_content_task", failed)
    first = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert "изменилось" in first["messages"][-1]["text"]
    second = await service.confirm(db, context(db, product), card["confirmation_id"], accept=True)
    assert second["replayed"] and attempts == [1]


@pytest.mark.asyncio
async def test_redraw_missing_title_is_explained_without_confirmation(db, product):
    task = Task(product_id=product.id, title="POST: bad", assigned_agent="content_factory",
                status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    from app.models.domain import ArtifactStatus
    post = Artifact(product_id=product.id, task_id=task.id, kind="channel_text", title="VK",
                    status=ArtifactStatus.AWAITING_APPROVAL,
                    content=json.dumps({"channel": "vk", "body": "Text", "image_model": "gemini"}))
    db.add(post)
    db.commit()
    outcome, confirmation, _ = await service.act(context(db, product), "redraw_post_image",
                                                {"material_id": str(post.id)})
    assert not outcome.ok and confirmation is None and "заголовок" in outcome.text
    assert not tasks(db, "content_image_regen")


def test_catalog_includes_required_fields_for_exact_targets():
    from app.dispatcher.registry import catalog_for_model
    catalog = {item["id"]: item for item in catalog_for_model()}
    assert "task_id" in catalog["resume_video"]["required"]
    assert "publication_id" in catalog["cancel_publication"]["required"]
    assert {"post_id", "when"} <= set(catalog["schedule_vk_post"]["required"])


def test_video_publication_requests_bypass_readonly_video_shortcut():
    from app.dispatcher.fast_routes import quick_capability
    assert quick_capability("Покажи последние видео") == "show_recent_videos"
    assert quick_capability("Покажи опубликованные видео") is None
    assert quick_capability("Покажи видео и отмени его публикацию") is None


@pytest.mark.asyncio
async def test_dynamic_clarification_is_saved_and_does_not_execute(db, product):
    model, _ = llm({"intent": "clarify", "reply_text": "Какой результат нужен?",
                    "confidence": 0.4, "clarification_options": [
                        "Привлечь новых клиентов", "Вернуть прежних клиентов",
                        {"capability": "create_post"}, "Привлечь новых клиентов"]})
    result = await send(db, product, text="Мне хотелось бы улучшить ситуацию с клиентами", model=model)
    answer = result["messages"][1]
    assert answer["intent"] == "clarify"
    assert {"type": "clarification", "options": ["Привлечь новых клиентов",
                                                 "Вернуть прежних клиентов"]} in answer["blocks"]
    assert not any(b["type"] in ("confirm", "task", "chips") for b in answer["blocks"])
    assert not list(db.scalars(select(Task)))
    replay = await send(db, product, text="Мне хотелось бы улучшить ситуацию с клиентами", model=model)
    assert replay["replayed"] and replay["messages"][1]["blocks"] == answer["blocks"]


def test_clarification_rejects_actions_and_malformed_optional_choices():
    from app.dispatcher.decision import DispatcherDecision
    assert DispatcherDecision(intent="clarify", clarification_options={"bad": "value"}).clarification_options == []
    assert clean_blocks([{"type": "clarification", "options": ["A", "B"],
                          "capability": "create_post"}]) == []
    assert clean_blocks([{"type": "clarification", "options": ["A"]}]) == []


@pytest.mark.asyncio
async def test_conversational_edit_updates_same_pending_card_and_replays(db, product):
    initial = await send(db, product, chip={
        "label": "Четыре Stories", "capability": "create_creative",
        "params": {"topic": "Уход за волосами", "format": "story", "frame_count": 4,
                   "platform": "telegram", "audience": "Новые клиенты"}})
    original = next(b for b in initial["messages"][1]["blocks"] if b["type"] == "confirm")
    model, _ = llm({"intent": "edit_card", "confidence": 0.95, "params": {
        "confirmation_id": original["confirmation_id"], "revision": 0, "frame_count": 6}})
    result = await service.handle_message(db, context(db, product), model, text="Нет, сделай шесть экранов",
                                          client_key="edit-six", mode="voice")
    assert result["messages"][1]["id"] == initial["messages"][1]["id"]
    card = next(b for b in result["messages"][1]["blocks"] if b["type"] == "confirm")
    assert card["revision"] == 1 and card["editable"]["frame_count"] == 6
    assert card["editable"]["topic"] == "Уход за волосами"
    assert card["editable"]["audience"] == "Новые клиенты"
    assert card["state"] == "pending"
    assert not tasks(db, "content_creative")
    replay = await service.handle_message(db, context(db, product), model, text="Нет, сделай шесть экранов",
                                          client_key="edit-six", mode="voice")
    assert replay["replayed"] and replay["messages"][1]["blocks"] == result["messages"][1]["blocks"]
    with pytest.raises(PermissionError):
        await service.confirm(db, context(db, product), original["confirmation_id"], accept=True, revision=0)
    assert not tasks(db, "content_creative")
    await service.confirm(db, context(db, product), original["confirmation_id"], accept=True, revision=1)
    assert len(tasks(db, "content_creative")) == 1


@pytest.mark.asyncio
async def test_conversational_edit_rejects_stale_foreign_and_unknown_fields(db, product):
    initial = await send(db, product, chip=post_chip())
    card = next(b for b in initial["messages"][1]["blocks"] if b["type"] == "confirm")
    for index, (account, params) in enumerate([
        ("owner", {"revision": 5, "topic": "Другая тема"}),
        ("other-owner", {"revision": 0, "topic": "Чужая тема"}),
        ("owner", {"revision": 0, "format": "package"}),
        ("owner", {"revision": 0, "topic": "x"}),
    ]):
        model, _ = llm({"intent": "edit_card", "confidence": 1, "params": {
            "confirmation_id": card["confirmation_id"], **params}})
        result = await send(db, product, text="Измени параметры", key=f"invalid-edit-{index}",
                            model=model, account=account)
        assert result["messages"][-1]["intent"] == "clarify"
    conversation = service.open_conversation(db, product.id, "owner")
    saved = next(m for m in service.messages(db, conversation) if m.confirmation_id == card["confirmation_id"])
    assert next(b for b in saved.blocks if b["type"] == "confirm") == card
    assert not list(db.scalars(select(Task)))


def test_schema_tool_forms_allow_only_audited_capabilities():
    from app.dispatcher.forms import FORM_CAPABILITIES, form_for
    for capability in FORM_CAPABILITIES:
        block = form_for(capability, {})
        assert clean_blocks([block])[0]["type"] == "tool_form"
        assert {f["name"] for f in block["fields"]} == set(CAPABILITIES[capability].params.model_fields)
    for capability in ["create_creative", "set_insights_depth", "schedule_post", "unknown"]:
        with pytest.raises(ValueError):
            form_for(capability, {})
    with pytest.raises(ValueError):
        form_for("list_tasks", {"tab": "invented"})
    with pytest.raises(ValueError):
        form_for("show_insights", {"days": 366})
    fields = {f["name"]: f for f in form_for("show_insights", {"days": "30"})["fields"]}
    assert fields["days"]["minimum"] == 7 and fields["days"]["maximum"] == 365
    assert fields["days"]["value"] == "30"
    assert {x["value"] for x in fields["topic"]["options"]} == set(
        CAPABILITIES["show_insights"].params.model_json_schema()["properties"]["topic"]["enum"])


@pytest.mark.asyncio
async def test_model_form_is_persisted_without_execution_then_regular_chip_reads(db, product):
    model, _ = llm({"intent": "form", "capability": "list_tasks", "confidence": 0.95,
                    "params": {"kind": "video", "tab": "problems"}})
    result = await send(db, product, text="Хочу выбрать фильтры для списка заданий", model=model)
    answer = result["messages"][-1]
    block = next(b for b in answer["blocks"] if b["type"] == "tool_form")
    assert answer["intent"] == "form"
    assert {f["name"]: f["value"] for f in block["fields"]}["kind"] == "video"
    assert not list(db.scalars(select(Task)))
    replay = await send(db, product, text="Хочу выбрать фильтры для списка заданий", model=model)
    assert replay["replayed"] and replay["messages"][-1]["blocks"] == answer["blocks"]
    from app.api.dispatcher import FormSubmissionIn
    response = await service.handle_message(db, context(db, product), None, text="", client_key="read-form",
        form_submission=FormSubmissionIn(message_id=answer["id"], params={"kind": "video", "tab": "all"}))
    assert response["messages"][-1]["intent"] == "execute"
    assert not list(db.scalars(select(Task)))


@pytest.mark.asyncio
async def test_model_cannot_make_mutating_tool_form(db, product):
    model, _ = llm({"intent": "form", "capability": "set_insights_depth", "confidence": 1,
                    "params": {"days": 90}})
    result = await send(db, product, text="Настрой мне форму для просмотра", model=model)
    assert result["messages"][-1]["intent"] == "clarify"
    assert not any(b["type"] == "tool_form" for b in result["messages"][-1]["blocks"])
    assert not list(db.scalars(select(Task)))


@pytest.mark.asyncio
async def test_saved_prepare_form_requires_confirmation_and_is_single_use(db, product):
    from app.api.dispatcher import FormSubmissionIn
    from app.dispatcher.forms import form_for
    from uuid import UUID
    model, _ = llm({"intent": "form", "capability": "create_post", "confidence": 0.95, "params": {}})
    initial = await send(db, product, text="Дай форму, где я сам укажу тему поста", model=model)
    answer = initial["messages"][-1]
    block = next(b for b in answer["blocks"] if b["type"] == "tool_form")
    assert block["submit_label"] == "Подготовить подтверждение"
    topic = next(f for f in block["fields"] if f["name"] == "topic")
    assert topic["required"] and topic["value"] == ""
    assert not list(db.scalars(select(Task)))
    with pytest.raises(ValueError):
        form_for("create_post", {}, partial=False)
    with pytest.raises(ValueError):
        form_for("create_post", {"topic": "Тема", "channel": "telegram", "image_model": "openai"}, partial=False)
    request = FormSubmissionIn(message_id=answer["id"], params={"topic": "Как снизить неявки", "channel": "vk"})
    with pytest.raises(LookupError):
        await service.handle_message(db, context(db, product, "other-owner"), None, text="",
                                     client_key="foreign", form_submission=request)
    with pytest.raises(ValueError):
        await service.handle_message(db, context(db, product), None, text="", client_key="tamper",
            form_submission=FormSubmissionIn(message_id=answer["id"], params={"topic": "Тема", "capability": "whiteboard_video"}))
    prepared = await service.handle_message(db, context(db, product), None, text="",
                                             client_key="first", form_submission=request)
    card = next(b for b in prepared["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["state"] == "pending" and card["editable"]["topic"] == "Как снизить неявки"
    assert not list(db.scalars(select(Task)))
    # Interleaved task result must not be mistaken for the original response.
    saved = db.get(DispatcherMessage, UUID(prepared["messages"][-1]["id"]))
    old_seq = saved.seq
    saved.seq += 1
    db.flush()
    db.add(DispatcherMessage(conversation_id=saved.conversation_id, product_id=product.id,
                             seq=old_seq, role="assistant", text="Другой результат", capability="task_result"))
    db.commit()
    duplicate = await service.handle_message(db, context(db, product), None, text="",
                                              client_key="second-tab", form_submission=request)
    assert duplicate["replayed"]
    assert duplicate["messages"][-1]["id"] == prepared["messages"][-1]["id"]
    await service.confirm(db, context(db, product), card["confirmation_id"], accept=True, revision=0)
    assert len(tasks(db, "content_factory")) == 1
    after = await service.handle_message(db, context(db, product), None, text="",
                                          client_key="third-tab", form_submission=request)
    assert after["replayed"] and len(tasks(db, "content_factory")) == 1


@pytest.mark.asyncio
async def test_video_form_edit_same_revision_and_no_task_before_submit(db, product):
    from app.api.dispatcher import FormSubmissionIn
    from app.dispatcher.forms import form_for
    initial_model, _ = llm({"intent": "form", "capability": "whiteboard_video", "confidence": 0.95,
                            "params": {"topic": "Как устроена онлайн-запись", "length": "30"}})
    initial = await send(db, product, text="Дай настройки видео", model=initial_model)
    original = initial["messages"][-1]
    block = next(b for b in original["blocks"] if b["type"] == "tool_form")
    assert block["revision"] == 0 and len(block["fields"]) == 12
    assert not list(db.scalars(select(Task)))
    edit_model, _ = llm({"intent": "edit_form", "confidence": 0.95, "params": {
        "message_id": original["id"], "revision": 0, "length": "60",
        "image_model": "gpt-image-2.5-sunburst"}})
    result = await service.handle_message(db, context(db, product), edit_model, text="Минуту, с GPT Image",
                                          client_key="edit-video-form", mode="voice")
    assert result["messages"][1]["id"] == original["id"]
    revised = next(b for b in result["messages"][1]["blocks"] if b["type"] == "tool_form")
    fields = {f["name"]: f["value"] for f in revised["fields"]}
    assert revised["revision"] == 1 and fields["length"] == "60"
    assert fields["image_model"] == "gpt-image-2.5-sunburst"
    assert fields["topic"] == "Как устроена онлайн-запись" and fields["max_api_calls"] == ""
    assert not list(db.scalars(select(Task)))
    with pytest.raises(ValueError):
        await service.handle_message(db, context(db, product), None, text="", client_key="stale-video",
            form_submission=FormSubmissionIn(message_id=original["id"], revision=0,
                                              params={"topic": fields["topic"]}))
    with pytest.raises(ValueError):
        form_for("whiteboard_video", {"topic": fields["topic"], "length": "45"}, partial=False)
    replay = await service.handle_message(db, context(db, product), edit_model, text="Минуту, с GPT Image",
                                          client_key="edit-video-form", mode="voice")
    assert replay["replayed"] and replay["messages"][1]["blocks"] == result["messages"][1]["blocks"]
    assert not list(db.scalars(select(Task)))
