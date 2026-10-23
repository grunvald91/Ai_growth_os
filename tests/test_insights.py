"""Deep business analytics: facts computed by code, depth setting, hand-over to the marketing team."""
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core.composition import build_review
from app.core.config import Settings
from app.dispatcher import service, updates
from app.dispatcher.executors import Context
from app.dispatcher.hints import compute_hints
from app.insights import metrics, settings as depth_settings
from app.insights.compute import compute
from app.insights.worker import AGENT, RESULT_KIND, run_insights_once
from app.llm.fake import FakeLLMProvider, LLMResponse, LLMUsage
from app.models.domain import Artifact, Decision, Metric, Product, Task, TaskStatus, ToolCall
from app.schedule.source import Booking, Schedule
from app.sso import access
from app.sso.models import StudioBusinessLink
from app.tools import FakeToolGateway

KEY = "k" * 40
TODAY = datetime.now(ZoneInfo("Europe/Moscow")).date()


def row(i, day, time="12:00", status="confirmed", client=1, service_name="Яхта", resource="Катер 1", price=10000,
        made=None):
    made = made or (datetime.combine(day, datetime.min.time()) - timedelta(days=3) + timedelta(hours=9))
    return {"id": i, "date": day.isoformat(), "time": time, "status": status, "service": service_name,
            "resource": resource, "client_name": f"Клиент {client}", "client_phone": "+79990000000",
            "price": price, "client_id": client, "created_at": made.isoformat()}


def schedule(bookings, today=TODAY):
    return Schedule.model_validate({"today": today.isoformat(), "bookings": bookings})


def twelve_bookings():
    days = [TODAY - timedelta(days=d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17)]
    return [row(i, day, client=1 + i % 5, time="19:00" if i % 4 == 0 else "13:00",
                status="cancelled" if i == 3 else "confirmed", service_name="Яхта" if i % 3 else "Катер")
            for i, day in enumerate(days)]


# ---- facts ----------------------------------------------------------------------------------

def test_facts_are_counted_by_definition():
    result = compute(schedule(twelve_bookings()), TODAY, 30, 14)
    by_name = {fact.name: fact.value for fact in result.facts}
    assert by_name["servgo.bookings_total"] == 11  # the cancelled one is not counted
    assert by_name["servgo.bookings_cancelled"] == 1
    assert by_name["servgo.cancelled_share_pct"] == round(100 / 12, 1)
    assert by_name["servgo.revenue_total"] == 110000 and by_name["servgo.average_check"] == 10000
    assert by_name["servgo.clients_unique"] == 5
    assert by_name["servgo.lead_time_median_days"] == 3
    assert by_name["servgo.depth_days"] == 30
    assert not result.low_data


def test_depth_decides_which_days_count():
    old = row(99, TODAY - timedelta(days=40))
    near = row(100, TODAY - timedelta(days=5))
    month = {f.name: f.value for f in compute(schedule([old, near]), TODAY, 30, 14).facts}
    quarter = {f.name: f.value for f in compute(schedule([old, near]), TODAY, 90, 14).facts}
    assert month["servgo.bookings_total"] == 1 and quarter["servgo.bookings_total"] == 2


def test_today_and_future_are_not_part_of_the_past_period_but_are_counted_ahead():
    future = [row(1, TODAY + timedelta(days=2)), row(2, TODAY), row(3, TODAY + timedelta(days=20))]
    by_name = {f.name: f.value for f in compute(schedule(future), TODAY, 30, 14).facts}
    assert by_name["servgo.bookings_total"] == 0
    assert by_name["servgo.upcoming_bookings"] == 2  # the one 20 days away is outside the 14-day window


def test_returning_clients_and_weak_days():
    same = [row(1, date(2026, 10, 5), client=7), row(2, date(2026, 10, 12), client=7), row(3, date(2026, 10, 13), client=8)]
    result = compute(schedule(same), date(2026, 10, 23), 30, 14)
    by_name = {f.name: f.value for f in result.facts}
    assert by_name["servgo.clients_returning"] == 1 and by_name["servgo.clients_returning_share_pct"] == 50.0
    text = " ".join(result.lines)
    assert "Пн (2)" in text and "Дни без единой записи" in text


def test_thin_data_is_flagged_not_hidden():
    result = compute(schedule([row(1, TODAY - timedelta(days=2))]), TODAY, 30, 14)
    assert result.low_data and "Данных пока мало" in " ".join(result.lines)


def test_empty_schedule_does_not_divide_by_zero():
    result = compute(schedule([]), TODAY, 30, 14)
    assert result.bookings == 0 and result.low_data
    assert {f.name: f.value for f in result.facts}["servgo.cancelled_share_pct"] == 0.0


def test_no_client_names_or_contacts_leave_the_module():
    result = compute(schedule(twelve_bookings()), TODAY, 30, 14)
    everything = json.dumps([[f.name, f.label] for f in result.facts] + result.lines + result.table, ensure_ascii=False)
    assert "Клиент " not in everything and "+7999" not in everything


def test_metric_names_fit_the_column():
    long_name = "Очень длинное название услуги " * 5
    result = compute(schedule([row(i, TODAY - timedelta(days=2), service_name=long_name) for i in range(3)]), TODAY, 30, 14)
    assert max(len(f.name) for f in result.facts) <= 100


def test_old_platform_without_client_numbers_still_works():
    plain = {"id": 1, "date": (TODAY - timedelta(days=2)).isoformat(), "time": "10:00", "status": "confirmed",
             "service": "Стрижка", "client_name": "Анна"}
    result = compute(schedule([plain]), TODAY, 30, 14)
    names = {f.name for f in result.facts}
    assert "servgo.clients_unique" not in names and "servgo.lead_time_median_days" not in names


# ---- depth setting --------------------------------------------------------------------------

@pytest.fixture
def product(db):
    row_ = Product(name="Яхты", slug="yachts")
    db.add(row_)
    db.commit()
    return row_


def test_depth_defaults_to_a_month_and_is_saved_per_company(db, product):
    assert depth_settings.depth_for(db, product.id) == 30
    depth_settings.set_depth(db, product.id, 90, "owner")
    assert depth_settings.depth_for(db, product.id) == 90
    depth_settings.set_depth(db, product.id, 14, "owner")
    assert depth_settings.depth_for(db, product.id) == 14


@pytest.mark.parametrize("days", [0, 6, 366, -5])
def test_depth_outside_the_range_is_refused(db, product, days):
    with pytest.raises(ValueError):
        depth_settings.set_depth(db, product.id, days, "owner")
    assert depth_settings.depth_for(db, product.id) == 30


# ---- metrics --------------------------------------------------------------------------------

def test_metrics_are_refreshed_in_place_the_same_day(db, product):
    result = compute(schedule(twelve_bookings()), TODAY, 30, 14)
    first = metrics.store(db, product.id, result)
    ids = {m.id for m in first}
    again = metrics.store(db, product.id, result)
    assert {m.id for m in again} == ids
    assert db.query(Metric).count() == len(result.facts)
    assert all(m.source == "servgo_schedule:30d" for m in again)


def test_other_depth_is_a_separate_set(db, product):
    metrics.store(db, product.id, compute(schedule(twelve_bookings()), TODAY, 30, 14))
    metrics.store(db, product.id, compute(schedule(twelve_bookings()), TODAY, 90, 14))
    assert {m.source for m in db.scalars(select(Metric))} == {"servgo_schedule:30d", "servgo_schedule:90d"}


# ---- through the dispatcher -----------------------------------------------------------------

class FakeBooking:
    def __init__(self):
        self.bookings = twelve_bookings()
        self.calls: list[httpx.Request] = []
        self.down = False

    def __call__(self, request):
        self.calls.append(request)
        if self.down:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(200, json={"today": TODAY.isoformat(), "bookings": self.bookings})


@pytest.fixture
def platform():
    fake = FakeBooking()
    access._transport = httpx.MockTransport(fake)
    yield fake
    access._transport = None


@pytest.fixture
def linked(db, product):
    db.add(StudioBusinessLink(servgo_business_id=11, product_id=product.id))
    db.commit()
    return product


def env():
    return Settings(_env_file=None, servgo_internal_key=KEY, servgo_api_url="https://servgo.test/api",
                    whiteboard_storyboard_model="s", whiteboard_image_model="google/i",
                    whiteboard_speech_model="sp", whiteboard_regions_model="r")


async def chip(db, product, capability, params=None, key="k"):
    ctx = Context(db=db, product=product, settings=env(), account="owner")
    result = await service.handle_message(db, ctx, None, text="", client_key=f"{capability}-{key}", mode="chip",
                                          chip={"label": "x", "capability": capability, "params": params or {}})
    return result["messages"][1]


@pytest.mark.asyncio
async def test_show_insights_reads_the_saved_depth(db, linked, platform):
    answer = await chip(db, linked, "show_insights")
    assert "записей: 11" in answer["text"]
    params = platform.calls[0].url.params
    assert params["date_from"] == (TODAY - timedelta(days=30)).isoformat()
    assert params["date_to"] == (TODAY + timedelta(days=13)).isoformat()
    depth_settings.set_depth(db, linked.id, 90, "owner")
    await chip(db, linked, "show_insights", key="two")
    assert platform.calls[1].url.params["date_from"] == (TODAY - timedelta(days=90)).isoformat()


@pytest.mark.asyncio
async def test_a_period_in_the_request_is_for_that_view_only(db, linked, platform):
    answer = await chip(db, linked, "show_insights", {"days": "60"})
    assert platform.calls[0].url.params["date_from"] == (TODAY - timedelta(days=60)).isoformat()
    assert depth_settings.depth_for(db, linked.id) == 30
    assert "только для этого просмотра" in answer["text"]


@pytest.mark.asyncio
async def test_insights_blocks_offer_the_hand_over(db, linked, platform):
    answer = await chip(db, linked, "show_insights")
    kinds = [b["type"] for b in answer["blocks"]]
    assert "table" in kinds
    chips = [c for b in answer["blocks"] if b["type"] == "chips" for c in b["items"]]
    assert "send_to_team" in {c["capability"] for c in chips}


@pytest.mark.asyncio
async def test_set_depth_saves_and_is_recorded(db, linked, platform):
    answer = await chip(db, linked, "set_insights_depth", {"days": "90"})
    assert depth_settings.depth_for(db, linked.id) == 90 and "90 дн." in answer["text"]
    assert db.scalar(select(ToolCall).where(ToolCall.operation == "set_insights_depth")) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("days", ["3", "1000", "abc"])
async def test_set_depth_out_of_range_asks_again_and_changes_nothing(db, linked, platform, days):
    answer = await chip(db, linked, "set_insights_depth", {"days": days})
    assert "срок в днях" in answer["text"]
    assert depth_settings.depth_for(db, linked.id) == 30


@pytest.mark.asyncio
async def test_without_a_linked_business_it_says_so(db, product, platform):
    answer = await chip(db, product, "show_insights")
    assert "не связано" in answer["text"] and platform.calls == []


@pytest.mark.asyncio
async def test_platform_down_is_said_plainly(db, linked, platform):
    platform.down = True
    answer = await chip(db, linked, "show_insights")
    assert "не отвечает" in answer["text"]
    assert db.query(Metric).count() == 0


@pytest.mark.asyncio
async def test_send_to_team_stores_evidence_and_queues_one_review(db, linked, platform):
    answer = await chip(db, linked, "send_to_team")
    assert "Передал команде" in answer["text"]
    stored = db.query(Metric).count()
    assert stored > 20
    task = db.scalar(select(Task).where(Task.assigned_agent == AGENT))
    assert task.status == TaskStatus.QUEUED and task.product_id == linked.id
    again = await chip(db, linked, "send_to_team", key="two")
    assert "уже разбирает" in again["text"]
    assert db.query(Task).filter(Task.assigned_agent == AGENT).count() == 1
    assert db.query(Metric).count() == stored  # refreshed in place, not duplicated


@pytest.mark.asyncio
async def test_send_to_team_warns_when_data_is_thin(db, linked, platform):
    platform.bookings = twelve_bookings()[:2]
    answer = await chip(db, linked, "send_to_team")
    assert "Данных пока мало" in answer["text"]


def test_hints_offer_insights_only_for_a_linked_company(db, product, tmp_path):
    labels = [c.label for c in compute_hints(db, product, tmp_path, schedule_ready=True)]
    assert "Итоги бизнеса" not in labels
    db.add(StudioBusinessLink(servgo_business_id=11, product_id=product.id))
    db.commit()
    labels = [c.label for c in compute_hints(db, product, tmp_path, schedule_ready=True)]
    assert "Итоги бизнеса" in labels and "Передать данные команде" in labels


# ---- the team's review ----------------------------------------------------------------------

@contextmanager
def same_session(db):
    yield db


def factory(db):
    return lambda: same_session(db)


def review_builder(tmp_path, data):
    provider = FakeLLMProvider(LLMResponse(content=json.dumps(data), model="fake",
                                           usage=LLMUsage(input_tokens=5, output_tokens=7)))
    return provider, lambda db, settings: build_review(db, Settings(products_root=tmp_path), provider, FakeToolGateway())


async def hand_over(db, linked, platform):
    await chip(db, linked, "send_to_team")
    return db.scalar(select(Task).where(Task.assigned_agent == AGENT))


@pytest.mark.asyncio
async def test_team_gets_the_facts_with_the_focus_and_answers_with_evidence(db, linked, platform, tmp_path):
    task = await hand_over(db, linked, platform)
    sample = db.scalar(select(Metric).where(Metric.name == "servgo.bookings_total"))
    data = {"summary": "Записей достаточно, но вечер почти пустой.", "decisions": [
        {"decision_type": "TEST", "rationale": "Вечером всего 3 записи из 11: проверить вечерние предложения.",
         "evidence_references": [{"kind": "metric", "id": str(sample.id)}], "next_tasks": []}]}
    provider, builder = review_builder(tmp_path, data)
    assert await run_insights_once(factory(db), env(), builder=builder) == 1
    request = provider.last_request
    assert "servgo_schedule" in request.system_prompt and "Focus of this review" in request.system_prompt
    context = json.loads(request.messages[0].content)["context"]["records"]["metric"]
    assert any(m["name"] == "servgo.bookings_total" and m["value"] in ("11", "11.0000") for m in context)
    db.refresh(task)
    assert task.status == TaskStatus.COMPLETED
    result = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == RESULT_KIND))
    assert json.loads(result.content)["summary"].startswith("Записей достаточно")
    decision = db.scalar(select(Decision))
    assert decision.evidence_references == [{"kind": "metric", "id": str(sample.id)}]


@pytest.mark.asyncio
async def test_review_failure_marks_the_task_failed(db, linked, platform, tmp_path):
    task = await hand_over(db, linked, platform)
    provider, builder = review_builder(tmp_path, {"summary": "x", "decisions": []})  # violates the contract
    assert await run_insights_once(factory(db), env(), builder=builder) == 1
    db.refresh(task)
    assert task.status == TaskStatus.FAILED
    assert db.scalar(select(Artifact).where(Artifact.kind == RESULT_KIND)) is None


@pytest.mark.asyncio
async def test_nothing_queued_means_nothing_to_do(db, linked, tmp_path):
    _, builder = review_builder(tmp_path, {})
    assert await run_insights_once(factory(db), env(), builder=builder) == 0


@pytest.mark.asyncio
async def test_a_stuck_review_is_failed_not_restarted(db, linked, platform, tmp_path):
    task = await hand_over(db, linked, platform)
    task.status = TaskStatus.RUNNING
    task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=40)
    db.commit()
    _, builder = review_builder(tmp_path, {})
    assert await run_insights_once(factory(db), env(), builder=builder) == 0
    db.refresh(task)
    assert task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_result_comes_back_into_the_conversation_as_decisions(db, linked, platform, tmp_path):
    task = await hand_over(db, linked, platform)
    sample = db.scalar(select(Metric).where(Metric.name == "servgo.bookings_total"))
    data = {"summary": "Вечер почти пустой.", "decisions": [
        {"decision_type": "TEST", "rationale": "Проверить вечерние предложения.",
         "evidence_references": [{"kind": "metric", "id": str(sample.id)}], "next_tasks": []},
        {"decision_type": "KEEP", "rationale": "Яхта берётся чаще всего, оставить как есть.",
         "evidence_references": [], "next_tasks": []}]}
    _, builder = review_builder(tmp_path, data)
    await run_insights_once(factory(db), env(), builder=builder)
    ctx = Context(db=db, product=linked, settings=env(), account="owner")
    conversation = service.open_conversation(db, linked.id, "owner")
    added = updates.collect_updates(db, ctx, conversation)
    assert len(added) == 1
    blocks = added[0].blocks
    table = next(b for b in blocks if b["type"] == "table")
    assert [r[0] for r in table["rows"]] == ["Проверить", "Оставить"]
    assert "опирается на показателей: 1" in table["rows"][0][1] and "без опоры на цифры" in table["rows"][1][1]
    assert "Вечер почти пустой" in added[0].text
    assert updates.collect_updates(db, ctx, conversation) == []  # reported once
