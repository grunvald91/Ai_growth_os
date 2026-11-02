"""Slot marketing: concrete service availability, historical suggestions, no auto-send."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.dispatcher.executors import Context, fill_open_slots, prepare_slot_offer
from app.dispatcher.registry import FillOpenSlotsParams, SlotOfferParams
from app.insights.opportunities import matching_slots
from app.models.domain import Product
from app.schedule.source import Schedule

TODAY = datetime.now(ZoneInfo("Europe/Moscow")).date()
SERVICE = 100
RESOURCE = 4


def row(i, client, days_ago, *, status="completed", service_id=SERVICE, resource_id=RESOURCE):
    return {"id": i, "date": (TODAY - timedelta(days=days_ago)).isoformat(),
            "time": "10:00", "status": status, "service": "Стрижка",
            "service_id": service_id, "resource": "Ирина", "resource_id": resource_id,
            "client_id": client, "client_name": f"Клиент {client}", "occupies_resource": True}


def history():
    data = [
        row(1, 10, 60), row(2, 10, 45), row(3, 10, 30),  # possible overdue repeat
        row(4, 11, 5),   # visited recently; not a target
        row(5, 12, 40),  # already has a future appointment
        row(6, 12, -2, status="confirmed"),
        row(7, 13, 45, status="cancelled"),  # cancelled is not completed
        row(8, 14, 50, service_id=999),  # wrong service
    ]
    return Schedule.model_validate({"today": TODAY.isoformat(), "bookings": data,
                                    "services": [{"id": SERVICE, "name": "Стрижка", "duration_minutes": 60}]})


def available(*, times=None, service_id=SERVICE):
    return Schedule.model_validate({
        "today": TODAY.isoformat(),
        "services": [{"id": SERVICE, "name": "Стрижка", "duration_minutes": 60}],
        "free_slots": [{"date": (TODAY + timedelta(days=1)).isoformat(),
                        "service_id": service_id, "resource_id": RESOURCE,
                        "resource": "Ирина", "times": times if times is not None else ["11:30", "12:00"]}],
    })


def test_only_completed_matching_service_visits_can_produce_suggestions():
    options = matching_slots(history(), available(), TODAY, SERVICE)
    assert len(options) == 2
    assert [[client.client_id for client in option.clients] for option in options] == [[10], [10]]
    assert options[0].clients[0].visits == 3
    paused = history().model_copy(update={"paused_client_ids": [10]})
    assert all(not o.clients for o in matching_slots(paused, available(), TODAY, SERVICE))
    assert not matching_slots(history(), available(service_id=None), TODAY, SERVICE)
    assert not matching_slots(history(), Schedule.model_validate({
        "today": TODAY.isoformat(), "free_slots": available().model_dump(mode="json")["free_slots"]
    }), TODAY, SERVICE)


class FakeSource:
    def __init__(self):
        self.calls = []
        self.history = history()
        self.availability = available()

    def fetch(self, first, last, *, with_slots=False, slot_service_id=None):
        self.calls.append((first, last, with_slots, slot_service_id))
        return self.availability if with_slots else self.history


@pytest.fixture
def owner_context(db, monkeypatch):
    import app.schedule.source as source_module
    product = Product(name="Компания", slug="slot-test")
    db.add(product)
    db.commit()
    source = FakeSource()
    monkeypatch.setattr(source_module, "source_for", lambda *args: source)
    return Context(db=db, product=product, settings=SimpleNamespace(), account="owner"), source


@pytest.mark.asyncio
async def test_dispatcher_service_picker_and_real_slot_candidates(owner_context):
    ctx, source = owner_context
    picker = await fill_open_slots(ctx, FillOpenSlotsParams(period="tomorrow"))
    assert "Выберите услугу" in picker.text
    assert picker.blocks[0]["items"][0]["params"]["service_id"] == str(SERVICE)
    result = await fill_open_slots(ctx, FillOpenSlotsParams(period="tomorrow", service_id=SERVICE))
    assert "проверенные варианты" in result.text
    table = next(b for b in result.blocks if b["type"] == "table")
    assert "Клиент 10" in table["rows"][0][3]
    assert "Клиент 11" not in result.text
    assert any(call[3] == SERVICE and call[2] is True for call in source.calls)
    assert any(c["capability"] == "prepare_slot_offer" for b in result.blocks
               if b["type"] == "chips" for c in b["items"])


@pytest.mark.asyncio
async def test_draft_rechecks_time_and_never_passes_customer_identity(owner_context, monkeypatch):
    import app.content.service as content_service
    ctx, source = owner_context
    seen = []
    def queue(db, product_id, topic, **kwargs):
        seen.append((topic, kwargs))
        return SimpleNamespace(id=uuid4(), status="queued")
    monkeypatch.setattr(content_service, "queue_content", queue)
    args = SlotOfferParams(service_id=SERVICE, resource_id=RESOURCE,
                           day=TODAY + timedelta(days=1), time="11:30")
    result = await prepare_slot_offer(ctx, args)
    assert result.task_id is not None and seen and seen[0][1]["format"] == "post"
    assert "Клиент" not in seen[0][0] and "7999" not in seen[0][0]
    assert "не публикуется" in result.text
    source.availability = available(times=["12:00"])
    denied = await prepare_slot_offer(ctx, args)
    assert not denied.ok
    assert len(seen) == 1
    with pytest.raises(ValueError):
        SlotOfferParams(service_id=SERVICE, resource_id=RESOURCE, day=TODAY, time="29:99")


def test_old_revenue_metrics_are_excluded_from_marketing_context(db, tmp_path):
    from decimal import Decimal
    from datetime import timezone
    from app.models.domain import Metric
    from app.workflows.context import load_context

    company = Product(name="Тест метрик", slug="legacy-metrics")
    db.add(company)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        Metric(product_id=company.id, name="servgo.revenue_total",
               value=Decimal("5000"), unit="₽", source="servgo_schedule:30d",
               observed_at=now, dimensions={}),
        Metric(product_id=company.id, name="servgo.service_revenue:Стрижка",
               value=Decimal("3500"), unit="₽", source="servgo_schedule:30d",
               observed_at=now, dimensions={}),
        Metric(product_id=company.id, name="servgo.estimated_booking_value_total",
               value=Decimal("3500"), unit="₽", source="servgo_schedule:30d",
               observed_at=now, dimensions={}),
    ])
    db.commit()
    context = load_context(db, company, tmp_path)
    names = [record["name"] for record in context["records"]["metric"]]
    assert names == ["servgo.estimated_booking_value_total"]


def test_service_value_is_an_estimate_not_revenue():
    from app.insights.compute import compute
    result = compute(history(), TODAY, 90, 14)
    names = {fact.name for fact in result.facts}
    assert "servgo.service_estimated_value:Стрижка" not in names  # no prices in history()
    assert "servgo.service_revenue:Стрижка" not in names
