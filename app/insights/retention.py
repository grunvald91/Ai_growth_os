"""Read-only return-visit opportunities, using *completed* bookings only.

Owner names appear only in the owner's dispatcher table. The marketing metrics
use counts, not this list. This is a signal for manual review, not churn prediction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from app.schedule.source import Booking, Schedule
from app.schedule.views import COUNTED


@dataclass(frozen=True)
class ReturnCandidate:
    client_id: int
    client_name: str
    last_visit: date
    usual_interval_days: float
    days_since_visit: int
    days_over_threshold: int


def find_return_candidates(schedule: Schedule, today: date, depth_days: int,
                           ahead_days: int) -> tuple[int, list[ReturnCandidate]]:
    """Return (clients with sufficient history, suggested follow-ups).

    Three distinct completed dates are needed for two observed intervals.
    A future booking within the fetched horizon suppresses the suggestion.
    The observation period and the horizon MUST be disclosed in the UI.
    """
    first = today - timedelta(days=depth_days)
    visits: dict[int, dict[date, Booking]] = defaultdict(dict)
    future_clients: set[int] = set()
    for booking in schedule.bookings:
        if booking.client_id is None or not booking.occupies_resource:
            continue
        if today <= booking.date <= today + timedelta(days=ahead_days - 1) and booking.status in COUNTED:
            future_clients.add(booking.client_id)
        if first <= booking.date < today and booking.status == "completed":
            visits[booking.client_id][booking.date] = booking

    eligible = 0
    candidates = []
    for client_id, dates_to_bookings in visits.items():
        days = sorted(dates_to_bookings)
        if len(days) < 3:
            continue
        eligible += 1
        gap = median([(b - a).days for a, b in zip(days, days[1:])])
        elapsed = (today - days[-1]).days
        threshold = max(7, round(gap * 1.25))
        if elapsed <= threshold or client_id in future_clients:
            continue
        last = dates_to_bookings[days[-1]]
        candidates.append(ReturnCandidate(
            client_id=client_id, client_name=last.client_name,
            last_visit=days[-1], usual_interval_days=gap,
            days_since_visit=elapsed, days_over_threshold=elapsed - threshold,
        ))
    candidates.sort(key=lambda c: (-c.days_over_threshold, c.last_visit, c.client_id))
    return eligible, candidates
