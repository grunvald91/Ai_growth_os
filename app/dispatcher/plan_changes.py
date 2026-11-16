"""Explicit, company-scoped editorial plan changes from dispatcher chat.

The LLM proposes only bounded identifiers and new values. No plan is modified
until the owner confirms the saved card. All final writes use calendar services.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.content.calendar import reschedule_slot
from app.content.plan_edit import update_draft_package
from app.dispatcher.outcome import Confirmation, Context, Outcome, links_block
from app.dispatcher.readers import plans
from app.dispatcher.registry import PlanPackageEditParams, PlanSlotMoveParams
from app.models.domain import Artifact


def _can_edit(ctx: Context) -> bool:
    return ctx.studio_role in (None, "owner", "co_owner")


def _denied() -> Outcome:
    return Outcome("Изменять контент-план может только владелец компании.", ok=False)


async def describe_edit_plan_package(ctx: Context, params: PlanPackageEditParams) -> Outcome | Confirmation:
    if not _can_edit(ctx):
        return _denied()
    _, draft = plans(ctx.db, ctx.product.id)
    if draft is None:
        return Outcome("Нет черновика контент-плана. Утверждённый план здесь не переписываю.",
                       blocks=[links_block(ctx, "plan")], ok=False)
    row, plan = draft
    slots = [slot for slot in plan.get("slots") or [] if slot.get("package_key") == params.package_key]
    if not slots:
        return Outcome("Не нашёл такой пакет в черновике. Откройте контент-план.",
                       blocks=[links_block(ctx, "plan")], ok=False)
    fields = ("topic", "objective", "audience")
    before = {name: slots[0].get(name, "") for name in fields}
    if any(any(slot.get(name, "") != before[name] for name in fields) for slot in slots):
        return Outcome("У пакета разные значения на площадках. Исправьте план перед изменением.",
                       ok=False)
    if any(slot.get("task_id") or slot.get("state") not in (None, "planned", "missed")
           or slot.get("publication_url") or slot.get("telegram_publication_id") for slot in slots):
        return Outcome("Этот пакет уже в работе. Изменение черновика недоступно.", ok=False)
    proposed = params.model_dump(exclude_none=True)
    after = {name: proposed.get(name, before[name]) for name in fields}
    rows = [
        {"label": label, "before": str(before[key]), "after": str(after[key])}
        for key, label in (("topic", "Тема"), ("objective", "Цель"), ("audience", "Аудитория"))
        if after[key] != before[key]
    ]
    if not rows:
        return Outcome("Эти значения уже указаны в контент-плане. Менять нечего.",
                       blocks=[links_block(ctx, "plan")], ok=False)
    return Confirmation(
        title="Изменить пакет в черновике?",
        details=[f"Пакет: {params.package_key}.",
                 f"Площадок в пакете: {len(slots)}.",
                 "Меняется только содержание черновика; публикации и сроки не запускаются."],
        params={"plan_id": str(row.id), "package_key": params.package_key,
                "before": before, "after": after},
        confirm_label="Сохранить изменения",
        change_preview={"rows": rows},
    )


async def confirm_edit_plan_package(ctx: Context, stored: dict, via: str) -> Outcome:
    if not _can_edit(ctx):
        return _denied()
    try:
        plan_id = UUID(str(stored["plan_id"]))
        row = ctx.db.get(Artifact, plan_id)
        if row is None or row.product_id != ctx.product.id or row.kind != "editorial_plan":
            return Outcome("Контент-план этой компании не найден.", ok=False)
        before, after = stored["before"], stored["after"]
        changed = update_draft_package(
            ctx.db, product_id=ctx.product.id, plan_id=plan_id,
            package_key=str(stored["package_key"]),
            topic=str(after["topic"]), objective=str(after["objective"]),
            audience=str(after["audience"]), expected_values=before,
        )
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        ctx.db.rollback()
        return Outcome(f"Не удалось изменить пакет: {exc}", ok=False)
    return Outcome("Пакет обновлён в черновике." if changed else "Пакет уже содержит эти значения.",
                   blocks=[links_block(ctx, "plan")])


def _local_time(value: str, zone: ZoneInfo) -> datetime:
    local = datetime.strptime(value, "%Y-%m-%d %H:%M")
    target = local.replace(tzinfo=zone)
    # Reject missing hours at DST transitions instead of silently moving them.
    if target.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError("В указанном часовом поясе такого местного времени нет")
    return target


async def describe_move_plan_slot(ctx: Context, params: PlanSlotMoveParams) -> Outcome | Confirmation:
    if not _can_edit(ctx):
        return _denied()
    active, draft = plans(ctx.db, ctx.product.id)
    selected = active or draft
    if selected is None:
        return Outcome("Контент-плана нет. Переносить пока нечего.", ok=False)
    row, plan = selected
    slots = [slot for slot in plan.get("slots") or []
             if str(slot.get("slot_id")) == params.slot_id]
    if len(slots) != 1:
        return Outcome("Не нашёл публикацию в текущем календаре.", blocks=[links_block(ctx, "calendar")], ok=False)
    slot = slots[0]
    if (slot.get("telegram_publication_id") or slot.get("publication_url")
            or slot.get("state") in {"scheduled", "published", "delivery_unknown"}):
        return Outcome("Публикация уже отправлена или назначена; сначала проверьте её статус.", ok=False)
    try:
        zone = ZoneInfo(str(plan.get("timezone") or "Europe/Moscow"))
        new_time = _local_time(params.when, zone)
        before_time = datetime.fromisoformat(str(slot["publish_at"])).astimezone(zone)
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError) as exc:
        return Outcome(f"Не удалось прочитать дату: {exc}", ok=False)
    if new_time == before_time:
        return Outcome("Дата не изменилась. Укажите новое время.", ok=False)
    if new_time <= datetime.now(zone):
        return Outcome("Новая дата уже прошла. Выберите будущее время.", ok=False)
    return Confirmation(
        title="Перенести публикацию?",
        details=[f"Площадка: {slot.get('platform') or '—'}.",
                 f"Тема: {str(slot.get('topic') or '')[:200]}.",
                 f"Часовой пояс плана: {zone}.",
                 "Материалы не перегенерируются; публикация сама не отправляется."],
        params={"plan_id": str(row.id), "slot_id": params.slot_id,
                "expected_publish_at": str(slot["publish_at"]),
                "when": new_time.isoformat()},
        confirm_label="Подтвердить перенос",
        change_preview={"rows": [{"label": "Публикация",
                                  "before": before_time.strftime("%d.%m.%Y %H:%M"),
                                  "after": new_time.strftime("%d.%m.%Y %H:%M")}]},
    )


async def confirm_move_plan_slot(ctx: Context, stored: dict, via: str) -> Outcome:
    if not _can_edit(ctx):
        return _denied()
    try:
        plan_id = UUID(str(stored["plan_id"]))
        row = ctx.db.get(Artifact, plan_id)
        if row is None or row.product_id != ctx.product.id or row.kind != "editorial_plan":
            return Outcome("Контент-план этой компании не найден.", ok=False)
        reschedule_slot(ctx.db, plan_id, str(stored["slot_id"]),
                        datetime.fromisoformat(str(stored["when"])),
                        expected_publish_at=str(stored["expected_publish_at"]))
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        ctx.db.rollback()
        return Outcome(f"Не удалось перенести публикацию: {exc}", ok=False)
    return Outcome("Время публикации обновлено в календаре.", blocks=[links_block(ctx, "calendar")])


DESCRIBERS = {
    "edit_plan_package": describe_edit_plan_package,
    "move_plan_slot": describe_move_plan_slot,
}
CONFIRMERS = {
    "edit_plan_package": confirm_edit_plan_package,
    "move_plan_slot": confirm_move_plan_slot,
}
