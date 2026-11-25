"""Deterministic chart selection from the existing computed booking facts."""
from app.insights.compute import Insights, METRIC_PREFIX, WEEKDAY_KEYS, PARTS
from app.schedule.views import WEEKDAYS


def trend_points(insights: Insights) -> tuple[list[dict], str, str]:
    """Daily counts for short periods, daily averages for longer ones."""
    days = insights.daily_bookings
    if len(days) <= 31:
        return ([{"label": day.strftime("%d.%m.%Y"), "value": count} for day, count in days],
                "шт", "Записи по дате посещения, без отменённых и без отдельных строк допуслуг.")
    points = []
    for start in range(0, len(days), 7):
        bucket = days[start:start + 7]
        label = f"{bucket[0][0]:%d.%m}–{bucket[-1][0]:%d.%m.%Y}"
        points.append({"label": label, "value": round(sum(count for _, count in bucket) / len(bucket), 2)})
    return points, "шт/день", "Среднее число записей в день в каждом интервале до 7 дней; последний интервал может быть короче."


def metrics_for(insights: Insights) -> dict | None:
    facts = {f.name.removeprefix(METRIC_PREFIX): f for f in insights.facts}
    keys = [("bookings_total", "Записей без отмен"), ("completed_visits", "Выполнено"),
            ("past_bookings_unverified", "Без отметки о выполнении"), ("bookings_cancelled", "Отменено")]
    if not all(key in facts and facts[key].unit == "шт" for key, _ in keys):
        return None
    return {"type": "metrics_summary", "title": "Записи за период", "source": "BookingBot",
            "period": f"{insights.first:%d.%m.%Y}–{insights.last:%d.%m.%Y}", "unit": "шт",
            "points": [{"label": label, "value": facts[key].value} for key, label in keys]}


def chart_for(insights: Insights, topic: str) -> dict | None:
    facts = {f.name.removeprefix(METRIC_PREFIX): f for f in insights.facts}
    if topic == "all":
        topic = "weekdays"
    unit = "шт"
    kind = "bar"
    note = ""
    if topic == "trend":
        points, unit, note = trend_points(insights)
        if len(points) < 2:
            return None
        if insights.low_data:
            note += " Мало данных; закономерности ориентировочные."
        return {"type": "chart", "kind": "line", "title": "Динамика записей", "unit": unit,
                "source": "BookingBot", "period": f"{insights.first:%d.%m.%Y}–{insights.last:%d.%m.%Y}",
                "note": note, "points": points}
    if topic == "weekdays":
        title = "Записи по дням недели"
        keys = [(f"weekday_{k}_bookings", WEEKDAYS[i]) for i, k in enumerate(WEEKDAY_KEYS)]
        note = "Суммарные записи по дням недели за период, не средние за один день."
    elif topic == "daytime":
        title = "Записи по времени суток"
        keys = [(f"daypart_{k}_bookings", label) for k, label, _, _ in PARTS]
    elif topic == "cancellations":
        title = "Статусы записей"
        kind = "donut"
        keys = [("completed_visits", "Выполнены"), ("past_bookings_unverified", "Без отметки о выполнении"),
                ("bookings_cancelled", "Отменены")]
    elif topic == "clients":
        title = "Клиенты с записями"
        keys = [("clients_unique", "Всего клиентов"), ("clients_returning", "Повторно записывались")]
        unit = "чел"
        note = "Повторная запись входит в общее число клиентов; это не подтверждение визита."
    elif topic == "services":
        title = "Записи по популярным услугам"
        keys = [(k, k.split(":", 1)[1]) for k in facts if k.startswith("service_bookings:")]
        note = "До пяти услуг; дополнительные услуги учитываются как отдельные строки."
    elif topic == "upcoming":
        title = "Темп записей: прошлый период и ближайшие дни"
        keys = [("bookings_per_day_past", "Прошедший период"), ("bookings_per_day_upcoming", "Уже записано вперёд")]
        unit = "шт/день"
        note = "Будущие даты могут ещё заполниться; это не прогноз. Период выше относится к прошлым данным."
    else:
        return None
    expected_unit = "шт" if unit == "шт/день" else unit
    points = [{"label": label, "value": facts[key].value} for key, label in keys
              if key in facts and facts[key].unit == expected_unit and label]
    if len(points) < 2:
        return None
    if kind == "donut" and (len(points) != 3 or not any(p["value"] for p in points)):
        kind = "bar"
    if insights.low_data:
        note = (note + " Мало данных; закономерности ориентировочные.").strip()
    return {"type": "chart", "kind": kind, "title": title, "unit": unit, "source": "BookingBot",
            "period": f"{insights.first:%d.%m.%Y}–{insights.last:%d.%m.%Y}",
            "note": note, "points": points}
