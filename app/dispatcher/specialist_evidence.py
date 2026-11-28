"""Privacy-minimal, bounded projections for expert consultations."""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from datetime import timedelta

from app.dispatcher.policy import Cost, Verdict, verdict
from app.dispatcher.registry import CAPABILITIES
from app.tools import ToolOperation

READS = frozenset({"show_insights", "show_schedule", "list_tasks", "list_publications"})


def preflight(steps):
    from app.dispatcher import executors
    if len(steps) > 2:
        raise ValueError("Слишком много просмотров")
    parsed, seen = [], set()
    for step in steps:
        cap = CAPABILITIES.get(step.capability)
        if (cap is None or cap.id not in READS or cap.operation != ToolOperation.ANALYTICS
                or cap.cost != Cost.NONE or cap.always_confirm or cap.id not in executors.EXECUTORS
                or verdict(cap.operation, cap.cost) != Verdict.RUN):
            raise ValueError("Недопустимый источник консультации")
        params = cap.params.model_validate(step.params).model_dump(mode="json", exclude_none=True)
        if cap.id == "show_schedule":
            if params["view"] not in {"free", "load"}:
                raise ValueError("Только агрегаты расписания")
            params["schedule_presentation"] = "table"
        elif cap.id == "show_insights":
            if params["topic"] == "services":
                raise ValueError("Индивидуальные названия услуг исключены")
            params["presentation"] = "table"
        else:
            if params.get("query"):
                raise ValueError("Произвольный поиск исключён")
            params["list_presentation"] = "table"
        signature = (cap.id, json.dumps(params, sort_keys=True))
        if signature in seen:
            raise ValueError("Повторный просмотр")
        seen.add(signature)
        parsed.append((cap.id, params))
    return parsed


def packet(source, scope, rows, *, status="ok", note=""):
    return {"source": source, "scope": scope, "status": status, "note": note,
            "rows": [[str(label)[:180], str(value)[:300]] for label, value in rows[:60]]}


def insight_facts(insights, topic):
    from app.insights.compute import METRIC_PREFIX
    from app.insights.charts import trend_points
    base = {"depth_days", "bookings_total", "completed_visits", "past_bookings_unverified",
            "bookings_cancelled", "cancelled_share_pct"}
    money = {"estimated_booking_value_total", "average_booking_value"}
    upcoming = {"upcoming_bookings", "bookings_per_day_past", "bookings_per_day_upcoming",
                "first_booking_day_offset"}
    rows = []
    for fact in insights.facts:
        key = fact.name.removeprefix(METRIC_PREFIX)
        group = ("money" if key in money else "upcoming" if key in upcoming else
                 "clients" if key.startswith("clients_") else "weekdays" if key.startswith("weekday_") else
                 "daytime" if key.startswith("daypart_") or key in {"lead_time_median_days", "same_day_share_pct"} else None)
        if key not in base and (group is None or topic not in {"all", group}):
            continue
        if math.isfinite(fact.value):
            # Avoid the default six significant digits of :g rounding large
            # counts or booking values. Preserve the computed precision.
            value = str(int(fact.value)) if float(fact.value).is_integer() else str(fact.value)
            rows.append((fact.label, f"{value} {fact.unit or ''}".strip()))
    note = "Расчётная стоимость записей не выручка; запись не подтверждает визит."
    if insights.low_data:
        note += " Мало данных, закономерности ненадёжны."
    if topic == "trend":
        points, unit, explanation = trend_points(insights)
        rows = [(p["label"], f'{p["value"]:g} {unit}') for p in points]
        note += " " + explanation
    return packet("BookingBot · аналитика", f"{insights.first:%d.%m.%Y}–{insights.last:%d.%m.%Y}", rows,
                  note=note)


def schedule_facts(schedule, first, last, mode, period, *, source_fields=None):
    if mode not in {"free", "load"}:
        return None
    scope = f"{first:%d.%m.%Y}–{last:%d.%m.%Y}, МСК"
    fields = schedule.model_fields_set if source_fields is None else source_fields
    if mode == "free":
        if "free_slots" not in fields:
            return packet("BookingBot · варианты начала записи", scope, [], status="unavailable",
                          note="Источник не передал раздел свободных окон.")
        counts = Counter()
        for item in schedule.free_slots:
            if first <= item.date <= last:
                counts[item.date] += len(item.times)
        rows = [((first + timedelta(days=i)).isoformat(), counts[first + timedelta(days=i)])
                for i in range((last - first).days + 1)]
        return packet("BookingBot · варианты начала записи", scope, rows,
                      note="Число вариантов начала по данным источника, не независимые свободные часы.")
    if "bookings" not in fields:
        return packet("BookingBot · загрузка", scope, [], status="unavailable",
                      note="Источник не передал раздел записей.")
    from app.schedule.views import load_view, COUNTED
    if any(b.duration_minutes is None or b.duration_minutes <= 0 for b in schedule.bookings
           if b.status in COUNTED and b.occupies_resource):
        return packet("BookingBot · загрузка", scope, [], status="unavailable",
                      note="У записей отсутствует корректная длительность; загрузка неизвестна.")
    # Reuse the deterministic calculation without the company label. Never
    # forward its staff table; only the day-level aggregate and summary.
    view = load_view(schedule, period, first, last, None)
    if not view.blocks:
        return packet("BookingBot · загрузка", scope, [], status="unavailable",
                      note="Нет достаточных данных о рабочих часах.")
    daily = next((b for b in view.blocks if b.get("type") == "table"
                  and b.get("columns") == ["День", "Записей", "Загрузка"]), None)
    rows = [(r[0], f"{r[1]} записей; загрузка {r[2]}") for r in daily["rows"]] if daily else []
    return packet("BookingBot · загрузка", scope, rows, note=view.text[:600])


def task_facts(rows, total, page):
    from app.models.domain import TaskStatus
    allowed = {s.value for s in TaskStatus}
    counts = Counter(str(r["state"]) if str(r["state"]) in allowed else "other" for r in rows)
    return packet("ServGo · задания", f"Страница {page}; найдено {total}", sorted(counts.items()),
                  status="ok" if rows else "no_data",
                  note=f"Статусы только {len(rows)} показанных заданий, не всей истории.")


def publication_facts(rows, page, more):
    from app.dispatcher.publications import CHANNELS, STATES
    counts = Counter((CHANNELS.get(p.channel, "Другая площадка"), STATES.get(p.status, "Другой статус")) for p in rows)
    note = f"Статусы {len(rows)} публикаций на странице; {'есть следующая страница' if more else 'следующей страницы нет'}. Не охваты и не продажи."
    if any(p.status == "delivery_unknown" for p in rows):
        note += " Неопределённую доставку сверить с площадкой; повторная отправка запрещена."
    return packet("ServGo · публикации", f"Страница {page}", [(f"{c}: {s}", n) for (c, s), n in sorted(counts.items())],
                  status="ok" if rows else "no_data", note=note)


async def collect(ctx, steps, *, act, deadline):
    parsed = preflight(steps)
    facts = []
    for capability, params in parsed:
        title = CAPABILITIES[capability].title
        scope = ", ".join(f"{k}={v}" for k, v in params.items()
                          if k not in {"presentation", "schedule_presentation", "list_presentation", "query"})
        remaining = deadline - time.monotonic() - 8
        if remaining <= 0 or act is None:
            facts.append(packet(title, scope, [], status="timeout", note="Просмотр не выполнен в пределах времени."))
            continue
        try:
            outcome, confirmation, _ = await asyncio.wait_for(act(ctx, capability, params), timeout=min(remaining, 12))
            if confirmation or outcome.confirmation or outcome.task_id or not outcome.ok:
                raise ValueError("Недопустимый результат")
            fact = outcome.specialist_facts
            if fact is None:
                facts.append(packet(title, scope, [], status="unavailable", note="Источник не предоставил данные."))
            else:
                facts.append({**fact, "scope": fact["scope"] + " · " + scope})
        except Exception:
            ctx.db.rollback()
            facts.append(packet(title, scope, [], status="unavailable", note="Не удалось получить данные источника."))
    return facts


def display_blocks(facts):
    statuses = {"ok": "Получены данные", "no_data": "Нет записей в выборке",
                "unavailable": "Данные недоступны", "timeout": "Просмотр не выполнен"}
    blocks = []
    for fact in facts:
        blocks.append({"type": "text", "text": f'{fact["source"]} · {fact["scope"]}\n{statuses[fact["status"]]}. {fact["note"]}'})
        if fact["rows"]:
            blocks.append({"type": "table", "columns": ["Показатель", "Значение"], "rows": fact["rows"]})
    return blocks
