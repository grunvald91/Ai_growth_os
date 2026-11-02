"""Check working calendars exported to the Studio without database round-trips."""
from datetime import date, timedelta
from types import SimpleNamespace

from web.api.routes.studio_schedule import _working_dates


def employee(duty_schedule=None, on_duty_date=None):
    return SimpleNamespace(duty_schedule=duty_schedule, on_duty_date=on_duty_date)


def test_duty_calendar_and_days_off_are_reflected_in_export():
    start = date(2026, 11, 2)
    days = [start + timedelta(days=i) for i in range(4)]
    staff = employee('["2026-11-02", "2026-11-03", "2026-11-05"]')
    assert _working_dates(staff, days, {date(2026, 11, 3)}) == ["2026-11-02", "2026-11-05"]


def test_on_duty_date_is_respected_without_calendar():
    start = date(2026, 11, 2)
    days = [start + timedelta(days=i) for i in range(3)]
    assert _working_dates(employee(on_duty_date=days[1]), days, set()) == ["2026-11-03"]
    assert _working_dates(employee(), days, {days[0]}) == ["2026-11-03", "2026-11-04"]


def test_invalid_duty_calendar_never_opens_a_resource():
    day = date(2026, 11, 2)
    assert _working_dates(employee(duty_schedule="not json"), [day], set()) == []
    assert _working_dates(employee(duty_schedule='{"unexpected": true}'), [day], set()) == []
