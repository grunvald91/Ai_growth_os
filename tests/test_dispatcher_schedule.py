"""Dispatcher and the booking schedule: read-only, numbers from the platform, honest when it is not there."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.dispatcher.hints import compute_hints
from app.models.domain import Product
from app.sso import access
from app.sso.models import StudioBusinessLink

KEY = "k" * 40
TODAY = datetime.now(ZoneInfo("Europe/Moscow")).date()


def booking(day, time, *, status="confirmed", client="Анна Петрова", service_name="Стрижка", resource="Мария",
            minutes=60, phone="+79990000001", email=None, id=1):
    return {"id": id, "date": day.isoformat(), "time": time, "duration_minutes": minutes, "status": status,
            "service": service_name, "resource": resource, "client_name": client, "client_phone": phone,
            "client_email": email, "price": 1500}


class FakeBooking:
    """Stands in for the booking platform's /internal/studio/schedule."""

    def __init__(self):
        self.bookings: list[dict] = []
        self.free: list[dict] = []
        self.calls: list[httpx.Request] = []
        self.mode = "normal"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.mode == "down":
            raise httpx.ConnectTimeout("timeout")
        if self.mode == "error":
            return httpx.Response(500, json={})
        if self.mode == "garbage":
            return httpx.Response(200, json={"unexpected": True})
        if request.headers.get("x-studio-key") != KEY:
            return httpx.Response(403, json={})
        return httpx.Response(200, json={
            "today": TODAY.isoformat(), "working_hours": "10:00–14:00", "break_hours": "12:00–13:00",
            "step_minutes": 60, "resources": [{"id": 1, "name": "Мария"}, {"id": 2, "name": "Олег"}],
            "bookings": self.bookings, "free_slots": self.free})


@pytest.fixture
def platform():
    fake = FakeBooking()
    access._transport = httpx.MockTransport(fake)
    yield fake
    access._transport = None


@pytest.fixture
def product(db):
    row = Product(name="Салон", slug="salon")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def linked(db, product):
    db.add(StudioBusinessLink(servgo_business_id=12, product_id=product.id))
    db.commit()
    return product


def settings(key=KEY):
    return Settings(_env_file=None, servgo_internal_key=key, servgo_api_url="https://servgo.test/api",
                    whiteboard_storyboard_model="s", whiteboard_image_model="google/i",
                    whiteboard_speech_model="sp", whiteboard_regions_model="r")


async def ask(db, product, view="bookings", period="today", account="owner", key=KEY):
    chip = {"label": "Расписание", "capability": "show_schedule", "params": {"view": view, "period": period}}
    ctx = Context(db=db, product=product, settings=settings(key), account=account)
    result = await service.handle_message(db, ctx, None, text="", client_key=f"k-{view}-{period}-{account}",
                                          mode="chip", chip=chip)
    return result["messages"][1]


def tables(answer):
    return [b for b in answer["blocks"] if b["type"] == "table"]


@pytest.mark.asyncio
async def test_bookings_of_today_come_from_the_platform(db, linked, platform):
    platform.bookings = [booking(TODAY, "10:00", id=1),
                         booking(TODAY, "11:00", status="cancelled", client="Отменивший", id=2),
                         booking(TODAY, "13:00", status="prepayment_sent", client="Борис Иванов", id=3)]
    answer = await ask(db, linked)
    table = tables(answer)[0]
    assert [row[1] for row in table["rows"]] == ["Анна Петрова", "Борис Иванов"]  # the cancelled one is not listed
    assert table["rows"][1][4] == "предоплата отправлена, ждёт вашей проверки"
    assert "Записей сегодня: 2" in answer["text"] and "Отменённых: 1" in answer["text"]
    assert "Предоплату проверить: 1" in answer["text"]


@pytest.mark.asyncio
async def test_empty_day_is_said_plainly(db, linked, platform):
    answer = await ask(db, linked)
    assert "Записей сегодня нет" in answer["text"]
    assert tables(answer) == []


@pytest.mark.asyncio
async def test_week_view_shows_dates(db, linked, platform):
    platform.bookings = [booking(TODAY + timedelta(days=2), "10:00")]
    table = tables(await ask(db, linked, period="week"))[0]
    assert table["columns"][0] == "Когда"
    assert (TODAY + timedelta(days=2)).strftime("%d.%m") in table["rows"][0][0]


@pytest.mark.asyncio
async def test_request_carries_the_key_the_business_and_the_period(db, linked, platform):
    await ask(db, linked, period="tomorrow")
    request = platform.calls[0]
    assert request.headers["x-studio-key"] == KEY
    params = request.url.params
    assert (params["business_id"], params["date_from"], params["date_to"]) == (
        "12", (TODAY + timedelta(days=1)).isoformat(), (TODAY + timedelta(days=1)).isoformat())
    assert "user_id" not in params  # studio login: the operator's link decides


@pytest.mark.asyncio
async def test_person_from_servgo_is_passed_so_servgo_checks_them_again(db, linked, platform):
    await ask(db, linked, account="servgo:7")
    assert platform.calls[0].url.params["user_id"] == "7"


@pytest.mark.asyncio
async def test_next_client_is_the_earliest_future_active_booking_with_contact(db, linked, platform):
    tomorrow = TODAY + timedelta(days=1)
    platform.bookings = [
        booking(TODAY, "00:00", client="Уже был", id=1),
        booking(tomorrow, "09:00", status="cancelled", client="Отменил", id=2),
        booking(tomorrow, "15:00", client="Позже", id=3),
        booking(tomorrow, "11:00", client="Борис Иванов", phone="+79990000002", email="boris@example.com", id=4),
    ]
    answer = await ask(db, linked, view="next")
    assert "Борис Иванов" in answer["text"]
    rows = dict(tables(answer)[0]["rows"])
    assert rows["Телефон"] == "+79990000002" and rows["Почта"] == "boris@example.com"


@pytest.mark.asyncio
async def test_no_next_client_is_said_plainly(db, linked, platform):
    answer = await ask(db, linked, view="next")
    assert "нет" in answer["text"]


@pytest.mark.asyncio
async def test_free_times_use_the_platforms_answer(db, linked, platform):
    platform.free = [{"date": TODAY.isoformat(), "resource_id": 1, "resource": "Мария", "times": ["11:00", "13:00"]},
                     {"date": TODAY.isoformat(), "resource_id": 2, "resource": "Олег", "times": []}]
    answer = await ask(db, linked, view="free")
    assert platform.calls[0].url.params["with_slots"] == "true"
    rows = tables(answer)[0]["rows"]
    assert ["Мария"] == [row[1] for row in rows if "11:00" in row[2]]
    assert ["нет окон"] == [row[2] for row in rows if row[1] == "Олег"]
    assert "Свободно сегодня: 2 окна" in answer["text"]


@pytest.mark.asyncio
async def test_no_free_times_is_said_plainly(db, linked, platform):
    answer = await ask(db, linked, view="free")
    assert "Свободных окон сегодня нет" in answer["text"]


@pytest.mark.asyncio
async def test_load_is_booked_time_over_working_time(db, linked, platform):
    # 10:00-14:00 with a 12:00-13:00 break is 180 working minutes; Maria has 90 of them booked
    platform.bookings = [booking(TODAY, "10:00", minutes=60, id=1), booking(TODAY, "11:00", minutes=30, id=2)]
    answer = await ask(db, linked, view="load")
    rows = {row[0]: row for row in tables(answer)[0]["rows"]}
    assert rows["Мария"][1:] == ["2", "1 ч 30 мин", "50%"]
    assert rows["Олег"][3] == "0%"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["down", "error", "garbage"])
async def test_platform_trouble_is_said_plainly_and_nothing_is_invented(db, linked, platform, mode):
    platform.mode = mode
    answer = await ask(db, linked)
    assert "не вижу" in answer["text"]
    assert tables(answer) == []


@pytest.mark.asyncio
async def test_without_the_key_nothing_is_asked(db, linked, platform):
    answer = await ask(db, linked, key="")
    assert "не вижу" in answer["text"]
    assert platform.calls == []


@pytest.mark.asyncio
async def test_other_company_does_not_see_this_schedule(db, linked, platform):
    other = Product(name="Другая", slug="other")
    db.add(other)
    db.commit()
    platform.bookings = [booking(TODAY, "10:00")]
    answer = await ask(db, other)
    assert "не связано" in answer["text"]
    assert platform.calls == []


def test_schedule_hints_appear_only_for_a_linked_company(db, product, tmp_path):
    labels = [chip.label for chip in compute_hints(db, product, tmp_path)]
    assert "Записи на сегодня" not in labels
    db.add(StudioBusinessLink(servgo_business_id=12, product_id=product.id))
    db.commit()
    labels = [chip.label for chip in compute_hints(db, product, tmp_path)]
    assert "Записи на сегодня" in labels and "Свободные окна на неделю" in labels


@pytest.mark.asyncio
async def test_unknown_view_is_refused_not_guessed(db, linked, platform):
    chip = {"label": "x", "capability": "show_schedule", "params": {"view": "cancel_all"}}
    ctx = Context(db=db, product=linked, settings=settings(), account="owner")
    result = await service.handle_message(db, ctx, None, text="", client_key="bad", mode="chip", chip=chip)
    assert platform.calls == []
    assert not tables(result["messages"][1])
