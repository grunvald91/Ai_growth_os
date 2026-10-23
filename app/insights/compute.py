"""Facts about the booking business, computed here from the platform's own booking rows.

No model writes a number. Every figure has one plain definition, stated in the owner-facing text, so a number
can be checked by hand. Client names and contacts never leave this module: only counts and sums do.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from app.schedule.source import Booking, Schedule
from app.schedule.views import COUNTED, SCHEDULE_TZ, WEEKDAYS, _count, day_label

METRIC_PREFIX = "servgo."
LOW_DATA_BOOKINGS = 10  # below this the picture is too thin to call a pattern
TOP_ITEMS = 5
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PARTS = (("morning", "утро (до 12:00)", 0, 12), ("day", "день (12:00–17:00)", 12, 17),
         ("evening", "вечер (с 17:00)", 17, 24))


@dataclass(frozen=True)
class Fact:
    name: str            # the metric name stored for the marketing team
    label: str           # what it is, in words
    value: float
    unit: str | None = None


@dataclass
class Insights:
    depth_days: int
    first: date          # the first and last day of the past period that was looked at
    last: date
    facts: list[Fact] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    table: list[list[str]] = field(default_factory=list)
    bookings: int = 0
    low_data: bool = False


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _created_day(booking: Booking) -> date | None:
    created = booking.created_at
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)  # the platform stores UTC
    return created.astimezone(ZoneInfo(SCHEDULE_TZ)).date()


def _hour(booking: Booking) -> int | None:
    try:
        return int(booking.time.split(":")[0])
    except (ValueError, IndexError):
        return None


def _short(name: str, limit: int = 40) -> str:
    return " ".join(name.split())[:limit]


def compute(schedule: Schedule, today: date, depth_days: int, ahead_days: int) -> Insights:
    first, last = today - timedelta(days=depth_days), today - timedelta(days=1)
    result = Insights(depth_days=depth_days, first=first, last=last)
    past = [b for b in schedule.bookings if first <= b.date <= last]
    ahead = [b for b in schedule.bookings if today <= b.date <= today + timedelta(days=ahead_days - 1)]
    # Add-ons booked with a main service are separate rows but not separate visits: they count for money and
    # services, never for visits, clients, days or times.
    done = [b for b in past if b.status in COUNTED and b.occupies_resource]
    cancelled = [b for b in past if b.status == "cancelled" and b.occupies_resource]
    upcoming = [b for b in ahead if b.status in COUNTED and b.occupies_resource]
    sold = [b for b in past if b.status in COUNTED]  # every paid line, add-ons included
    result.bookings = len(done)
    result.low_data = len(done) < LOW_DATA_BOOKINGS
    facts = result.facts

    def add(name: str, label: str, value: float, unit: str | None = None) -> None:
        facts.append(Fact(METRIC_PREFIX + name, label, float(value), unit))

    add("depth_days", "Сколько дней смотрели назад", depth_days, "дн")
    add("bookings_total", "Записей за период (без отменённых)", len(done), "шт")
    add("bookings_cancelled", "Отменённых записей", len(cancelled), "шт")
    add("cancelled_share_pct", "Доля отмен", _percent(len(cancelled), len(done) + len(cancelled)), "%")
    result.lines.append(f"За период {day_label(first)} – {day_label(last)} ({depth_days} дн.) записей: {len(done)}"
                        + (f", отменено: {len(cancelled)} ({_percent(len(cancelled), len(done) + len(cancelled)):g}%)."
                           if cancelled else ", отмен не было."))
    result.table.append(["Записей", str(len(done))])
    result.table.append(["Отмен", f"{len(cancelled)} ({_percent(len(cancelled), len(done) + len(cancelled)):g}%)"])

    priced = [b.price for b in sold if b.price]
    if priced:
        total = sum(priced)
        # Per visit with a price: a visit with an add-on is one cheque; visits without a price are left out.
        paid_visits = sum(1 for b in done if b.price) or len(done)
        average = total / paid_visits if paid_visits else 0.0
        add("revenue_total", "Сумма записей по цене услуги", total, "₽")
        add("average_check", "Средний чек", average, "₽")
        result.lines.append(f"Сумма записей по цене услуг: {_money(total)}, средний чек {_money(average)}.")
        result.table.append(["Сумма по ценам услуг", _money(total)])
        result.table.append(["Средний чек", _money(average)])

    # Clients: counted by the platform's client number, never by name; two bookings on one day are one visit.
    visits = Counter(client for client, _ in {(b.client_id, b.date) for b in done if b.client_id is not None})
    if visits:
        returning = sum(1 for count in visits.values() if count >= 2)
        add("clients_unique", "Разных клиентов", len(visits), "чел")
        add("clients_returning", "Клиентов, пришедших 2 раза и больше", returning, "чел")
        add("clients_returning_share_pct", "Доля клиентов, пришедших 2 раза и больше", _percent(returning, len(visits)), "%")
        result.lines.append(f"Клиентов: {len(visits)}. Пришли больше одного раза: {returning} "
                            f"({_percent(returning, len(visits)):g}%), остальные — один раз за период.")
        result.table.append(["Клиентов", str(len(visits))])
        result.table.append(["Вернулись (2+ раза)", f"{returning} ({_percent(returning, len(visits)):g}%)"])

    # Weekdays: by visit date. A weekday with no bookings at all is reported as a fact, not as a verdict.
    by_weekday = Counter(b.date.weekday() for b in done)
    for index, key in enumerate(WEEKDAY_KEYS):
        add(f"weekday_{key}_bookings", f"Записей на {WEEKDAYS[index]}", by_weekday.get(index, 0), "шт")
    if by_weekday:
        busy = max(by_weekday, key=lambda i: (by_weekday[i], -i))
        quiet = min(by_weekday, key=lambda i: (by_weekday[i], i))
        empty = [WEEKDAYS[i] for i in range(7) if i not in by_weekday]
        text = f"Больше всего записей на {WEEKDAYS[busy]} ({by_weekday[busy]})"
        if quiet != busy:
            text += f", меньше всего на {WEEKDAYS[quiet]} ({by_weekday[quiet]})"
        text += "."
        if empty:
            text += f" Дни без единой записи: {', '.join(empty)}."
        result.lines.append(text)
        result.table.append(["По дням недели", ", ".join(f"{WEEKDAYS[i]} {by_weekday.get(i, 0)}" for i in range(7))])

    # Part of the day, by visit time.
    hours = [(b, _hour(b)) for b in done]
    parts = {key: sum(1 for _, h in hours if h is not None and low <= h < high) for key, _, low, high in PARTS}
    if any(parts.values()):
        for key, label, _, _ in PARTS:
            add(f"daypart_{key}_bookings", f"Записей: {label}", parts[key], "шт")
        top = max(PARTS, key=lambda p: parts[p[0]])
        result.lines.append("Время визитов: " + ", ".join(f"{label} — {parts[key]}" for key, label, _, _ in PARTS)
                            + f". Чаще всего: {top[1]}.")
        result.table.append(["Время визитов", ", ".join(f"{label.split(' ')[0]} {parts[key]}" for key, label, _, _ in PARTS)])

    # How far ahead people book.
    # A booking entered after the visit (the owner filling in the past) says nothing about how people book.
    leads = []
    for b in done:
        made = _created_day(b)
        if made is not None and made <= b.date:
            leads.append((b.date - made).days)
    if leads:
        same_day = sum(1 for lead in leads if lead == 0)
        add("lead_time_median_days", "За сколько дней до визита записываются (медиана)", median(leads), "дн")
        add("same_day_share_pct", "Доля записей на тот же день", _percent(same_day, len(leads)), "%")
        result.lines.append(f"Обычно записываются за {median(leads):g} дн. до визита; в день визита — "
                            f"{_percent(same_day, len(leads)):g}% записей.")
        result.table.append(["Записываются за", f"{median(leads):g} дн. (медиана)"])

    # Services and masters/halls.
    services = Counter(_short(b.service) for b in sold)
    revenue: dict[str, int] = defaultdict(int)
    for b in sold:
        revenue[_short(b.service)] += b.price or 0
    for name, count in services.most_common(TOP_ITEMS):
        add(f"service_bookings:{name}", f"Записей на услугу «{name}»", count, "шт")
        if revenue[name]:
            add(f"service_revenue:{name}", f"Сумма по услуге «{name}»", revenue[name], "₽")
    if services:
        result.lines.append("Чаще всего берут: " + ", ".join(f"«{n}» — {c}" for n, c in services.most_common(3)) + ".")
        result.table.append(["Услуги", ", ".join(f"{n} {c}" for n, c in services.most_common(3))])
    resources = Counter(_short(b.resource) for b in done if b.resource)
    for name, count in resources.most_common(TOP_ITEMS):
        add(f"resource_bookings:{name}", f"Записей к «{name}»", count, "шт")
    if len(resources) > 1:
        result.lines.append("Загрузка по мастерам и залам: "
                            + ", ".join(f"{n} — {c}" for n, c in resources.most_common(TOP_ITEMS)) + ".")

    # What is coming: bookings already made for the next days, against the daily pace of the past period.
    per_day_past = len(done) / depth_days
    per_day_ahead = len(upcoming) / ahead_days
    add("upcoming_bookings", f"Записей на ближайшие {ahead_days} дн.", len(upcoming), "шт")
    add("bookings_per_day_past", "Записей в день за прошедший период", round(per_day_past, 2), "шт")
    add("bookings_per_day_upcoming", "Записей в день на ближайшие дни", round(per_day_ahead, 2), "шт")
    result.lines.append(f"На ближайшие {ahead_days} {_count(ahead_days, 'день', 'дня', 'дней')} уже записано: "
                        f"{len(upcoming)}. В среднем {per_day_ahead:.1f} в день, а за прошедший период было "
                        f"{per_day_past:.1f} в день (дальние даты обычно ещё добираются).")
    result.table.append([f"Уже записано на {ahead_days} дн.", str(len(upcoming))])

    # Bookings may start well after the beginning of the period (the business joined the platform later).
    # Then the daily pace above is diluted by days without data, and the owner should know that.
    earliest = min((b.date for b in past), default=None)
    if earliest is not None and (earliest - first).days >= 7:
        add("first_booking_day_offset", "Через сколько дней от начала периода появилась первая запись",
            (earliest - first).days, "дн")
        result.lines.append(f"Первая запись в этом периоде — {day_label(earliest)}; до неё записей нет, поэтому "
                            "средние «в день» за прошедший период занижены.")

    if result.low_data:
        result.lines.append(f"Данных пока мало: всего {len(done)} {_count(len(done), 'запись', 'записи', 'записей')} "
                            f"за период. Закономерности ниже ориентировочные.")
    return result
