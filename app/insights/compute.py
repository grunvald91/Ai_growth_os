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
    # Each line and table row belongs to a topic, so a narrow question gets a narrow answer. "base" is always shown.
    line_topics: list[str] = field(default_factory=list)
    row_topics: list[str] = field(default_factory=list)
    topic: str = "base"

    def line(self, text: str) -> None:
        self.lines.append(text)
        self.line_topics.append(self.topic)

    def row(self, cells: list[str]) -> None:
        self.table.append(cells)
        self.row_topics.append(self.topic)

    def view(self, topic: str = "all") -> tuple[list[str], list[list[str]]]:
        """Lines and table rows for one topic (the base ones always come with it)."""
        keep = lambda t: topic == "all" or t in ("base", topic)  # noqa: E731
        return ([text for text, t in zip(self.lines, self.line_topics) if keep(t)],
                [cells for cells, t in zip(self.table, self.row_topics) if keep(t)])


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
    completed = [b for b in done if b.status == "completed"]
    result.bookings = len(done)
    result.low_data = len(done) < LOW_DATA_BOOKINGS
    facts = result.facts

    def add(name: str, label: str, value: float, unit: str | None = None) -> None:
        facts.append(Fact(METRIC_PREFIX + name, label, float(value), unit))

    add("depth_days", "Сколько дней смотрели назад", depth_days, "дн")
    add("bookings_total", "Записей за период (без отменённых)", len(done), "шт")
    add("completed_visits", "Посещений со статусом «выполнена»", len(completed), "шт")
    add("past_bookings_unverified", "Прошедших записей без отметки о выполнении", len(done) - len(completed), "шт")
    add("bookings_cancelled", "Отменённых записей", len(cancelled), "шт")
    add("cancelled_share_pct", "Доля отмен", _percent(len(cancelled), len(done) + len(cancelled)), "%")
    result.line(f"За период {day_label(first)} – {day_label(last)} ({depth_days} дн.) записей: {len(done)}"
                        + (f", отменено: {len(cancelled)} ({_percent(len(cancelled), len(done) + len(cancelled)):g}%)."
                           if cancelled else ", отмен не было."))
    result.row(["Записей", str(len(done))])
    result.line(f"Из них отмечены выполненными: {len(completed)}. "
                f"Без отметки о выполнении: {len(done) - len(completed)}; считать их посещениями нельзя.")
    result.row(["Подтверждённые выполненные визиты", str(len(completed))])
    result.row(["Отмен", f"{len(cancelled)} ({_percent(len(cancelled), len(done) + len(cancelled)):g}%)"])

    result.topic = "money"
    # BookingBot sends estimated_price, not a payment ledger. Never call these figures
    # revenue or a paid check. Group add-ons into the value of their primary booking.
    priced = [b.price for b in sold if b.price is not None]
    if priced:
        total = sum(priced)
        priced_visits = sum(1 for b in done if b.price is not None)
        add("estimated_booking_value_total", "Расчётная стоимость записей (не оплаченная выручка)", total, "₽")
        result.line(f"Расчётная стоимость записей: {_money(total)}. Это НЕ полученная выручка; "
                    "данных о фактических оплатах здесь нет.")
        result.row(["Расчётная стоимость записей", _money(total)])
        if priced_visits:
            average = total / priced_visits
            add("average_booking_value", "Средняя расчётная стоимость визита (не оплаченный чек)",
                average, "₽")
            result.row(["Средняя расчётная стоимость визита", _money(average)])

    result.topic = "clients"
    # Clients: counted by the platform's client number, never by name; two bookings on one day are one visit.
    # Repeat bookings and repeat *completed* visits are different, especially when
    # old pending/confirmed rows were never marked completed in BookingBot.
    visits = Counter(client for client, _ in {(b.client_id, b.date) for b in done if b.client_id is not None})
    completed_dates: dict[int, list[date]] = defaultdict(list)
    for client, day in sorted({(b.client_id, b.date) for b in completed if b.client_id is not None}):
        completed_dates[client].append(day)
    if visits:
        returning = sum(1 for count in visits.values() if count >= 2)
        add("clients_unique", "Разных клиентов с записью за период", len(visits), "чел")
        add("clients_returning", "Клиентов с повторной записью за период (не факт визита)", returning, "чел")
        add("clients_returning_share_pct", "Доля клиентов с повторной записью за период",
            _percent(returning, len(visits)), "%")
        result.line(f"Клиентов с записями: {len(visits)}. Повторно записывались: {returning} "
                    f"({_percent(returning, len(visits)):g}%). Запись ещё не означает посещение.")
        result.row(["Клиентов с записями", str(len(visits))])
        result.row(["Повторно записывались (2+ даты)", f"{returning} ({_percent(returning, len(visits)):g}%)"])
    if completed_dates:
        completed_returning = sum(len(days) >= 2 for days in completed_dates.values())
        add("clients_completed_unique", "Клиентов с выполненными визитами за период",
            len(completed_dates), "чел")
        add("clients_completed_returning", "Клиентов с 2+ выполненными визитами за период",
            completed_returning, "чел")
        result.line(f"По отметкам «выполнена» посетили: {len(completed_dates)} клиентов; "
                    f"возвращались 2+ раза: {completed_returning}.")
        result.row(["Вернулись по выполненным визитам", str(completed_returning)])

        # Suggested opportunity, not a claim of churn: require >=3 distinct visits
        # (two intervals) and no active booking in the fetched 14-day horizon.
        upcoming_clients = {b.client_id for b in upcoming if b.client_id is not None}
        due = 0
        eligible = 0
        for client_id, dates in completed_dates.items():
            if len(dates) < 3:
                continue
            eligible += 1
            typical_gap = median([(b - a).days for a, b in zip(dates, dates[1:])])
            elapsed = (today - dates[-1]).days
            if elapsed > max(7, round(typical_gap * 1.25)) and client_id not in upcoming_clients:
                due += 1
        if eligible:
            add("clients_possible_return_delay", "Клиентов с потенциальной задержкой повторного визита",
                due, "чел")
            add("clients_return_interval_sample", "Клиентов с минимум 3 выполненными визитами", eligible, "чел")
            result.line(f"Возможность для повторной записи: {due} из {eligible} клиентов с 3+ "
                        f"выполненными визитами вышли за обычный интервал и не записаны на "
                        f"ближайшие {ahead_days} дней. Это гипотеза, не прогноз оттока.")
            result.row(["Возможное запоздание повторного визита", f"{due} из {eligible}"])

    result.topic = "weekdays"
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
        result.line(text)
        result.row(["По дням недели", ", ".join(f"{WEEKDAYS[i]} {by_weekday.get(i, 0)}" for i in range(7))])

    result.topic = "daytime"
    # Part of the day, by visit time.
    hours = [(b, _hour(b)) for b in done]
    parts = {key: sum(1 for _, h in hours if h is not None and low <= h < high) for key, _, low, high in PARTS}
    if any(parts.values()):
        for key, label, _, _ in PARTS:
            add(f"daypart_{key}_bookings", f"Записей: {label}", parts[key], "шт")
        top = max(PARTS, key=lambda p: parts[p[0]])
        result.line("Время визитов: " + ", ".join(f"{label} — {parts[key]}" for key, label, _, _ in PARTS)
                            + f". Чаще всего: {top[1]}.")
        result.row(["Время визитов", ", ".join(f"{label.split(' ')[0]} {parts[key]}" for key, label, _, _ in PARTS)])

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
        result.line(f"Обычно записываются за {median(leads):g} дн. до визита; в день визита — "
                            f"{_percent(same_day, len(leads)):g}% записей.")
        result.row(["Записываются за", f"{median(leads):g} дн. (медиана)"])

    result.topic = "services"
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
        result.line("Чаще всего берут: " + ", ".join(f"«{n}» — {c}" for n, c in services.most_common(3)) + ".")
        result.row(["Услуги", ", ".join(f"{n} {c}" for n, c in services.most_common(3))])
    resources = Counter(_short(b.resource) for b in done if b.resource)
    for name, count in resources.most_common(TOP_ITEMS):
        add(f"resource_bookings:{name}", f"Записей к «{name}»", count, "шт")
    if len(resources) > 1:
        result.line("Загрузка по мастерам и залам: "
                            + ", ".join(f"{n} — {c}" for n, c in resources.most_common(TOP_ITEMS)) + ".")

    result.topic = "upcoming"
    # What is coming: bookings already made for the next days, against the daily pace of the past period.
    per_day_past = len(done) / depth_days
    per_day_ahead = len(upcoming) / ahead_days
    add("upcoming_bookings", f"Записей на ближайшие {ahead_days} дн.", len(upcoming), "шт")
    add("bookings_per_day_past", "Записей в день за прошедший период", round(per_day_past, 2), "шт")
    add("bookings_per_day_upcoming", "Записей в день на ближайшие дни", round(per_day_ahead, 2), "шт")
    result.line(f"На ближайшие {ahead_days} {_count(ahead_days, 'день', 'дня', 'дней')} уже записано: "
                        f"{len(upcoming)}. В среднем {per_day_ahead:.1f} в день, а за прошедший период было "
                        f"{per_day_past:.1f} в день (дальние даты обычно ещё добираются).")
    result.row([f"Уже записано на {ahead_days} дн.", str(len(upcoming))])

    result.topic = "upcoming"  # it explains the daily pace, not cancellations or money
    # Bookings may start well after the beginning of the period (the business joined the platform later).
    # Then the daily pace above is diluted by days without data, and the owner should know that.
    earliest = min((b.date for b in past), default=None)
    if earliest is not None and (earliest - first).days >= 7:
        add("first_booking_day_offset", "Через сколько дней от начала периода появилась первая запись",
            (earliest - first).days, "дн")
        result.line(f"Первая запись в этом периоде — {day_label(earliest)}; до неё записей нет, поэтому "
                            "средние «в день» за прошедший период занижены.")

    result.topic = "base"
    if result.low_data:
        result.line(f"Данных пока мало: всего {len(done)} {_count(len(done), 'запись', 'записи', 'записей')} "
                            f"за период. Закономерности ниже ориентировочные.")
    return result
