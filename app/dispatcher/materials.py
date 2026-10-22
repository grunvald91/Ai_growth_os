"""Ready materials and publication: show a material, approve it, send a post for rework, schedule a post.

Approval and scheduling go through the same functions as the Studio pages (`app.content.review`), so the
dispatcher can never do what a page would refuse. Approving and scheduling always ask first (YELLOW).
Nothing here sends anything to a channel: a scheduled post is sent by the publishing worker, if it is switched on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.content.review import (REVIEW_KINDS, ReviewError, approve_material, material_data, needs_fact_check,
                                schedule_owner_post)
from app.content.revisions import queue_post_edit
from app.content.telegram_publishing import connected_channels
from app.dispatcher.outcome import Confirmation, Context, Outcome, links_block, task_block
from app.dispatcher.registry import MaterialParams, ReviseParams, SchedulePostParams
from app.models.domain import Approval, Artifact, ArtifactStatus, Publication

MOSCOW = ZoneInfo("Europe/Moscow")
KIND_LABELS = {"channel_text": "Пост", "article": "Статья", "creative_brief": "Креатив",
               "video_script": "Сценарий видео", "video_render": "Видео"}
STATUS_LABELS = {"awaiting_approval": "Ждёт вашего решения", "approved": "Утверждён", "published": "Опубликован",
                 "rejected": "Отклонён", "draft": "Черновик"}
EXCERPT_CARD = 600
EXCERPT_CONFIRM = 240


def _record_approval(ctx: Context, row: Artifact, action: str, via: str) -> Approval:
    """The owner's yes, kept next to the change it allowed."""
    approval = Approval(product_id=ctx.product.id, artifact_id=row.id, action_type=action, risk_level="YELLOW",
                        status="approved", requested_by=ctx.account, decided_by=ctx.account,
                        decided_at=datetime.now(timezone.utc), reason=f"via={via}")
    ctx.db.add(approval)
    ctx.db.commit()
    return approval


def _status(row: Artifact) -> str:
    return getattr(row.status, "value", str(row.status))


def _uuid(text: str | None) -> UUID | None:
    try:
        return UUID(str(text)) if text else None
    except ValueError:
        return None


def excerpt(row: Artifact, limit: int) -> str:
    data = material_data(row)
    text = str(data.get("body") or data.get("text") or data.get("summary") or data.get("lead") or "").strip()
    if row.kind == "video_render" and not text:
        text = "Видео готово к просмотру."
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _page(ctx: Context, row: Artifact) -> str:
    return f"/studio/products/{ctx.product.id}/materials/{row.id}"


def find_material(ctx: Context, material_id: str | None, *, kinds=REVIEW_KINDS, awaiting_only: bool = True):
    """The named material of this company, or the newest one waiting for the owner."""
    if material_id:
        wanted = _uuid(material_id)
        row = ctx.db.get(Artifact, wanted) if wanted else None
        if row is None or row.product_id != ctx.product.id or row.kind not in kinds:
            return None
        return row
    query = select(Artifact).where(Artifact.product_id == ctx.product.id, Artifact.kind.in_(kinds))
    if awaiting_only:
        query = query.where(Artifact.status == ArtifactStatus.AWAITING_APPROVAL)
    return ctx.db.scalar(query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))


def card(ctx: Context, row: Artifact) -> dict:
    return {"type": "material", "artifact_id": str(row.id), "kind": KIND_LABELS.get(row.kind, "Материал"),
            "title": row.title[:300], "status": STATUS_LABELS.get(_status(row), _status(row)),
            "excerpt": excerpt(row, EXCERPT_CARD), "href": _page(ctx, row)}


def not_found() -> Outcome:
    return Outcome("Не нашёл такой материал.", "Не нашёл такой материал.", ok=False)


async def show_material(ctx: Context, params: MaterialParams) -> Outcome:
    row = find_material(ctx, params.material_id)
    if row is None:
        if params.material_id:
            return not_found()
        return Outcome("Ничего не ждёт вашего решения.", "Ничего не ждёт вашего решения.",
                       [links_block(ctx, "review")])
    blocks: list[dict] = [card(ctx, row)]
    kind = KIND_LABELS.get(row.kind, "Материал")
    if _status(row) == "awaiting_approval":
        blocks.append({"type": "chips", "items": [
            {"label": "Утвердить", "capability": "approve_material", "params": {"material_id": str(row.id)}}]})
        hint = " Чтобы доработать пост, напишите, что поправить." if row.kind == "channel_text" else ""
        text = f"{kind} «{row.title[:80]}» ждёт вашего решения.{hint}"
    else:
        text = f"{kind} «{row.title[:80]}»: {STATUS_LABELS.get(_status(row), _status(row)).lower()}."
    blocks.append({"type": "links", "items": [{"label": "Открыть полностью", "href": _page(ctx, row)}]})
    return Outcome(text, "Готовый материал ждёт вашего решения, он на экране." if _status(row) == "awaiting_approval" else text,
                   blocks)


async def describe_approve_material(ctx: Context, params: MaterialParams) -> Outcome | Confirmation:
    row = find_material(ctx, params.material_id)
    if row is None:
        return not_found() if params.material_id else Outcome(
            "Ничего не ждёт вашего решения.", "Ничего не ждёт вашего решения.", [links_block(ctx, "review")])
    if _status(row) == "approved":
        return Outcome("Этот материал уже утверждён.", "Этот материал уже утверждён.", [card(ctx, row)])
    if _status(row) != "awaiting_approval":
        return Outcome("Эту версию утвердить уже нельзя.", "Эту версию утвердить уже нельзя.", ok=False)
    if needs_fact_check(row):
        # Numbers, prices and results must be checked by eye. A button here would skip that, so the page does it.
        text = "В материале есть числа, цены или результаты, их нужно проверить глазами. Откройте полный просмотр."
        return Outcome(text, "В материале нужно проверить числа. Откройте его полностью.",
                       [card(ctx, row), {"type": "links", "items": [{"label": "Проверить и утвердить",
                                                                      "href": _page(ctx, row)}]}], ok=False)
    details = [row.title[:120]]
    text = excerpt(row, EXCERPT_CONFIRM)
    if text:
        details.append(text)
    details.append("После утверждения материал можно планировать к публикации. Сам он нигде не публикуется.")
    return Confirmation(title=f"Утвердить {KIND_LABELS.get(row.kind, 'материал').lower()}?", details=details,
                        params={"material_id": str(row.id), "version": row.version}, confirm_label="Утвердить")


async def confirm_approve_material(ctx: Context, stored: dict, via: str) -> Outcome:
    material_id = _uuid(stored.get("material_id"))
    row = ctx.db.get(Artifact, material_id) if material_id else None
    if row is None or row.product_id != ctx.product.id:
        return not_found()
    if row.version != stored.get("version") and _status(row) != "approved":
        return Outcome("Материал изменился, пока вы думали. Откройте его и проверьте новую версию.",
                       "Материал изменился. Откройте его и проверьте.", [card(ctx, row)], ok=False)
    already = _status(row) == "approved"
    try:
        row = approve_material(ctx.db, ctx.settings.products_root, ctx.product.id, row.id)
    except ReviewError as exc:
        ctx.db.rollback()
        return Outcome(exc.message, exc.message, ok=False)
    if already:  # approved elsewhere in the meantime: nothing was decided here
        return Outcome("Этот материал уже утверждён.", "Этот материал уже утверждён.", [card(ctx, row)])
    _record_approval(ctx, row, "dispatcher:approve_material", via)
    blocks: list[dict] = [card(ctx, row)]
    text = "Утвердил."
    if row.kind == "channel_text" and material_data(row).get("channel") == "telegram":
        text += " Если хотите, скажите, на какое время запланировать публикацию в Telegram."
        blocks.append({"type": "links", "items": [{"label": "Каналы и публикации",
                                                   "href": f"/studio/products/{ctx.product.id}/channels"}]})
    return Outcome(text, "Утвердил.", blocks)


async def describe_revise_material(ctx: Context, params: ReviseParams) -> Outcome | Confirmation:
    row = find_material(ctx, params.material_id, kinds=("channel_text",))
    if row is None:
        return Outcome("Не нашёл пост на проверке, который можно доработать.",
                       "Не нашёл пост для доработки.", [links_block(ctx, "review")], ok=False)
    return Confirmation(title="Доработать пост?",
                        details=[row.title[:120], f"Что поправить: {params.feedback}",
                                 "Команда перепишет пост по вашей правке. Новая версия снова придёт вам на проверку."],
                        params={"material_id": str(row.id), "version": row.version, "feedback": params.feedback},
                        confirm_label="Доработать")


async def confirm_revise_material(ctx: Context, stored: dict, via: str) -> Outcome:
    material_id = _uuid(stored.get("material_id"))
    row = ctx.db.get(Artifact, material_id) if material_id else None
    if row is None or row.product_id != ctx.product.id or row.kind != "channel_text":
        return not_found()
    if row.version != stored.get("version"):
        return Outcome("Пост изменился, пока вы думали. Откройте его и проверьте новую версию.",
                       "Пост изменился. Проверьте его.", [card(ctx, row)], ok=False)
    try:
        task = queue_post_edit(ctx.db, row.id, str(stored.get("feedback") or ""))
    except (ValueError, LookupError) as exc:
        ctx.db.rollback()
        return Outcome(str(exc), str(exc), ok=False)
    text = "Принял, дорабатываю пост. Новая версия появится на проверке."
    return Outcome(text, "Принял, дорабатываю пост.",
                   [task_block(ctx, task, f"Доработка: {row.title[:60]}",
                               f"/studio/products/{ctx.product.id}/tasks/{task.id}")], task_id=task.id)


# ---- scheduling -----------------------------------------------------------------------------------

def _moscow(text: str) -> datetime | None:
    try:
        local = datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        return None
    return local.replace(tzinfo=MOSCOW) if local.tzinfo is None else local.astimezone(MOSCOW)


def _unscheduled_post(ctx: Context, post_id: str | None) -> Artifact | None:
    if post_id:
        row = find_material(ctx, post_id, kinds=("channel_text",))
        return row if row is not None and material_data(row).get("channel") == "telegram" else None
    busy = {pid for (pid,) in ctx.db.execute(select(Publication.artifact_id).where(
        Publication.product_id == ctx.product.id,
        Publication.status.notin_(["cancelled", "failed_before_send"])))}
    rows = ctx.db.scalars(select(Artifact).where(
        Artifact.product_id == ctx.product.id, Artifact.kind == "channel_text",
        Artifact.status == ArtifactStatus.APPROVED).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(50))
    return next((r for r in rows if r.id not in busy and material_data(r).get("channel") == "telegram"), None)


def _channel_title(row: Artifact) -> str:
    return row.title


async def describe_schedule_post(ctx: Context, params: SchedulePostParams) -> Outcome | Confirmation:
    when = _moscow(params.when)
    if when is None:
        return Outcome("Не разобрал дату и время. Скажите, например: «завтра в 12:00».", "Не разобрал время.", ok=False)
    now = datetime.now(timezone.utc)
    if when.astimezone(timezone.utc) < now:
        return Outcome("Это время уже прошло. Назовите время в будущем.", "Это время уже прошло.", ok=False)
    if when.astimezone(timezone.utc) > now + timedelta(days=90):
        return Outcome("Публикацию можно назначить не дальше чем на 90 дней вперёд.",
                       "Это слишком далеко, максимум 90 дней.", ok=False)
    post = _unscheduled_post(ctx, params.post_id)
    if post is None:
        return Outcome("Нет утверждённого поста для Telegram, который ещё не запланирован. Сначала утвердите пост.",
                       "Нет утверждённого поста для Telegram.", [links_block(ctx, "review")], ok=False)
    if _status(post) != "approved":
        return Outcome("Сначала утвердите этот пост.", "Сначала утвердите этот пост.", [card(ctx, post)], ok=False)
    channels = connected_channels(ctx.db, ctx.product.id)
    if not channels:
        return Outcome("Telegram-канал не подключён. Подключите его на странице публикаций.",
                       "Telegram-канал не подключён.", [links_block(ctx, "channels")], ok=False)
    channel = next((c for c in channels if params.channel_id and str(c.id) == params.channel_id), None)
    if channel is None and params.channel_id:
        return Outcome("Не нашёл такой канал.", "Не нашёл такой канал.", [links_block(ctx, "channels")], ok=False)
    if channel is None and len(channels) > 1:
        items = [{"label": _channel_title(c)[:80], "capability": "schedule_post",
                  "params": {"post_id": str(post.id), "when": when.strftime("%Y-%m-%d %H:%M"), "channel_id": str(c.id)}}
                 for c in channels[:6]]
        return Outcome("В какой канал запланировать?", "В какой канал запланировать?",
                       [{"type": "chips", "items": items}], ok=False)
    channel = channel or channels[0]
    details = [post.title[:120]]
    text = excerpt(post, EXCERPT_CONFIRM)
    if text:
        details.append(text)
    details += [f"Канал: {_channel_title(channel)}", f"Когда: {when.strftime('%d.%m.%Y %H:%M')} (Москва)"]
    if not ctx.settings.content_telegram_autopost_enabled:
        details.append("Автоотправка на сервере сейчас выключена: публикация появится в списке, "
                       "но сама не уйдёт, пока её не включат.")
    else:
        details.append("Пост уйдёт в канал сам в это время. До отправки назначение можно отменить на странице публикаций.")
    return Confirmation(title="Запланировать публикацию?", details=details,
                        params={"post_id": str(post.id), "version": post.version, "connection_id": str(channel.id),
                                "when": when.astimezone(timezone.utc).isoformat()},
                        confirm_label="Запланировать")


async def confirm_schedule_post(ctx: Context, stored: dict, via: str) -> Outcome:
    post_id, connection_id = _uuid(stored.get("post_id")), _uuid(stored.get("connection_id"))
    try:
        when = datetime.fromisoformat(str(stored.get("when")))
    except ValueError:
        when = None
    post = ctx.db.get(Artifact, post_id) if post_id else None
    if post is None or post.product_id != ctx.product.id or connection_id is None or when is None:
        return Outcome("Не нашёл пост или время для публикации.", ok=False)
    if post.version != stored.get("version"):
        return Outcome("Пост изменился, пока вы думали. Откройте его и проверьте новую версию.",
                       "Пост изменился. Проверьте его.", [card(ctx, post)], ok=False)
    try:
        publication = schedule_owner_post(ctx.db, ctx.product.id, post_id, connection_id, when)
    except (ValueError, LookupError) as exc:
        ctx.db.rollback()
        return Outcome(str(exc), str(exc), ok=False)
    approval = _record_approval(ctx, post, "dispatcher:schedule_post", via)
    publication.approval_id = approval.id  # the publication points at the owner's yes
    ctx.db.commit()
    local = when.astimezone(MOSCOW).strftime("%d.%m %H:%M")
    suffix = "" if ctx.settings.content_telegram_autopost_enabled else " Автоотправка выключена, поэтому сам пост не уйдёт."
    return Outcome(f"Запланировал на {local} (Москва).{suffix}", f"Запланировал на {local}.",
                   [links_block(ctx, "channels")])


EXECUTORS = {"show_material": show_material}
DESCRIBERS = {"approve_material": describe_approve_material, "schedule_post": describe_schedule_post,
              "revise_material": describe_revise_material}
CONFIRMERS = {"approve_material": confirm_approve_material, "schedule_post": confirm_schedule_post,
              "revise_material": confirm_revise_material}
