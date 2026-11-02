"""Evidence-based slot suggestions; no outbound contact or speculative revenue.

Bookable starts are provided by BookingBot's real service-specific slot engine.
They are alternative start times, not independent capacity units.
Historical visitors are merely candidates for owner review, not a consent list.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from app.schedule.source import Booking, Schedule
from app.schedule.views import COUNTED


@dataclass(frozen=True)
class ClientSuggestion:
    client_id: int
    name: str
    last_visit: date
    visits: int
    days_since_visit: int
    same_resource: bool


@dataclass(frozen=True)
class SlotOpportunity:
    date: date
    time: str
    resource_id: int
    resource: str
    clients: tuple[ClientSuggestion, ...]


def suggest_clients(history: Schedule, today: date, service_id: int, resource_id: int,
                    *, history_days: int = 180, ahead_days: int = 14,
                    max_clients: int = 3) -> tuple[ClientSuggestion, ...]:
    """Past completed service visitors without a new upcoming booking (within known horizon)."""
    first = today - timedelta(days=history_days)
    visits: dict[int, dict[date, Booking]] = defaultdict(dict)
    booked_ahead: set[int] = set()
    for booking in history.bookings:
        if booking.client_id is None:
            continue
        if today <= booking.date < today + timedelta(days=ahead_days) and booking.status in COUNTED:
            booked_ahead.add(booking.client_id)
        if (first <= booking.date < today and booking.status == "completed"
                and booking.occupies_resource and booking.service_id == service_id):
            visits[booking.client_id][booking.date] = booking

    candidates: list[tuple[tuple, ClientSuggestion]] = []
    for client_id, by_date in visits.items():
        if client_id in booked_ahead:
            continue
        visit_days = sorted(by_date)
        last_day = visit_days[-1]
        elapsed = (today - last_day).days
        # Avoid offering to immediately contact someone who has just visited.
        if elapsed < 21:
            continue
        last = by_date[last_day]
        same_resource = last.resource_id == resource_id
        if len(visit_days) >= 3:
            intervals = [(b - a).days for a, b in zip(visit_days, visit_days[1:])]
            typical = median(intervals)
            overdue = elapsed > max(21, round(typical * 1.25))
        else:
            overdue = False  # 1-2 visits do not establish a stable cadence.
        suggestion = ClientSuggestion(client_id=client_id, name=last.client_name,
                                      last_visit=last_day, visits=len(visit_days),
                                      days_since_visit=elapsed, same_resource=same_resource)
        # Prefer same master, then evidence of a possible delayed repeat visit.
        score = (int(same_resource), int(overdue), len(visit_days), elapsed, -client_id)
        candidates.append((score, suggestion))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return tuple(suggestion for _, suggestion in candidates[:max_clients])


def matching_slots(history: Schedule, availability: Schedule, today: date, service_id: int,
                   *, history_days: int = 180, max_slots: int = 6) -> list[SlotOpportunity]:
    """Match actual service-specific starts to former clients, preserving free-slot order."""
    if not any(service.id == service_id for service in availability.services):
        return []
    result: list[SlotOpportunity] = []
    seen: set[tuple[date, int, str]] = set()
    for slot in sorted(availability.free_slots, key=lambda s: (s.date, s.resource_id or 0)):
        if slot.service_id != service_id or slot.resource_id is None:
            continue  # Legacy availability is not verified for this service.
        for time in sorted(slot.times):
            key = (slot.date, slot.resource_id, time)
            if key in seen:
                continue
            seen.add(key)
            result.append(SlotOpportunity(
                date=slot.date, time=time, resource_id=slot.resource_id,
                resource=slot.resource or "Мастер",
                clients=suggest_clients(history, today, service_id, slot.resource_id,
                                        history_days=history_days),
            ))
            if len(result) >= max_slots:
                return result
    return result
