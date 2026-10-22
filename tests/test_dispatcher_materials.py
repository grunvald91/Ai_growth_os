"""Dispatcher: ready materials and publication. The same rules as the Studio pages, always with a card first."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.models.domain import Approval, Artifact, ArtifactStatus, Product, Publication, Task

POST_BODY = "Онлайн-запись без звонков: клиент выбирает время сам, а вы видите расписание на день."


def make_settings(autopost=False):
    return Settings(_env_file=None, content_telegram_autopost_enabled=autopost, whiteboard_storyboard_model="s",
                    whiteboard_image_model="google/i", whiteboard_speech_model="sp", whiteboard_regions_model="r")


@pytest.fixture
def product(db):
    row = Product(name="Салон", slug="salon")
    db.add(row)
    db.commit()
    return row


def material(db, product, *, kind="channel_text", status=ArtifactStatus.AWAITING_APPROVAL, title="Пост про запись",
             body=POST_BODY, **extra):
    row = Artifact(product_id=product.id, kind=kind, title=title, status=status,
                   content=json.dumps({"channel": "telegram", "body": body, **extra}))
    db.add(row)
    db.commit()
    return row


def channel(db, product, title="Канал салона", chat_id="-1001234567890"):
    row = Artifact(product_id=product.id, kind="telegram_channel_connection", title=title,
                   status=ArtifactStatus.APPROVED, content=json.dumps({"chat_id": chat_id, "title": title}))
    db.add(row)
    db.commit()
    return row


def ctx(db, product, autopost=False, account="owner"):
    return Context(db=db, product=product, settings=make_settings(autopost), account=account)


async def chip(db, product, capability, params=None, key=None, **kw):
    item = {"label": capability, "capability": capability, "params": params or {}}
    return (await service.handle_message(db, ctx(db, product, **kw), None, text="", client_key=key or capability + str(params),
                                         mode="chip", chip=item))["messages"][1]


def blocks(answer, kind):
    return [b for b in answer["blocks"] if b["type"] == kind]


def when_text(days=2, hour=12):
    local = (datetime.now(timezone.utc) + timedelta(days=days)).astimezone().replace(hour=hour, minute=0)
    return local.strftime("%Y-%m-%d %H:%M")


# ---- showing ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_waiting_material_is_shown_as_a_card_with_an_approve_button(db, product):
    row = material(db, product)
    answer = await chip(db, product, "show_material")
    card = blocks(answer, "material")[0]
    assert (card["artifact_id"], card["kind"], card["status"]) == (str(row.id), "Пост", "Ждёт вашего решения")
    assert card["excerpt"].startswith("Онлайн-запись")
    assert card["href"] == f"/studio/products/{product.id}/materials/{row.id}"
    buttons = blocks(answer, "chips")[0]["items"]
    assert buttons[0]["capability"] == "approve_material" and buttons[0]["params"] == {"material_id": str(row.id)}


@pytest.mark.asyncio
async def test_nothing_waiting_is_said_plainly(db, product):
    answer = await chip(db, product, "show_material")
    assert "Ничего не ждёт" in answer["text"] and blocks(answer, "material") == []


@pytest.mark.asyncio
async def test_other_companys_material_is_not_shown(db, product):
    other = Product(name="Чужая", slug="other")
    db.add(other)
    db.commit()
    foreign = material(db, other)
    answer = await chip(db, product, "show_material", {"material_id": str(foreign.id)})
    assert "Не нашёл" in answer["text"] and blocks(answer, "material") == []


# ---- approving --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_waits_for_a_card_and_changes_nothing_before_it(db, product):
    row = material(db, product)
    answer = await chip(db, product, "approve_material", {"material_id": str(row.id)})
    confirm = blocks(answer, "confirm")[0]
    assert confirm["state"] == "pending" and confirm["paid"] is False
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL
    done = await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=True)
    db.refresh(row)
    assert row.status == ArtifactStatus.APPROVED and row.version == 2
    assert "Утвердил" in done["messages"][1]["text"]
    approval = db.scalar(select(Approval).where(Approval.action_type == "dispatcher:approve_material"))
    assert approval.artifact_id == row.id and approval.decided_by == "owner" and approval.reason == "via=button"
    again = await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=True)
    assert again["replayed"] is True
    db.refresh(row)
    assert row.version == 2


@pytest.mark.asyncio
async def test_declining_the_card_leaves_the_material_alone(db, product):
    row = material(db, product)
    confirm = blocks(await chip(db, product, "approve_material"), "confirm")[0]
    await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=False)
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_material_with_numbers_to_check_is_not_approved_from_here(db, product):
    row = material(db, product, fact_check_required=True)
    answer = await chip(db, product, "approve_material", {"material_id": str(row.id)})
    assert blocks(answer, "confirm") == []
    assert "проверить глазами" in answer["text"]
    link = blocks(answer, "links")[0]["items"][0]["href"]
    assert link.endswith(f"/materials/{row.id}")
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_material_changed_while_the_card_was_open_is_not_approved(db, product):
    row = material(db, product)
    confirm = blocks(await chip(db, product, "approve_material"), "confirm")[0]
    row.version += 1  # for example an edit finished
    db.commit()
    done = await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=True)
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL
    assert "изменился" in done["messages"][1]["text"]


@pytest.mark.asyncio
async def test_already_approved_material_is_not_asked_twice(db, product):
    row = material(db, product, status=ArtifactStatus.APPROVED)
    answer = await chip(db, product, "approve_material", {"material_id": str(row.id)})
    assert blocks(answer, "confirm") == [] and "уже утверждён" in answer["text"]


@pytest.mark.asyncio
async def test_a_model_decision_to_approve_still_gets_a_card(db, product):
    from tests.test_dispatcher import llm
    row = material(db, product)
    model, _ = llm({"intent": "execute", "capability": "approve_material", "params": {"material_id": str(row.id)},
                    "confidence": 0.95, "reply_text": "Утверждаю", "spoken": "Утверждаю", "next_chips": []})
    answer = (await service.handle_message(db, ctx(db, product), model, text="утверди пост", client_key="m1"))["messages"][1]
    assert blocks(answer, "confirm")
    db.refresh(row)
    assert row.status == ArtifactStatus.AWAITING_APPROVAL


# ---- reworking --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rework_queues_an_edit_task_with_the_feedback(db, product):
    row = material(db, product)
    answer = await chip(db, product, "revise_material", {"feedback": "Сделай короче и добавь призыв записаться"})
    assert "дорабатываю" in answer["text"]
    task = db.scalar(select(Task).where(Task.assigned_agent == "content_editor"))
    assert task is not None and task.title == "EDIT: " + str(row.id)


@pytest.mark.asyncio
async def test_rework_without_feedback_asks_what_to_change(db, product):
    material(db, product)
    answer = await chip(db, product, "revise_material", {})
    assert "что поправить" in answer["text"]
    assert db.scalar(select(Task).where(Task.assigned_agent == "content_editor")) is None


@pytest.mark.asyncio
async def test_rework_of_the_same_post_twice_is_refused_by_the_page_rule(db, product):
    material(db, product)
    await chip(db, product, "revise_material", {"feedback": "Сделай короче и добавь призыв"}, key="a")
    answer = await chip(db, product, "revise_material", {"feedback": "Ещё раз то же самое"}, key="b")
    assert "уже отправлен на доработку" in answer["text"]


# ---- scheduling -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduling_asks_first_then_creates_a_scheduled_publication(db, product):
    post = material(db, product, status=ArtifactStatus.APPROVED)
    connection = channel(db, product)
    when = when_text()
    answer = await chip(db, product, "schedule_post", {"when": when})
    confirm = blocks(answer, "confirm")[0]
    details = " ".join(confirm["details"])
    assert "Канал салона" in details and "(Москва)" in details and "Автоотправка на сервере сейчас выключена" in details
    assert db.scalar(select(Publication)) is None
    done = await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=True)
    publication = db.scalar(select(Publication))
    assert (publication.artifact_id, publication.status, publication.channel) == (post.id, "scheduled", "telegram")
    assert "Запланировал" in done["messages"][1]["text"]
    assert db.scalar(select(Approval).where(Approval.action_type == "dispatcher:schedule_post")) is not None
    assert str(connection.id) in db.scalars(select(Artifact.content).where(Artifact.kind == "telegram_publish_intent")).first()


@pytest.mark.asyncio
async def test_with_autopost_on_the_card_says_the_post_goes_out_by_itself(db, product):
    material(db, product, status=ArtifactStatus.APPROVED)
    channel(db, product)
    answer = await chip(db, product, "schedule_post", {"when": when_text()}, autopost=True)
    assert "уйдёт в канал сам" in " ".join(blocks(answer, "confirm")[0]["details"])


@pytest.mark.asyncio
async def test_time_in_the_past_or_unreadable_is_refused_before_any_card(db, product):
    material(db, product, status=ArtifactStatus.APPROVED)
    channel(db, product)
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    assert "уже прошло" in (await chip(db, product, "schedule_post", {"when": past}, key="p"))["text"]
    assert "Не разобрал" in (await chip(db, product, "schedule_post", {"when": "завтра днём!!"}, key="q"))["text"]
    far = (datetime.now() + timedelta(days=120)).strftime("%Y-%m-%d %H:%M")
    assert "90 дней" in (await chip(db, product, "schedule_post", {"when": far}, key="r"))["text"]


@pytest.mark.asyncio
async def test_unapproved_post_is_not_scheduled(db, product):
    material(db, product)  # still waiting for the owner
    channel(db, product)
    answer = await chip(db, product, "schedule_post", {"when": when_text()})
    assert blocks(answer, "confirm") == [] and "утвердите" in answer["text"].lower()


@pytest.mark.asyncio
async def test_without_a_connected_channel_there_is_a_link_not_a_card(db, product):
    material(db, product, status=ArtifactStatus.APPROVED)
    answer = await chip(db, product, "schedule_post", {"when": when_text()})
    assert blocks(answer, "confirm") == [] and "не подключён" in answer["text"]
    assert blocks(answer, "links")[0]["items"][0]["href"].endswith("/channels")


@pytest.mark.asyncio
async def test_two_channels_ask_which_one_with_complete_chips(db, product):
    post = material(db, product, status=ArtifactStatus.APPROVED)
    channel(db, product, "Первый", "-1001")
    second = channel(db, product, "Второй", "-1002")
    answer = await chip(db, product, "schedule_post", {"when": when_text()})
    items = blocks(answer, "chips")[0]["items"]
    assert {i["label"] for i in items} == {"Первый", "Второй"}
    chosen = next(i for i in items if i["label"] == "Второй")
    assert chosen["params"]["channel_id"] == str(second.id) and chosen["params"]["post_id"] == str(post.id)
    assert blocks(answer, "confirm") == []


@pytest.mark.asyncio
async def test_an_already_scheduled_post_is_not_picked_again(db, product):
    material(db, product, status=ArtifactStatus.APPROVED)
    channel(db, product)
    confirm = blocks(await chip(db, product, "schedule_post", {"when": when_text()}, key="1"), "confirm")[0]
    await service.confirm(db, ctx(db, product), confirm["confirmation_id"], accept=True)
    answer = await chip(db, product, "schedule_post", {"when": when_text(days=3)}, key="2")
    assert blocks(answer, "confirm") == [] and "Нет утверждённого поста" in answer["text"]
    assert len(list(db.scalars(select(Publication)))) == 1


@pytest.mark.asyncio
async def test_another_companys_channel_cannot_be_used(db, product):
    other = Product(name="Чужая", slug="other")
    db.add(other)
    db.commit()
    material(db, product, status=ArtifactStatus.APPROVED)
    foreign = channel(db, other)
    answer = await chip(db, product, "schedule_post", {"when": when_text(), "channel_id": str(foreign.id)})
    assert blocks(answer, "confirm") == []


# ---- results coming back ----------------------------------------------------------------

from app.dispatcher.updates import collect_updates  # noqa: E402
from app.models.domain import TaskStatus  # noqa: E402


async def started_post(db, product):
    await chip(db, product, "create_post", {"topic": "Онлайн-запись без звонков", "channel": "telegram"})
    return db.scalar(select(Task).where(Task.assigned_agent == "content_factory"))


def updates(db, product):
    context = ctx(db, product)
    conversation = service.open_conversation(db, product.id, context.account)
    return collect_updates(db, context, conversation)


@pytest.mark.asyncio
async def test_nothing_is_added_while_the_task_still_runs(db, product):
    await started_post(db, product)
    assert updates(db, product) == []


@pytest.mark.asyncio
async def test_finished_post_comes_back_as_a_card_once(db, product):
    task = await started_post(db, product)
    task.status = TaskStatus.COMPLETED
    result = material(db, product, title="Пост про запись")
    result.task_id = task.id
    db.commit()
    first = updates(db, product)
    assert len(first) == 1
    message = service.to_dict(first[0])
    assert message["blocks"][1]["type"] == "material" and message["blocks"][1]["artifact_id"] == str(result.id)
    approve = [b for b in message["blocks"] if b["type"] == "chips"][0]["items"][0]
    assert approve["capability"] == "approve_material" and approve["params"] == {"material_id": str(result.id)}
    assert updates(db, product) == []  # the same result is not announced twice


@pytest.mark.asyncio
async def test_failed_task_is_reported_with_a_link_to_the_task(db, product):
    task = await started_post(db, product)
    task.status = TaskStatus.FAILED
    db.commit()
    message = service.to_dict(updates(db, product)[0])
    assert message["text"].startswith("Не получилось")
    assert message["blocks"][1]["items"][0]["href"].endswith(f"/tasks/{task.id}")


@pytest.mark.asyncio
async def test_finished_plan_comes_back_with_an_approve_button(db, product):
    await chip(db, product, "make_plan")
    task = db.scalar(select(Task).order_by(Task.created_at.desc()))
    task.status = TaskStatus.COMPLETED
    plan = Artifact(product_id=product.id, task_id=task.id, kind="editorial_plan", title="План",
                    status=ArtifactStatus.DRAFT, content=json.dumps({"approval_state": "draft", "slots": []}))
    db.add(plan)
    db.commit()
    message = service.to_dict(updates(db, product)[0])
    chips = [b for b in message["blocks"] if b["type"] == "chips"][0]["items"]
    assert chips[0]["capability"] == "approve_plan"


@pytest.mark.asyncio
async def test_another_conversation_does_not_get_this_result(db, product):
    task = await started_post(db, product)
    task.status = TaskStatus.COMPLETED
    db.commit()
    context = ctx(db, product, account="someone-else")
    other = service.open_conversation(db, product.id, context.account)
    assert collect_updates(db, context, other) == []


@pytest.mark.asyncio
async def test_check_what_is_ready_shows_the_newest_card_with_its_button(db, product):
    row = material(db, product)
    answer = await chip(db, product, "show_review")
    assert blocks(answer, "material")[0]["artifact_id"] == str(row.id)
    assert blocks(answer, "chips")[0]["items"][0]["capability"] == "approve_material"
