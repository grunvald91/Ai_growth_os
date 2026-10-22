"""What the dispatcher shows about the schedule. Every number and name is computed here from the platform's data;
the model never writes them.

Booked time, cancellations and free times are the platform's own figures. Nothing is estimated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.schedule.source import Booking, Schedule

# Statuses of bookings that are still going to happen or already happened. Cancelled ones are counted separately.
ACTIVE = {"pending", "confirmed", "pending_prepayment", "prepayment_sent", "prepayment_confirmed"}
COUNTED = ACTIVE | {"completed"}
STATUS_LABELS = {
    "pending": "ждёт подтверждения", "confirmed": "подтверждена", "completed": "выполнена", "cancelled": "отменена",
    "pending_prepayment": "ждёт предоплату", "prepayment_sent": "предоплата отправлена, ждёт вашей проверки",
    "prepayment_confirmed": "предоплата подтверждена",
}
SCHEDULE_TZ = "Europe/Moscow"  # the booking platform counts "today" and free times in Moscow time
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
PERIOD_LABELS = {"today": "сегодня", "tomorrow": "завтра", "week": "на ближайшие 7 дней"}


@dataclass
class View:
    text: str
    spoken: str
    blocks: list[dict] = field(default_factory=list)


def period_dates(period: str, today: date) -> tuple[date, date]:
    if period == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if period == "week":
        return today, today + timedelta(days=6)
    return today, today


def day_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"


def _when(booking: Booking) -> datetime:
    hours, minutes = booking.time.split(":")
    return datetime.combine(booking.date, datetime.min.time()).replace(hour=int(hours), minute=int(minutes))


def _count(number: int, one: str, few: str, many: str) -> str:
    tail = number % 100
    if 11 <= tail <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(number % 10, many)


def _records(number: int) -> str:
    return f"{number} {_count(number, 'запись', 'записи', 'записей')}"


def _minutes(hours: str | None) -> int | None:
    """Length of a 'HH:MM–HH:MM' interval in minutes."""
    try:
        start, end = (part.strip() for part in hours.replace("-", "–").split("–"))  # type: ignore[union-attr]
        first = int(start[:2]) * 60 + int(start[3:5])
        last = int(end[:2]) * 60 + int(end[3:5])
    except (AttributeError, ValueError):
        return None
    return last - first if last > first else last + 24 * 60 - first


def _suffix(label: str | None) -> str:
    return f" ({label})" if label else ""


def bookings_view(schedule: Schedule, period: str, first: date, last: date, label: str | None = None) -> View:
    counted = sorted((b for b in schedule.bookings if b.status in COUNTED), key=lambda b: (b.date, b.time, b.id))
    cancelled = sum(1 for b in schedule.bookings if b.status == "cancelled")
    when = PERIOD_LABELS[period]
    if not counted:
        text = f"Записей {when} нет." + (f" Отменённых: {cancelled}." if cancelled else "")
        return View(text + _suffix(label), f"Записей {when} нет.")
    single = first == last
    rows = [[b.time if single else f"{day_label(b.date)} {b.time}", b.client_name, b.service, b.resource or "—",
             STATUS_LABELS.get(b.status, b.status)] for b in counted]
    columns = ["Время" if single else "Когда", "Клиент", "Услуга", "Мастер", "Статус"]
    waiting = sum(1 for b in counted if b.status == "prepayment_sent")
    text = f"Записей {when}: {len(counted)}." + (f" Отменённых: {cancelled}." if cancelled else "")
    if waiting:
        text += f" Предоплату проверить: {waiting}."
    spoken = f"Записей {when}: {len(counted)}." + (f" Предоплату нужно проверить: {waiting}." if waiting else "")
    return View(text + _suffix(label), spoken, [{"type": "table", "columns": columns, "rows": rows}])


def next_view(schedule: Schedule, now: datetime, label: str | None = None) -> View:
    upcoming = sorted((b for b in schedule.bookings if b.status in ACTIVE and _when(b) >= now),
                      key=lambda b: (_when(b), b.id))
    if not upcoming:
        return View("Ближайших записей в ближайшие 7 дней нет." + _suffix(label), "Ближайших записей нет.")
    nxt = upcoming[0]
    day = "сегодня" if nxt.date == now.date() else day_label(nxt.date)
    text = f"Следующий — {nxt.client_name}, {day} в {nxt.time}: {nxt.service}."
    details = [["Когда", f"{day_label(nxt.date)} {nxt.time}"], ["Клиент", nxt.client_name], ["Услуга", nxt.service],
               ["Мастер", nxt.resource or "—"], ["Статус", STATUS_LABELS.get(nxt.status, nxt.status)]]
    if nxt.client_phone:
        details.append(["Телефон", nxt.client_phone])
    if nxt.client_email:
        details.append(["Почта", nxt.client_email])
    spoken = f"Следующий — {nxt.client_name}, {day} в {nxt.time}: {nxt.service}."
    return View(text + _suffix(label), spoken, [{"type": "table", "columns": ["Запись", "Данные"], "rows": details}])


def free_view(schedule: Schedule, period: str, label: str | None = None) -> View:
    when = PERIOD_LABELS[period]
    rows, total = [], 0
    for item in sorted(schedule.free_slots, key=lambda s: (s.date, s.resource or "")):
        total += len(item.times)
        rows.append([day_label(item.date), item.resource or "Все", ", ".join(item.times) if item.times else "нет окон"])
    if total == 0:
        return View(f"Свободных окон {when} нет." + _suffix(label), f"Свободных окон {when} нет.")
    word = _count(total, "окно", "окна", "окон")
    return View(f"Свободно {when}: {total} {word}." + _suffix(label), f"Свободно {when}: {total} {word}.",
                [{"type": "table", "columns": ["День", "Кто", "Свободные окна"], "rows": rows}])


def load_view(schedule: Schedule, period: str, first: date, last: date, label: str | None = None) -> View:
    when = PERIOD_LABELS[period]
    work = _minutes(schedule.working_hours)
    if work is None:
        return View("В заведении не заданы часы работы, загрузку посчитать не могу." + _suffix(label),
                    "Часы работы не заданы, загрузку не посчитать.")
    day_minutes = work - (_minutes(schedule.break_hours) or 0)
    days = (last - first).days + 1
    counted = [b for b in schedule.bookings if b.status in COUNTED]
    names = [r.name for r in schedule.resources]
    if not names:
        groups = [("Все записи", counted, day_minutes * days)]
    else:
        groups = [(name, [b for b in counted if b.resource == name], day_minutes * days) for name in names]
        loose = [b for b in counted if b.resource not in names]
        if loose:
            groups.append(("Без мастера", loose, 0))
    rows = []
    for name, own, capacity in groups:
        booked = sum(b.duration_minutes or 0 for b in own)
        rows.append([name, str(len(own)), _hours(booked), _percent(booked, capacity)])
    blocks = [{"type": "table", "columns": ["Мастер", "Записей", "Занято", "Загрузка"], "rows": rows}]
    if days > 1:
        per_day = []
        for offset in range(days):
            day = first + timedelta(days=offset)
            booked = sum(b.duration_minutes or 0 for b in counted if b.date == day)
            count = sum(1 for b in counted if b.date == day)
            per_day.append([day_label(day), str(count), _percent(booked, day_minutes * max(1, len(schedule.resources)))])
        blocks.append({"type": "table", "columns": ["День", "Записей", "Загрузка"], "rows": per_day})
    total_booked = sum(b.duration_minutes or 0 for b in counted)
    capacity = day_minutes * days * max(1, len(schedule.resources))
    text = (f"Загрузка {when}: {_percent(total_booked, capacity)}, записей {len(counted)}. "
            "Выходные мастеров здесь не учитываются.")
    return View(text + _suffix(label), f"Загрузка {when}: {_percent(total_booked, capacity)}.", blocks)


def _hours(minutes: int) -> str:
    return f"{minutes // 60} ч {minutes % 60:02d} мин" if minutes % 60 else f"{minutes // 60} ч"


def _percent(booked: int, capacity: int) -> str:
    return f"{round(booked * 100 / capacity)}%" if capacity > 0 else "—"
