"""Company-scoped publishing actions. Never send directly from dispatcher."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.content.revisions import source_fingerprint
from app.dispatcher.outcome import Confirmation, Context, Outcome, links_block
from app.models.domain import Approval, Artifact, ArtifactStatus, Product, Publication

CHANNELS = {"telegram": "Telegram", "vk": "ВКонтакте"}
STATES = {"scheduled": "Запланировано", "sending": "Отправляется", "published": "Опубликовано",
          "delivery_unknown": "Доставка не подтверждена", "failed_before_send": "Не отправлено",
          "missed": "Пропущено", "cancelled": "Отменено"}
PROBLEMS = ("delivery_unknown", "failed_before_send", "missed")
CANCELLABLE = {"telegram": {"scheduled", "missed", "failed_before_send"}, "vk": {"scheduled"}}


def _allowed(ctx):
    return ctx.studio_role in (None, "owner", "co_owner")


def _denied():
    return Outcome("Управлять публикациями может владелец компании.", ok=False)


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _when(value):
    from app.dispatcher.materials import _moscow
    when = _moscow(value)
    now = datetime.now(timezone.utc)
    if when is None or not now < when <= now + timedelta(days=90):
        raise ValueError("Укажите будущее время в пределах 90 дней.")
    return when


def _local(value):
    from app.dispatcher.materials import MOSCOW
    return _utc(value).astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M") if value else "—"


def _rows(db, product_id):
    return select(Publication, Artifact.title).join(Artifact, Publication.artifact_id == Artifact.id).where(
        Publication.product_id == product_id, Artifact.product_id == product_id,
        Publication.channel.in_(CHANNELS), Artifact.kind == "channel_text")


def publication_snapshot(db, product_id, *, limit=15):
    rows = db.execute(_rows(db, product_id).order_by(
        Publication.created_at.desc(), Publication.id.desc()).limit(limit))
    return [{"publication_id": str(pub.id), "title": title[:140], "channel": pub.channel,
             "status": pub.status, "scheduled_at": _utc(pub.scheduled_at).isoformat() if pub.scheduled_at else None}
            for pub, title in rows]


def _get(ctx, publication_id, *, lock=False):
    stmt = select(Publication).where(Publication.product_id == ctx.product.id,
        Publication.id == publication_id, Publication.channel.in_(CHANNELS))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    pub = ctx.db.scalar(stmt)
    if pub is None:
        raise LookupError("Публикация этой компании не найдена.")
    post = ctx.db.scalar(select(Artifact).where(Artifact.id == pub.artifact_id,
        Artifact.product_id == ctx.product.id, Artifact.kind == "channel_text"))
    if post is None:
        raise LookupError("Исходный пост не найден.")
    return pub, post


def _intent(ctx, pub):
    prefix = "VK" if pub.channel == "vk" else "Telegram"
    row = ctx.db.scalar(select(Artifact).where(Artifact.product_id == ctx.product.id,
        Artifact.kind == pub.channel + "_publish_intent", Artifact.title == prefix + " schedule " + str(pub.id),
        Artifact.status == ArtifactStatus.APPROVED))
    if row is None:
        raise ValueError("Не найдено подтверждённое назначение публикации.")
    data = json.loads(row.content)
    if data.get("publication_id") != str(pub.id) or data.get("post_id") != str(pub.artifact_id):
        raise ValueError("Назначение публикации не соответствует посту.")
    return data


def _pin(pub):
    return {"publication_id": str(pub.id), "status": pub.status,
            "scheduled_at": _utc(pub.scheduled_at).isoformat() if pub.scheduled_at else None,
            "updated_at": _utc(pub.updated_at).isoformat()}


def _locked(ctx, stored):
    ctx.db.scalar(select(Product).where(Product.id == ctx.product.id).with_for_update())
    pub, post = _get(ctx, UUID(stored["publication_id"]), lock=True)
    if _pin(pub) != {k: stored[k] for k in _pin(pub)}:
        raise ValueError("Публикация изменилась. Проверьте актуальное состояние.")
    return pub, post


def _audit(ctx, post, action, via, pub=None):
    approval = Approval(product_id=ctx.product.id, artifact_id=post.id, action_type="dispatcher:" + action,
                        risk_level="YELLOW", status="approved", requested_by=ctx.account,
                        decided_by=ctx.account, decided_at=datetime.now(timezone.utc), reason=f"via={via}")
    ctx.db.add(approval)
    ctx.db.flush()
    if pub is not None:
        pub.approval_id = approval.id
    ctx.db.commit()


def _error(ctx, exc):
    ctx.db.rollback()
    return Outcome(str(exc) if isinstance(exc, (LookupError, ValueError)) else "Не удалось изменить публикацию.",
                   ok=False, blocks=[links_block(ctx, "channels", "vk")])


async def list_publications(ctx: Context, params):
    stmt = _rows(ctx.db, ctx.product.id)
    if params.channel != "all":
        stmt = stmt.where(Publication.channel == params.channel)
    if params.status == "problems":
        stmt = stmt.where(Publication.status.in_(PROBLEMS))
    elif params.status != "all":
        stmt = stmt.where(Publication.status == params.status)
    rows = list(ctx.db.execute(stmt.order_by(Publication.created_at.desc(), Publication.id.desc())
                               .offset((params.page - 1) * 15).limit(16)))
    base = params.model_dump(mode="json")
    def chip(label, **changes):
        return {"label": label, "capability": "list_publications",
                "params": {k: str(v) for k, v in {**base, **changes}.items()}}
    if not rows:
        blocks = [links_block(ctx, "channels", "vk")]
        if params.page > 1:
            blocks.insert(0, {"type": "chips", "items": [chip("Первая страница", page=1)]})
        return Outcome("Подходящих публикаций на этой странице не найдено.", blocks=blocks, no_data=True)
    shown = rows[:15]
    table = {"type": "table", "columns": ["Материал", "Площадка", "Статус", "Назначено (МСК)"],
             "rows": [[title[:140], CHANNELS[pub.channel], STATES.get(pub.status, pub.status),
                       _local(pub.scheduled_at)] for pub, title in shown]}
    text = f"Публикации выбранной компании. Страница {params.page}, показано: {len(shown)}."
    if any(pub.status == "delivery_unknown" for pub, _ in shown):
        text += " Неопределённую доставку нужно сверить с площадкой; повторно отправлять такой пост нельзя."
    blocks = [table]
    if params.list_presentation == "list":
        from app.dispatcher.blocks import clean_blocks
        cards = clean_blocks([{"type": "resource_list", "title": "Публикации", "filterable": True,
            "items": [{"kind": "publication", "title": title.strip()[:140] or "Публикация",
                       "status": STATES.get(pub.status, pub.status)[:80],
                       "detail": f"{CHANNELS[pub.channel]} · Назначено (МСК): {_local(pub.scheduled_at)}",
                       "href": f"/studio/products/{ctx.product.id}/materials/{pub.artifact_id}",
                       "link_label": "Открыть исходный материал"} for pub, title in shown]}])
        if cards:
            blocks = cards
    controls = [chip("Показать таблицу" if params.list_presentation == "list" else "Показать карточки",
                     list_presentation="table" if params.list_presentation == "list" else "list")]
    if params.page > 1:
        controls.append(chip("Предыдущая страница", page=params.page - 1))
    if len(rows) > 15 and params.page < 1000:
        controls.append(chip("Следующая страница", page=params.page + 1))
    return Outcome(text, blocks=[*blocks, {"type": "chips", "items": controls}, links_block(ctx, "channels", "vk")])


async def describe_schedule_vk_post(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    from app.social_vk import connected, prepared_image
    try:
        when = _when(params.when)
        post = ctx.db.scalar(select(Artifact).where(Artifact.id == params.post_id,
            Artifact.product_id == ctx.product.id, Artifact.kind == "channel_text",
            Artifact.status == ArtifactStatus.APPROVED))
        if post is None or json.loads(post.content).get("channel") != "vk":
            raise ValueError("Выберите утверждённый пост ВКонтакте.")
        if not ctx.settings.content_vk_autopost_enabled:
            raise ValueError("Автоотправка ВКонтакте выключена.")
        channel = connected(ctx.db, ctx.product.id)
        if channel is None:
            raise ValueError("Подключите сообщество ВКонтакте.")
        prepared_image(ctx.db, ctx.settings.products_root, post)
        return Confirmation("Запланировать пост ВКонтакте?",
            [post.title[:120], f"Сообщество: {channel.account_name}", f"Когда: {_local(when)} (Москва).",
             "Отправится через действующий обработчик публикаций."],
            {"post_id": str(post.id), "source_digest": source_fingerprint(post), "source_version": post.version,
             "channel_id": str(channel.id), "group_id": channel.account_id, "when": _utc(when).isoformat()},
            confirm_label="Запланировать")
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


async def confirm_schedule_vk_post(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    from app.social_vk import connected, schedule
    try:
        ctx.db.scalar(select(Product).where(Product.id == ctx.product.id).with_for_update())
        post = ctx.db.scalar(select(Artifact).where(Artifact.id == UUID(stored["post_id"]),
            Artifact.product_id == ctx.product.id, Artifact.kind == "channel_text")
            .with_for_update().execution_options(populate_existing=True))
        channel = connected(ctx.db, ctx.product.id)
        if (post is None or post.version != stored["source_version"] or source_fingerprint(post) != stored["source_digest"]
                or channel is None or str(channel.id) != stored["channel_id"] or channel.account_id != stored["group_id"]):
            raise ValueError("Пост или сообщество изменились. Подтвердите заново.")
        if not ctx.settings.content_vk_autopost_enabled:
            raise ValueError("Автоотправка ВКонтакте выключена.")
        when = _when(stored["when"])
        pub = schedule(ctx.db, ctx.product.id, post.id, when, products_root=ctx.settings.products_root, commit=False)
        _audit(ctx, post, "schedule_vk_post", via, pub)
        return Outcome(f"Пост ВКонтакте запланирован на {_local(when)} (Москва).", blocks=[links_block(ctx, "vk")])
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


async def describe_cancel_publication(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    try:
        pub, post = _get(ctx, params.publication_id)
        _intent(ctx, pub)
        if pub.status not in CANCELLABLE[pub.channel]:
            raise ValueError("Отменить можно только заведомо неотправленную публикацию.")
        return Confirmation("Отменить назначенную публикацию?",
            [post.title[:120], CHANNELS[pub.channel], f"Назначено: {_local(pub.scheduled_at)} (Москва).",
             "Материал сохранится. Уже отправленные посты не удаляются."], _pin(pub), confirm_label="Отменить публикацию")
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


async def confirm_cancel_publication(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    try:
        pub, post = _locked(ctx, stored)
        _intent(ctx, pub)
        if pub.status not in CANCELLABLE[pub.channel]:
            raise ValueError("Публикация уже не подлежит отмене.")
        if pub.channel == "vk":
            from app.social_vk import cancel
            cancel(ctx.db, ctx.product.id, pub.id, commit=False)
        else:
            from app.content.telegram_publishing import cancel_scheduled_post
            cancel_scheduled_post(ctx.db, pub.id, commit=False)
        _audit(ctx, post, "cancel_publication", via)
        return Outcome("Назначение отменено. Материал сохранён.", blocks=[links_block(ctx, "channels", "vk")])
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


async def describe_move_publication(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    try:
        pub, post = _get(ctx, params.publication_id)
        data = _intent(ctx, pub)
        when = _when(params.when)
        if pub.status != "scheduled" or pub.scheduled_at is None or _utc(pub.scheduled_at) <= datetime.now(timezone.utc):
            raise ValueError("Перенос доступен только для ещё не наступившей публикации.")
        if data.get("plan_id") or data.get("slot_id"):
            return Outcome("Публикация связана с контент-планом. Сначала отмените назначение, затем перенесите слот календаря и назначьте публикацию заново.",
                           ok=False, blocks=[links_block(ctx, "calendar", "channels")])
        if when == _utc(pub.scheduled_at):
            raise ValueError("Укажите новое время.")
        return Confirmation("Перенести назначенную публикацию?",
            [post.title[:120], CHANNELS[pub.channel], f"Было: {_local(pub.scheduled_at)} (Москва).",
             f"Будет: {_local(when)} (Москва). Материал и площадка не меняются."],
            {**_pin(pub), "when": _utc(when).isoformat()}, confirm_label="Перенести")
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


async def confirm_move_publication(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    try:
        pub, post = _locked(ctx, stored)
        data = _intent(ctx, pub)
        when = _when(stored["when"])
        if (pub.status != "scheduled" or pub.scheduled_at is None or _utc(pub.scheduled_at) <= datetime.now(timezone.utc)
                or data.get("plan_id") or data.get("slot_id")):
            raise ValueError("Публикация больше не подлежит прямому переносу.")
        post = ctx.db.scalar(select(Artifact).where(Artifact.id == post.id, Artifact.product_id == ctx.product.id)
                             .with_for_update().execution_options(populate_existing=True))
        if post is None or post.version != data.get("source_version") or source_fingerprint(post) != data.get("source_digest"):
            raise ValueError("Материал изменился. Проверьте его перед назначением.")
        # Old intent stays immutable. New intent is built by the original scheduler.
        # One transaction: failed validation rolls back cancellation as well.
        pub.status = "cancelled"
        ctx.db.flush()
        if pub.channel == "vk":
            from app.social_vk import connected, schedule
            channel = connected(ctx.db, ctx.product.id)
            if (not ctx.settings.content_vk_autopost_enabled or channel is None
                    or str(channel.id) != data["channel_id"] or channel.account_id != data["group_id"]):
                raise ValueError("Сообщество или автоотправка ВКонтакте изменились.")
            new = schedule(ctx.db, ctx.product.id, post.id, when, products_root=ctx.settings.products_root, commit=False)
        else:
            from app.content.telegram_publishing import schedule_post
            connection = ctx.db.scalar(select(Artifact).where(Artifact.id == UUID(data["connection_id"]),
                Artifact.product_id == ctx.product.id, Artifact.kind == "telegram_channel_connection",
                Artifact.status == ArtifactStatus.APPROVED).with_for_update().execution_options(populate_existing=True))
            if connection is None or json.loads(connection.content).get("chat_id") != data["chat_id"]:
                raise ValueError("Telegram-канал изменился.")
            new = schedule_post(ctx.db, post.id, connection.id, when, commit=False)
        _audit(ctx, post, "move_publication", via, new)
        return Outcome(f"Публикация перенесена на {_local(when)} (Москва).", blocks=[links_block(ctx, "channels", "vk")])
    except (ValueError, LookupError, KeyError, TypeError) as exc:
        return _error(ctx, exc)


EXECUTORS = {"list_publications": list_publications}
DESCRIBERS = {"schedule_vk_post": describe_schedule_vk_post, "cancel_publication": describe_cancel_publication,
              "move_publication": describe_move_publication}
CONFIRMERS = {"schedule_vk_post": confirm_schedule_vk_post, "cancel_publication": confirm_cancel_publication,
              "move_publication": confirm_move_publication}
