from datetime import date

from scripts.seed_100boats_demo_bookings import HOURS, LAST_START, plan_bookings

TODAY = date(2026, 10, 23)
VESSELS = list(range(1, 10))


def test_plan_is_deterministic_and_realistic():
    plan = plan_bookings(TODAY, VESSELS)
    assert plan == plan_bookings(TODAY, VESSELS)
    assert 40 <= len(plan) <= 140
    assert all(9 <= item['hour'] <= LAST_START for item in plan)


def test_no_overlap_on_one_vessel():
    plan = plan_bookings(TODAY, VESSELS)
    seen = {}
    for item in plan:
        key = (item['date'], item['vessel'])
        for other_hour in seen.get(key, []):
            assert abs(other_hour - item['hour']) >= HOURS
        seen.setdefault(key, []).append(item['hour'])


def test_past_is_done_future_has_requests_and_a_payment_to_check():
    plan = plan_bookings(TODAY, VESSELS)
    assert all(i['status'] in ('completed', 'cancelled') for i in plan if i['date'] < TODAY)
    future = [i['status'] for i in plan if i['date'] >= TODAY]
    assert future.count('pending') == 3 and future.count('prepayment_sent') == 2
    assert 'confirmed' in future
