"""What each ability does. Only these functions touch the platform.

Every executor works through existing services (the same ones the Studio pages use), so tasks
keep their duplicate protection, owner approvals and audit records. Nothing here publishes anything.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.dispatcher.readers import ACTIVE, attention, latest_active_video, plans, upcoming_slots, work_rows
from app.dispatcher.registry import (CAPABILITIES, CHANNEL_LABELS, CancelVideoParams, CreativeParams, DepthParams, GoToParams,
                                     FillOpenSlotsParams, InsightsParams, PackageParams, PostParams, ReturnCandidatesParams, ScheduleParams, SlotOfferParams, VideoParams, page_href, page_label)
from app.dispatcher.outcome import Confirmation, Context, Outcome, links_block, task_block  # noqa: F401
from app.models.domain import Approval, Product, Task, ToolCall


_task_block = task_block
_links = links_block


# ---- reading -----------------------------------------------------------------------------

async def show_work(ctx: Context, params) -> Outcome:
    from app.api.content_studio_activity import work_url
    rows = work_rows(ctx.db, ctx.product.id)
    active = sorted((row for row in rows if row["state"] in ACTIVE),
                    key=lambda row: (row.get("created_at") or "", row["id"]))
    if not active:
        return Outcome("Сейчас ничего не выполняется.", "Сейчас ничего не выполняется.",
                       [_links(ctx, "work")])
    table = {"type": "table", "columns": ["Задание", "Статус"],
             "rows": [[row["title"], row["label"]] for row in active]}
    spoken = f"В работе {len(active)}. Подробности на экране."
    links = {"type": "links", "items": [{"label": f"Открыть: {row['title'][:60]}", "href": work_url(ctx.product.id, row)}
                                        for row in active]}
    return Outcome(f"В работе: {len(active)}.", spoken, [table, links])


async def show_review(ctx: Context, params) -> Outcome:
    notes = sorted(attention(ctx.db, ctx.product.id),
                   key=lambda note: (note.get("created_at") or "", note["id"]))
    if not notes:
        return Outcome("Ничего не ждёт вашего решения.", "Ничего не ждёт вашего решения.", [_links(ctx, "review")])
    # A compact, typed card is more useful than a long wall of 80-character
    # blue links. Preserve the underlying URLs and the original chronology.
    newest = materials.find_material(ctx, None)
    pending_id = str(newest.id) if newest is not None else None
    candidates = [note for note in notes if note["id"] != pending_id]
    shown = candidates[-8:]  # the newest eight, oldest -> newest
    resources = [{
        "kind": note.get("resource_kind", "material"),
        "title": str(note.get("resource_title") or note["text"]).strip()[:140],
        "status": str(note.get("resource_status") or "Требует внимания")[:80],
        "href": note["url"],
    } for note in shown]
    blocks: list[dict] = []
    if resources:
        blocks.append({"type": "resource_list", "items": resources})
    # Older records aren't lost: the full read-only review list is still here.
    if len(candidates) > len(shown):
        blocks.append({"type": "links", "items": [
            {"label": "Посмотреть все материалы", "href": f"/studio/products/{ctx.product.id}/review"}]})
    if newest is not None:  # Keep the actionable approval card near the composer.
        blocks += [materials.card(ctx, newest),
                   {"type": "chips", "items": [{"label": "Утвердить", "capability": "approve_material",
                                                "params": {"material_id": str(newest.id)}}]}]
    return Outcome(f"Требует внимания: {len(notes)}.",
                   f"Требует внимания: {len(notes)}. Материалы на экране.", blocks)


async def show_plan(ctx: Context, params) -> Outcome:
    active, draft = plans(ctx.db, ctx.product.id)
    if active is None and draft is None:
        return Outcome("Контент-плана пока нет. Могу поставить задание его составить.",
                       "Контент-плана пока нет.", [_links(ctx, "plan")])
    blocks: list[dict] = []
    if active is not None:
        slots = upcoming_slots(active[1])
        from app.api.content_studio import platform_label
        # The plan carries its own time zone (Moscow by default); the server clock may be UTC.
        try:
            zone = ZoneInfo(str(active[1].get("timezone") or "Europe/Moscow"))
        except (ValueError, KeyError):  # ZoneInfoNotFoundError is a KeyError
            zone = ZoneInfo("Europe/Moscow")
        if slots:
            blocks.append({"type": "table", "columns": ["Когда", "Площадка", "Тема"],
                           "rows": [[slot["_when"].astimezone(zone).strftime("%d.%m %H:%M"),
                                     platform_label(slot.get("platform")), str(slot.get("topic", ""))]
                                    for slot in slots[:8]]})
        text = f"План утверждён. Впереди публикаций: {len(slots)}." + (" Показаны ближайшие." if len(slots) > 8 else "")
    else:
        text = "Утверждённого плана нет."
    if draft is not None:
        text += " Есть черновик нового плана, он ждёт утверждения."
    blocks.append(_links(ctx, "plan"))
    return Outcome(text, text, blocks)


async def go_to(ctx: Context, params: GoToParams) -> Outcome:
    label = page_label(params.target)
    return Outcome(f"{label} — вот здесь.", f"{label} — на экране кнопка.",
                   [{"type": "links", "items": [{"label": label, "href": page_href(params.target, ctx.product.id)}]}])


async def show_schedule(ctx: Context, params: ScheduleParams) -> Outcome:
    """Bookings, free times, load or the next client. Read-only; numbers and names come from the booking platform."""
    from app.schedule import views
    from app.schedule.source import source_for
    source = source_for(ctx.db, ctx.settings, ctx.product, ctx.account)
    if source is None:
        from app.schedule.source import linked_business_ids
        if len(linked_business_ids(ctx.db, ctx.product.id)) > 1:
            return Outcome("С этим профилем связано несколько заведений. Выберите организацию в ServGo, "
                           "чтобы не смешать данные расписаний.", "Выберите организацию для анализа.",
                           [_links(ctx, "schedule")], no_data=True)
        return Outcome("Расписание записей я пока не вижу: заведение не связано с этой компанией. "
                       "Открыть его можно в админке ServGo.",
                       "Расписание записей я пока не вижу: заведение не связано с компанией.",
                       [_links(ctx, "schedule")], no_data=True)
    now = datetime.now(ZoneInfo(views.SCHEDULE_TZ)).replace(tzinfo=None)
    period = "week" if params.view == "next" else params.period
    first, last = views.period_dates(period, now.date())
    schedule = await asyncio.to_thread(source.fetch, first, last, with_slots=params.view == "free")
    if schedule is None:
        return Outcome("Сейчас не вижу расписание: онлайн-запись не отвечает. Остальное работает.",
                       "Сейчас не вижу расписание, онлайн-запись не отвечает.", [_links(ctx, "schedule")], no_data=True)
    label = source.business_label
    if params.view == "free":
        view = views.free_view(schedule, period, label)
    elif params.view == "load":
        view = views.load_view(schedule, period, first, last, label)
    elif params.view == "next":
        view = views.next_view(schedule, now, label)
    else:
        view = views.bookings_view(schedule, period, first, last, label)
    return Outcome(view.text, view.spoken, [*view.blocks, _links(ctx, "schedule")])


# ---- business insights -------------------------------------------------------------------

def _no_booking_data(ctx: Context, reason: str) -> Outcome:
    if reason == "no_link":
        return Outcome("Данные записей я пока не вижу: заведение не связано с этой компанией. "
                       "Открыть его можно в админке ServGo.",
                       "Данные записей я пока не вижу: заведение не связано с компанией.",
                       [_links(ctx, "schedule")], no_data=True)
    return Outcome("Сейчас не вижу данные записей: онлайн-запись не отвечает. Остальное работает.",
                   "Сейчас не вижу данные записей, онлайн-запись не отвечает.", [_links(ctx, "schedule")], no_data=True)


async def _insights(ctx: Context, days: int | None):
    """(insights, None) or (None, outcome to show instead)."""
    from app.insights import service
    resolved = service.resolve(ctx.db, ctx.settings, ctx.product, ctx.account, days)
    if resolved is None:
        return None, _no_booking_data(ctx, "no_link")
    insights = await asyncio.to_thread(service.read, *resolved)
    if insights is None:
        return None, _no_booking_data(ctx, "unavailable")
    return insights, None


async def show_insights(ctx: Context, params: InsightsParams) -> Outcome:
    """Facts about the business over the chosen depth. Read-only; every figure is computed from the platform's rows."""
    from app.insights.settings import depth_for
    insights, failure = await _insights(ctx, params.days)
    if insights is None:
        return failure
    lines, rows = insights.view(params.topic)
    chips = []
    if params.topic != "all":
        chips.append({"label": "Показать всё", "capability": "show_insights",
                      "params": {"days": str(params.days)} if params.days else {}})
    if params.topic in ("all", "clients"):
        chips.append({"label": "Кого пригласить повторно", "capability": "show_return_candidates", "params": {}})
    chips.append({"label": "Передать маркетинг-команде", "capability": "send_to_team", "params": {}})
    if insights.depth_days < 90:
        chips.append({"label": "Посмотреть за 90 дней", "capability": "show_insights",
                      "params": {"days": "90", **({"topic": params.topic} if params.topic != "all" else {})}})
    note = ""
    if params.days and params.days != depth_for(ctx.db, ctx.product.id):
        note = f"\n(Срок {params.days} дн. — только для этого просмотра. Сохранить как обычный: «смотри за {params.days} дней».)"
    return Outcome("\n".join(lines) + note, " ".join(lines[:2]),
                   [{"type": "table", "columns": ["Показатель", "Значение"], "rows": rows},
                    {"type": "chips", "items": chips}, _links(ctx, "schedule")])



async def fill_open_slots(ctx: Context, params: FillOpenSlotsParams) -> Outcome:
    """Read-only service-specific availability and past-client suggestions."""
    from datetime import timedelta
    from app.insights.opportunities import matching_slots
    from app.schedule.source import source_for
    from app.schedule.views import day_label, period_dates
    from app.insights.settings import AHEAD_DAYS

    source = source_for(ctx.db, ctx.settings, ctx.product, ctx.account)
    if source is None:
        return _no_booking_data(ctx, "no_link")
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if params.service_id is None:
        catalog = await asyncio.to_thread(source.fetch, today, today)
        if catalog is None:
            return _no_booking_data(ctx, "unavailable")
        if not catalog.services:
            return Outcome("Список основных активных услуг пуст или BookingBot ещё не передаёт "
                           "каталог. Слоты без подтверждённой услуги не предлагаю.",
                           "Не вижу список услуг для подбора свободного времени.", no_data=True)
        page_size = 12
        first_item = (params.page - 1) * page_size
        services = catalog.services[first_item:first_item + page_size]
        if not services:
            return Outcome("На этой странице услуг нет. Покажу начало списка.",
                           "Страница услуг пуста.",
                           [{"type": "chips", "items": [{
                               "label": "Первая страница", "capability": "fill_open_slots",
                               "params": {"period": params.period, "page": "1"}}]}])
        chips = [{"label": service.name[:45], "capability": "fill_open_slots",
                  "params": {"service_id": str(service.id), "period": params.period}}
                 for service in services]
        if params.page > 1:
            chips.append({"label": "Предыдущие услуги", "capability": "fill_open_slots",
                          "params": {"period": params.period, "page": str(params.page - 1)}})
        if first_item + page_size < len(catalog.services):
            chips.append({"label": "Другие услуги", "capability": "fill_open_slots",
                          "params": {"period": params.period, "page": str(params.page + 1)}})
        return Outcome("Выберите услугу, чтобы проверить её реальную длительность, "
                       "свободное время у мастеров и историю клиентов. "
                       "Это только анализ, никаких рассылок.",
                       "Выберите услугу для поиска свободных часов.",
                       [{"type": "chips", "items": chips}, _links(ctx, "schedule")])

    # Read history and future bookings without expensive availability calculations.
    history = await asyncio.to_thread(
        source.fetch, today - timedelta(days=180),
        today + timedelta(days=AHEAD_DAYS - 1))
    if history is None:
        return _no_booking_data(ctx, "unavailable")
    service = next((item for item in history.services if item.id == params.service_id), None)
    if service is None:
        return Outcome("Эта услуга не найдена у выбранной организации. "
                       "Выберите услугу из актуального списка.",
                       "Услуга недоступна.", no_data=True)
    first, last = period_dates(params.period, today)
    availability = await asyncio.to_thread(
        source.fetch, first, last, with_slots=True, slot_service_id=params.service_id)
    if availability is None:
        return _no_booking_data(ctx, "unavailable")
    if not availability.services:
        return Outcome("BookingBot ещё не подтверждает доступность по конкретной услуге. "
                       "Не буду предлагать непроверенные окна.", "Нет достоверных данных о свободном времени.",
                       no_data=True)
    options = matching_slots(history, availability, today, params.service_id)
    if not options:
        return Outcome(f"Для услуги «{service.name}» на выбранный период "
                       "подтверждённых свободных стартов не нашёл.",
                       "Свободного времени для этой услуги не найдено.",
                       [{"type": "chips", "items": [
                           {"label": "Другая услуга", "capability": "fill_open_slots",
                            "params": {"period": params.period}},
                           {"label": "Ближайшая неделя", "capability": "fill_open_slots",
                            "params": {"service_id": str(params.service_id), "period": "week"}}]},
                        _links(ctx, "schedule")])

    rows = []
    chips = []
    chosen_resources: set[int] = set()
    for option in options:
        names = ", ".join(f"{c.name} ({c.visits} виз.)" for c in option.clients) or "Нет по истории"
        rows.append([day_label(option.date), option.time, option.resource, names])
        if option.resource_id not in chosen_resources and len(chips) < 3:
            chosen_resources.add(option.resource_id)
            chips.append({"label": f"Подготовить пост: {day_label(option.date)} {option.time} · {option.resource[:18]}",
                          "capability": "prepare_slot_offer",
                          "params": {"service_id": str(params.service_id),
                                     "resource_id": str(option.resource_id),
                                     "day": option.date.isoformat(), "time": option.time}})

    text = (f"«{service.name}»: проверенные варианты начала записи — {len(options)} "
            f"(показаны первые {len(options)}). Клиенты рядом — те, кто раньше завершил эту услугу "
            "и не записан в ближайшие 14 дней; это предложения для проверки, "
            "не согласие на сообщения. Начала могут перекрываться по времени: "
            "нельзя считать их независимыми свободными часами или гарантированной выручкой. "
            "Свободное время не забронировано.")
    return Outcome(text, f"Нашёл {len(options)} вариантов начала записи. Таблица на экране.",
                   [{"type": "table", "columns": ["Дата", "Начало", "Мастер/зал",
                                                 "Клиенты из истории (не аудитория рассылки)"], "rows": rows},
                    {"type": "chips", "items": chips}, _links(ctx, "schedule")])


async def prepare_slot_offer(ctx: Context, params: SlotOfferParams) -> Outcome:
    """Queue a draft for the content team only after a fresh authoritative slot check."""
    from app.schedule.source import source_for
    from app.content.service import queue_content
    source = source_for(ctx.db, ctx.settings, ctx.product, ctx.account)
    if source is None:
        return _no_booking_data(ctx, "no_link")
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if not today <= params.day <= today + timedelta(days=6):
        return Outcome("Для подготовки предложения выберите время в ближайшие семь дней.",
                       "Выберите ближайшую дату.", ok=False)
    schedule = await asyncio.to_thread(source.fetch, params.day, params.day,
                                       with_slots=True, slot_service_id=params.service_id)
    if schedule is None:
        return _no_booking_data(ctx, "unavailable")
    service = next((s for s in schedule.services if s.id == params.service_id), None)
    free = next((s for s in schedule.free_slots if s.resource_id == params.resource_id
                 and s.service_id == params.service_id and params.time in s.times), None)
    if service is None or free is None:
        return Outcome("Это время для выбранной услуги сейчас недоступно. "
                       "Откройте свежий подбор и выберите другое.",
                       "Свободное время изменилось.", ok=False,
                       blocks=[{"type": "chips", "items": [
                           {"label": "Перепроверить время", "capability": "fill_open_slots",
                            "params": {"service_id": str(params.service_id), "period": "week"}}]}])
    # No names, client IDs, contacts or list of CRM candidates enter the content task.
    topic = (f"Мягкое приглашение на услугу «{service.name[:55]}» "
             f"{params.day:%d.%m.%Y} в {params.time}, {str(free.resource or 'специалист')[:55]}. "
             "Без скидок и обещаний брони; ссылка на онлайн-запись ServGo. "
             "Перед публикацией проверить актуальность слота.")
    try:
        task = queue_content(ctx.db, ctx.product.id, topic, format="post", channel=params.channel)
    except (ValueError, LookupError):
        ctx.db.rollback()
        return Outcome("Не удалось передать черновик команде.", "Задание не создано.", ok=False)
    return Outcome("Маркетинговой команде передано задание подготовить пост. "
                   "Клиентские контакты и персональные данные не переданы. "
                   "Пост не публикуется автоматически, а свободное время не резервируется.",
                   "Пост готовится. Публикации и рассылки не запущены.",
                   [_task_block(ctx, task, "Предложение для свободного времени",
                                f"/studio/products/{ctx.product.id}/tasks/{task.id}")],
                   task_id=task.id)


async def show_return_candidates(ctx: Context, params: ReturnCandidatesParams) -> Outcome:
    """Read-only named opportunities for the authenticated owner, never sent to the team."""
    from datetime import timedelta
    from app.insights.retention import find_return_candidates
    from app.insights.settings import AHEAD_DAYS
    from app.schedule.source import source_for
    source = source_for(ctx.db, ctx.settings, ctx.product, ctx.account)
    if source is None:
        return _no_booking_data(ctx, "no_link")
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    schedule = await asyncio.to_thread(
        source.fetch, today - timedelta(days=params.days),
        today + timedelta(days=AHEAD_DAYS - 1))
    if schedule is None:
        return _no_booking_data(ctx, "unavailable")
    today = schedule.today or today
    eligible, candidates = find_return_candidates(schedule, today, params.days, AHEAD_DAYS)
    if not eligible:
        return Outcome(
            f"За последние {params.days} дней не нашёл клиентов с минимум тремя "
            "отмеченными выполненными визитами. Пока нельзя делать вывод о периодичности.",
            "Для оценки повторных визитов пока недостаточно подтверждённой истории.",
            [{"type": "links", "items": [{"label": "Клиенты в CRM",
                                          "href": "https://servgo.ru/admin/contacts"}]}])
    summary = (f"Возможная задержка повторного визита у {len(candidates)} из {eligible} "
               f"клиентов с тремя и более выполненными визитами за {params.days} дней. "
               f"У них нет записи в ближайшие {AHEAD_DAYS} дней. "
               "Это список для проверки, а не доказательство, что клиент потерян.")
    rows = [[person.client_name, person.last_visit.strftime("%d.%m.%Y"),
             f"{person.usual_interval_days:g} дн.", str(person.days_since_visit)]
            for person in candidates[:20]]
    blocks = []
    if rows:
        blocks.append({"type": "table",
                       "columns": ["Клиент", "Последний выполненный визит",
                                   "Обычный интервал", "Дней с визита"], "rows": rows})
        if len(candidates) > 20:
            summary += " Показаны первые 20 случаев с наибольшей задержкой."
    blocks.append({"type": "links", "items": [{"label": "Открыть клиентскую CRM",
                                              "href": "https://servgo.ru/admin/contacts"}]})
    return Outcome(summary, f"Возможная задержка у {len(candidates)} клиентов. Список на экране.", blocks)


async def set_insights_depth(ctx: Context, params: DepthParams) -> Outcome:
    from app.insights.settings import set_depth
    from app.schedule.views import _count
    set_depth(ctx.db, ctx.product.id, params.days, ctx.account)
    ctx.db.add(ToolCall(product_id=ctx.product.id, tool_name="dispatcher", operation="set_insights_depth",
                        request_data={"params": {"days": params.days}, "account": ctx.account},
                        response_data={"ok": True}, status="executed", executed_at=datetime.now(timezone.utc)))
    ctx.db.commit()
    word = _count(params.days, "день", "дня", "дней")
    return Outcome(f"Готово. Теперь при разборе бизнеса смотрю назад на {params.days} {word}.",
                   f"Готово, теперь смотрю назад на {params.days} {word}.",
                   [{"type": "chips", "items": [{"label": "Показать итоги бизнеса", "capability": "show_insights",
                                                 "params": {}}]}])


async def send_to_team(ctx: Context, params) -> Outcome:
    """Store the facts as metrics (evidence) and queue the marketing team's review over them. Publishes nothing."""
    from app.insights import metrics
    from app.insights.worker import queue
    insights, failure = await _insights(ctx, None)
    if insights is None:
        return failure
    rows = metrics.store(ctx.db, ctx.product.id, insights)
    task, created = queue(ctx.db, ctx.product.id, insights.depth_days, len(rows))
    block = _task_block(ctx, task, "Разбор данных бизнеса", f"/studio/products/{ctx.product.id}/tasks/{task.id}")
    if not created:
        return Outcome("Команда уже разбирает цифры бизнеса. Сообщу, когда будут решения.",
                       "Команда уже разбирает цифры. Сообщу, когда будут решения.", [block], task_id=task.id)
    from app.schedule.views import _count
    text = (f"Передал команде цифры записей за {insights.depth_days} дн. "
            f"({len(rows)} {_count(len(rows), 'показатель', 'показателя', 'показателей')}). "
            "Разбор займёт несколько минут: вернусь с решениями, что оставить, остановить, усилить и проверить. "
            "Ничего не публикуется и не тратится на рекламу.")
    if insights.low_data:
        text += f" Данных пока мало (записей: {insights.bookings}), поэтому команда будет осторожна в выводах."
    return Outcome(text, "Передал цифры команде. Скоро вернусь с решениями.", [block], task_id=task.id)


# ---- creating work -----------------------------------------------------------------------

def _context_details(params) -> list[str]:
    details = []
    if params.audience:
        details.append(f"Аудитория: {params.audience}.")
    if params.editorial_brief:
        preview = params.editorial_brief[:180]
        details.append(f"Требования: {preview}" + ("…" if len(params.editorial_brief) > 180 else ""))
    return details


async def describe_create_post(ctx: Context, params: PostParams) -> Confirmation:
    return Confirmation(
        title="Подготовить пост?",
        details=[f"Тема: {params.topic}.", f"Площадка: {CHANNEL_LABELS[params.channel]}.",
                 *_context_details(params),
                 "Готовый текст придёт на проверку; автоматической публикации нет."],
        params=params.model_dump(exclude_none=True), confirm_label="Создать",
    )


async def create_post(ctx: Context, params: PostParams) -> Outcome:
    from app.content.service import queue_content
    try:
        task = queue_content(ctx.db, ctx.product.id, params.topic, format="post", channel=params.channel,
                             audience=params.audience, editorial_brief=params.editorial_brief)
    except (ValueError, LookupError):
        ctx.db.rollback()
        return Outcome("Не получилось поставить пост в работу: проверьте тему и площадку.",
                       "Не получилось поставить пост в работу.", ok=False)
    label = CHANNEL_LABELS[params.channel]
    text = f"Принял. Пишу пост для {label} на тему «{params.topic}». Сообщу, когда будет готов."
    return Outcome(text, f"Принял, пишу пост для {label}. Скоро будет готов.",
                   [_task_block(ctx, task, f"Пост для {label}: {params.topic}",
                                f"/studio/products/{ctx.product.id}/tasks/{task.id}")], task_id=task.id)


async def describe_create_creative(ctx: Context, params: CreativeParams) -> Confirmation:
    labels = {"story": "Stories", "short_video": "Видеосценарий", "static_image": "Изображение"}
    item = labels[params.format]
    details = [f"Тема: {params.topic}.", f"Формат: {item}."]
    if params.frame_count is not None:
        details.append(f"Экранов: {params.frame_count}.")
    platform = params.platform or {"story": "telegram", "short_video": "youtube_shorts", "static_image": "telegram"}[params.format]
    details.append(f"Площадка: {platform}.")
    if params.format in {"story", "static_image"}:
        from app.content.creative_images import image_aspect_ratio
        details.append(f"Пропорции: {image_aspect_ratio(params.format, platform, requested=params.aspect_ratio)}.")
        selected = params.image_model or "auto"
        details.append(f"Генерация изображений: {selected}.")
    details.extend(_context_details(params))
    if params.format == "short_video":
        details.append("Это видеосценарий, не готовый MP4.")
    else:
        details.append("После раскадровки будут созданы готовые PNG. Платные вызовы модели начинаются только после подтверждения.")
    details.append("Без отдельного согласования ничего не публикуется.")
    return Confirmation(title=f"Подготовить {item}?", details=details,
                        params=params.model_dump(exclude_none=True), confirm_label="Создать")


async def confirm_create_creative(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.content.creative import CreativeRequest, queue_creative
    from pydantic import ValidationError
    try:
        params = CreativeParams.model_validate(stored)
        command = CreativeRequest(mode="creative", product_id=ctx.product.id, topic=params.topic,
                                  format=params.format,
                                  platform=params.platform or {"story": "telegram", "short_video": "youtube_shorts", "static_image": "telegram"}[params.format],
                                  frame_count=params.frame_count, audience=params.audience,
                                  editorial_brief=params.editorial_brief,
                                  image_model=(params.image_model or
                                               ("auto" if params.format in {"story", "static_image"} else "none")),
                                  aspect_ratio=params.aspect_ratio,
                                  brand_mode="auto" if params.format == "story" else "classic")
        task = queue_creative(ctx.db, command, settings=ctx.settings)
    except (ValueError, ValidationError, LookupError):
        ctx.db.rollback()
        return Outcome("Не удалось создать задание. Проверьте тему, формат и площадку.", ok=False)
    label = {"story": "Stories", "short_video": "Видеосценарий", "static_image": "Изображение"}[params.format]
    amount = f" ({params.frame_count} экранов)" if params.frame_count else ""
    return Outcome(f"Принял. Готовлю {label}{amount} на тему «{params.topic}». Результат появится в заданиях.",
                   blocks=[_task_block(ctx, task, f"{label}{amount}: {params.topic}",
                                       f"/studio/products/{ctx.product.id}/tasks/{task.id}")], task_id=task.id)


async def describe_create_package(ctx: Context, params: PackageParams) -> Confirmation:
    return Confirmation(title="Создать полный пакет?",
        details=[f"Тема: {params.topic}.", "Включены статья, адаптации и видеосценарии.",
                 *_context_details(params),
                 "Готовый анимированный MP4 не входит и отдельно оплачивается.",
                 "Публикация не запускается автоматически."],
        params=params.model_dump(exclude_none=True), confirm_label="Создать")


async def confirm_create_package(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.content.service import queue_content
    try:
        params = PackageParams.model_validate(stored)
        task = queue_content(ctx.db, ctx.product.id, params.topic, format="package",
                             audience=params.audience, editorial_brief=params.editorial_brief)
    except (ValueError, LookupError):
        ctx.db.rollback()
        return Outcome("Не удалось создать пакет. Уточните тему.", ok=False)
    return Outcome(f"Принял. Готовлю полный пакет на тему «{params.topic}».",
        blocks=[_task_block(ctx, task, f"Полный пакет: {params.topic}",
                            f"/studio/products/{ctx.product.id}/tasks/{task.id}")], task_id=task.id)


async def make_plan(ctx: Context, params) -> Outcome:
    from app.content.calendar import queue_plan
    try:
        task = queue_plan(ctx.db, ctx.product.id)
    except LookupError:
        ctx.db.rollback()
        return Outcome("Компания не найдена.", ok=False)
    except ValueError:
        ctx.db.rollback()
        return Outcome("Не удалось поставить контент-план в очередь. Проверьте профиль компании "
                       "и откройте раздел «Контент-план» для повторного запуска.",
                       blocks=[_links(ctx, "plan")], ok=False)
    # A task ID is the receipt; never claim a new draft already exists.
    text = ("Контент-план готовится. Задание уже в работе, его статус доступен по ссылке."
            if task.status.value == "running" else
            "Задание на контент-план в очереди. Результат появится после генерации "
            "и будет ожидать вашего утверждения.")
    return Outcome(text, text,
                   [_task_block(ctx, task, "Контент-план", f"/studio/products/{ctx.product.id}/tasks/{task.id}"),
                    _links(ctx, "plan")],
                   task_id=task.id)


async def describe_seo_factory(ctx: Context, params) -> Outcome | Confirmation:
    from app.seo.activation import is_enabled
    if not is_enabled(ctx.db, ctx.product.id):
        href = f"/studio/products/{ctx.product.id}/seo"
        return Outcome("SEO-завод для этой компании выключен. Подключите его кнопкой в разделе SEO-завод.",
                       "SEO-завод выключен. Подключите его в настройках компании.",
                       [{"type": "links", "items": [{"label": "Подключить SEO-завод", "href": href}]}], ok=False)
    focus = getattr(params, "focus", None)
    horizon_days = getattr(params, "horizon_days", 7)
    return Confirmation(
        title="Запустить SEO-завод?",
        details=([f"Фокус: {focus}."] if focus else []) + [
            "План на месяц (30 дней)." if horizon_days == 30 else "План на неделю (7 дней).",
            "Команда соберёт реальные запросы клиентов из подсказок Яндекса и Google.",
            "Составит контент-план статей под воронку и поставит статьи в работу.",
            "Каждая статья проверяется критиком; чистые статьи сразу выходят в блог, остальные ждут вас.",
            "Это много обращений к ИИ: чем больше статей в плане, тем дольше и дороже."],
        params={"focus": focus, "horizon_days": horizon_days}, paid=True, confirm_label="Запустить")


async def confirm_seo_factory(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.seo.factory import queue_factory
    try:
        task = queue_factory(ctx.db, ctx.product.id, stored.get("focus"),
                             horizon_days=stored.get("horizon_days", 7))
    except LookupError:
        ctx.db.rollback()
        return Outcome("Компания не найдена.", ok=False)
    return Outcome("Запустил SEO-завод. Сообщу, когда план будет готов и статьи пойдут в работу.",
                   "Запустил SEO-завод.", [_task_block(ctx, task, "SEO-завод", f"/studio/products/{ctx.product.id}/tasks/{task.id}")],
                   task_id=task.id)


async def describe_approve_plan(ctx: Context, params) -> Outcome | Confirmation:
    _, draft = plans(ctx.db, ctx.product.id)
    if draft is None:
        return Outcome("Черновика плана на утверждение нет. Могу составить новый.", "Черновика плана нет.", ok=False)
    row, plan = draft
    count = len(plan.get("slots") or [])
    return Confirmation(
        title="Утвердить контент-план?",
        details=[f"Публикаций в плане: {count}.", CAPABILITIES["approve_plan"].consequence,
                 "Сами публикации по-прежнему подтверждаются отдельно."],
        params={"plan_id": str(row.id)})


async def confirm_approve_plan(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.content.calendar import approve_plan
    from app.models.domain import Artifact
    try:
        plan_id = UUID(str(stored["plan_id"]))
    except (KeyError, ValueError):
        return Outcome("Не нашёл план для утверждения.", ok=False)
    row = ctx.db.get(Artifact, plan_id)
    if row is None or row.product_id != ctx.product.id:
        return Outcome("Не нашёл план для утверждения.", ok=False)
    try:
        approve_plan(ctx.db, ctx.settings, plan_id)
    except ValueError as exc:
        ctx.db.rollback()
        return Outcome(str(exc), str(exc), ok=False)
    ctx.db.add(Approval(product_id=ctx.product.id, artifact_id=plan_id, action_type="dispatcher:approve_plan",
                        risk_level="YELLOW", status="approved", requested_by=ctx.account, decided_by=ctx.account,
                        decided_at=datetime.now(timezone.utc), reason=f"via={via}"))
    ctx.db.commit()
    return Outcome("План утверждён. Команда начнёт готовить материалы.", "План утверждён.", [_links(ctx, "plan")])


async def whiteboard_video(ctx: Context, params: VideoParams) -> Outcome:
    from app.api.whiteboard import CreateJob, _queue, job_state
    try:
        body = CreateJob(topic=params.topic, duration_seconds=int(params.length),
                         call_to_action=params.call_to_action or "")
        task = _queue(ctx.db, ctx.product, body, ctx.settings)
        state = job_state(ctx.db, ctx.product.id, task.id)
    except HTTPException as exc:
        ctx.db.rollback()
        return Outcome(str(exc.detail), ok=False)
    href = f"/studio/products/{ctx.product.id}/whiteboard/{task.id}"
    if state["status"] != "waiting_approval":
        return Outcome(f"Такое видео уже в работе: «{params.topic}».", "Такое видео уже в работе.",
                       [_task_block(ctx, task, f"Видео: {params.topic}", href)], task_id=task.id)
    confirmation = Confirmation(
        title="Запустить рисованное видео?",
        details=[f"Тема: {params.topic}.",
                 "Длина: около минуты, 6 сцен." if params.length == "60" else "Длина: около 30 секунд, 3 сцены.",
                 f"Призыв в конце: {params.call_to_action}." if params.call_to_action
                 else "Призыв в конце: подписаться на канал и поставить лайк.",
                 f"Это платная генерация: до {state['max_api_calls']} обращений к ИИ.",
                 "Пока вы не подтвердите, ничего не списывается."],
        params={"task_id": str(task.id), "request_digest": state["request_digest"]},
        paid=True, confirm_label="Запустить")
    return Outcome("Подготовил задание на видео. Оно платное, поэтому нужно ваше подтверждение.",
                   f"Подготовил задание на видео «{params.topic}». Это платная генерация. Запускаем?",
                   [_task_block(ctx, task, f"Видео: {params.topic}", href)], task_id=task.id,
                   confirmation=confirmation)


async def confirm_whiteboard_video(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.api.whiteboard import ApproveJob, _approve
    try:
        task_id = UUID(str(stored["task_id"]))
        body = ApproveJob(request_digest=str(stored["request_digest"]), confirmed=True)
        await _approve(ctx.db, ctx.product.id, task_id, body, ctx.account, ctx.settings)
    except (KeyError, ValueError):
        return Outcome("Подтверждение относится к другой версии задания. Откройте видео и проверьте его.", ok=False)
    except HTTPException as exc:
        ctx.db.rollback()
        return Outcome(str(exc.detail), str(exc.detail), ok=False)
    href = f"/studio/products/{ctx.product.id}/whiteboard/{task_id}"
    return Outcome("Запустил рисованное видео. Оно появится в ленте, когда будет готово.",
                   "Запустил рисованное видео.", [{"type": "links", "items": [{"label": "Ход создания", "href": href}]}],
                   task_id=task_id)


async def cancel_video(ctx: Context, params: CancelVideoParams) -> Outcome:
    from app.content.whiteboard.jobs import cancel_job
    if params.task_id:
        try:
            task = ctx.db.get(Task, UUID(params.task_id))
        except ValueError:
            task = None
        if task is None or task.product_id != ctx.product.id or task.assigned_agent != "whiteboard_renderer":
            return Outcome("Не нашёл такое видео.", "Не нашёл такое видео.", ok=False)
    else:
        task = latest_active_video(ctx.db, ctx.product.id)
        if task is None:
            return Outcome("Нет рисованного видео, которое можно отменить.", "Отменять нечего.", ok=False)
    cancel_job(ctx.db, ctx.product.id, task.id)
    return Outcome("Видео отменено.", "Видео отменено.", task_id=task.id)


# ---- refusals ----------------------------------------------------------------------------

REFUSALS = {
    "budget": "Бюджет я сам не меняю.", "pricing": "Цены я сам не меняю.",
    "access": "Доступы и права я сам не меняю.", "delete": "Удалять данные я сам не буду.",
    "credentials": "Ключи и пароли я не трогаю.", "billing": "Платёжные данные я не меняю.",
}
REFUSAL_PAGES = {"access": "profile", "pricing": "admin", "billing": "admin", "budget": "admin"}


async def restricted_action(ctx: Context, params) -> Outcome:
    reason = REFUSALS[params.kind]
    text = reason + " Это делается вручную владельцем в настройках."
    page = REFUSAL_PAGES.get(params.kind)
    return Outcome(text, reason, [_links(ctx, page)] if page else [], ok=False)


EXECUTORS = {
    "show_work": show_work, "show_review": show_review, "show_plan": show_plan, "go_to": go_to,
    "show_schedule": show_schedule, "show_insights": show_insights, "show_return_candidates": show_return_candidates,
    "fill_open_slots": fill_open_slots, "prepare_slot_offer": prepare_slot_offer,
    "set_insights_depth": set_insights_depth, "send_to_team": send_to_team, "create_post": create_post, "make_plan": make_plan,
    "whiteboard_video": whiteboard_video, "cancel_video": cancel_video, "restricted_action": restricted_action,
}
# Abilities that always ask first: describe what will happen, run it only after a confirmation.
DESCRIBERS = {"approve_plan": describe_approve_plan, "seo_factory": describe_seo_factory,
              "create_post": describe_create_post,
              "create_creative": describe_create_creative,
              "create_package": describe_create_package}
CONFIRMERS = {"approve_plan": confirm_approve_plan, "whiteboard_video": confirm_whiteboard_video,
              "create_post": lambda ctx, stored, via: create_post(ctx, PostParams.model_validate(stored)),
              "create_creative": confirm_create_creative,
              "create_package": confirm_create_package,
              "seo_factory": confirm_seo_factory}

# Materials and publication live in their own module; it imports the shared types from `outcome`, not from here.
from app.dispatcher import materials  # noqa: E402

EXECUTORS.update(materials.EXECUTORS)

from app.dispatcher.owner_views import recommend_today, show_recent_videos  # noqa: E402

EXECUTORS.update({"recommend_today": recommend_today, "show_recent_videos": show_recent_videos})
DESCRIBERS.update(materials.DESCRIBERS)
CONFIRMERS.update(materials.CONFIRMERS)
