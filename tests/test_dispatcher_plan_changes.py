"""Stage 5: plan changes from Studio chat never write before explicit approval."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.dispatcher import service
from app.dispatcher.outcome import Context
from app.dispatcher.readers import snapshot
from app.dispatcher.registry import PlanPackageEditParams, PlanSlotMoveParams
from app.models.domain import Artifact, Product, ToolCall


@pytest.fixture
def company(db):
    p = Product(name="Проверка плана", slug="plan-chat-stage-five")
    db.add(p)
    db.commit()
    return p


def make_plan(db, company, *, state="draft"):
    zone = ZoneInfo("Europe/Moscow")
    tomorrow = datetime.now(zone).date() + timedelta(days=1)
    start = datetime.combine(tomorrow, datetime.min.time(), zone)
    slots = []
    for index, platform in enumerate(("own_site", "dzen")):
        when = start + timedelta(days=index, hours=12)
        slots.append({
            "slot_id": str(index + 1), "package_key": "booking", "topic": "Как избежать ошибок записи",
            "platform": platform, "format": "article", "day_offset": index,
            "hour": 12, "minute": 0, "prepare_lead_hours": 24,
            "audience": "Владельцы небольших компаний",
            "objective": "Проверить полезность онлайн-записи",
            "rationale": "Проверка конкретного полезного материала на целевой площадке.",
            "evidence_refs": [], "success_metric": "leads", "timing_basis": "test_hypothesis",
            "publish_at": when.isoformat(), "prepare_at": (when-timedelta(hours=24)).isoformat(),
            "state": "planned", "task_id": None,
        })
    value = {
        "generated_at": datetime.now(zone).isoformat(), "approval_state": state,
        "timezone": "Europe/Moscow", "horizon_days": 14, "review_after_days": 7,
        "strategy": "Проверить полезность подсказок для записи клиентов владельцам бизнеса.",
        "capacity_assumptions": ["Владелец подтвердит материалы перед публикацией."],
        "unknowns": [], "channels": [
            {"platform": p, "decision": "test",
             "rationale": "Проверить пригодность материалов для этой площадки.",
             "evidence_refs": []} for p in ("own_site", "dzen")
        ], "slots": slots,
    }
    row = Artifact(product_id=company.id, kind="editorial_plan",
                   title="Тестовый план", content=json.dumps(value, ensure_ascii=False))
    db.add(row)
    db.commit()
    return row


def ctx(db, company, *, role=None):
    return Context(db=db, product=company, settings=object(), account="owner", studio_role=role)


async def request_card(db, context, capability, params, *, key):
    result = await service.handle_message(
        db, context, None, text="", client_key=key, mode="chip",
        chip={"label": "Изменить контент-план", "capability": capability, "params": params})
    return next(block for block in result["messages"][-1]["blocks"] if block["type"] == "confirm")


def plan_data(row):
    return json.loads(row.content)


@pytest.mark.asyncio
async def test_edit_topic_confirms_before_writing_every_channel(db, company):
    row = make_plan(db, company)
    owner = ctx(db, company)
    assert snapshot(db, company)["editable_plan"]["draft_packages"][0]["package_key"] == "booking"
    block = await request_card(db, owner, "edit_plan_package",
                               {"package_key": "booking", "topic": "Как клиенту записаться без звонка"},
                               key="topic1")
    assert block["state"] == "pending"
    assert block["change_preview"]["rows"][0]["before"] == "Как избежать ошибок записи"
    assert plan_data(row)["slots"][0]["topic"] == "Как избежать ошибок записи"
    result = await service.confirm(db, owner, block["confirmation_id"], accept=True)
    assert "обновлён" in result["messages"][-1]["text"]
    assert [slot["topic"] for slot in plan_data(row)["slots"]] == [
        "Как клиенту записаться без звонка", "Как клиенту записаться без звонка"]
    assert (await service.confirm(db, owner, block["confirmation_id"], accept=True))["replayed"]


@pytest.mark.asyncio
async def test_decline_does_not_change_plan(db, company):
    row = make_plan(db, company)
    owner = ctx(db, company)
    block = await request_card(db, owner, "edit_plan_package",
                               {"package_key": "booking", "objective": "Получить обратную связь"},
                               key="topic-declined")
    await service.confirm(db, owner, block["confirmation_id"], accept=False)
    assert plan_data(row)["slots"][0]["objective"] == "Проверить полезность онлайн-записи"


@pytest.mark.asyncio
async def test_stale_package_confirmation_cannot_overwrite_another_edit(db, company):
    row = make_plan(db, company)
    owner = ctx(db, company)
    block = await request_card(db, owner, "edit_plan_package",
                               {"package_key": "booking", "topic": "Как быстро оформить запись"},
                               key="topic-stale")
    data = plan_data(row)
    for slot in data["slots"]:
        slot["topic"] = "Как верно подтвердить визит"
    row.content = json.dumps(data, ensure_ascii=False)
    db.commit()
    result = await service.confirm(db, owner, block["confirmation_id"], accept=True)
    assert "изменился" in result["messages"][-1]["text"]
    assert all(slot["topic"] == "Как верно подтвердить визит" for slot in plan_data(row)["slots"])


@pytest.mark.asyncio
async def test_slot_time_move_uses_existing_calendar_guards(db, company):
    row = make_plan(db, company, state="active")
    owner = ctx(db, company)
    old = plan_data(row)["slots"][0]["publish_at"]
    when = datetime.fromisoformat(old).replace(hour=16)
    snap = snapshot(db, company)["editable_plan"]
    assert snap["calendar_slots"][0]["slot_id"] == "1"
    block = await request_card(db, owner, "move_plan_slot",
                               {"slot_id": "1", "when": when.strftime("%Y-%m-%d %H:%M")},
                               key="move1")
    assert block["change_preview"]["rows"][0]["after"].endswith("16:00")
    assert plan_data(row)["slots"][0]["publish_at"] == old
    result = await service.confirm(db, owner, block["confirmation_id"], accept=True)
    assert "обновлено" in result["messages"][-1]["text"]
    assert plan_data(row)["slots"][0]["publish_at"] == when.isoformat()
    assert plan_data(row)["slots"][0]["topic"] == "Как избежать ошибок записи"


@pytest.mark.asyncio
async def test_stale_slot_move_rejected_and_other_company_not_touched(db, company):
    row = make_plan(db, company, state="active")
    owner = ctx(db, company)
    old = datetime.fromisoformat(plan_data(row)["slots"][0]["publish_at"])
    block = await request_card(db, owner, "move_plan_slot",
                               {"slot_id": "1", "when": old.replace(hour=16).strftime("%Y-%m-%d %H:%M")},
                               key="move-stale")
    data = plan_data(row)
    data["slots"][0]["publish_at"] = old.replace(hour=14).isoformat()
    row.content = json.dumps(data, ensure_ascii=False)
    db.commit()
    result = await service.confirm(db, owner, block["confirmation_id"], accept=True)
    assert "уже изменилась" in result["messages"][-1]["text"]
    assert plan_data(row)["slots"][0]["publish_at"].endswith("14:00:00+03:00")

    foreign = Product(name="Чужой бизнес", slug="another-plan-owner")
    db.add(foreign)
    db.commit()
    from app.dispatcher.plan_changes import confirm_move_plan_slot
    result = await confirm_move_plan_slot(ctx(db, foreign), {
        "plan_id": str(row.id), "slot_id": "1", "when": old.replace(hour=18).isoformat(),
        "expected_publish_at": data["slots"][0]["publish_at"]}, "button")
    assert result.ok is False
    assert plan_data(row)["slots"][0]["publish_at"].endswith("14:00:00+03:00")


@pytest.mark.asyncio
async def test_non_owner_and_noneditable_plan_rejected(db, company):
    row = make_plan(db, company, state="draft")
    viewer = ctx(db, company, role="viewer")
    from app.dispatcher.plan_changes import describe_edit_plan_package, describe_move_plan_slot
    denied = await describe_edit_plan_package(viewer, PlanPackageEditParams(
        package_key="booking", topic="Как улучшить запись клиентов"))
    assert denied.ok is False
    denied_move = await describe_move_plan_slot(viewer, PlanSlotMoveParams(
        slot_id="1", when=(datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")))
    assert denied_move.ok is False
    value = plan_data(row)
    value["slots"][0]["task_id"] = "job-started"
    row.content = json.dumps(value, ensure_ascii=False)
    db.commit()
    owner = ctx(db, company)
    refused = await describe_edit_plan_package(owner, PlanPackageEditParams(
        package_key="booking", topic="Как улучшить запись клиентов"))
    assert refused.ok is False
    with pytest.raises(ValidationError):
        PlanPackageEditParams(package_key="booking")
    with pytest.raises(ValidationError):
        PlanSlotMoveParams(slot_id="1", when="завтра вечером")
