"""Owner-controlled, multi-channel Telegram posting. No implicit publication.

Connect channels through the existing owner-authenticated control bot, using
Telegram Bot API and a verified channel admin role. Every publication requires
an explicit owner-approved artifact and an explicit scheduling action.

Delivery is at-most-once: the intent is marked sending in the database BEFORE
the Bot API request. After an ambiguous network failure, no automatic retry
occurs; status becomes delivery_unknown and requires manual reconciliation.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update

from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.services.artifacts import ArtifactService
from app.content.revisions import source_fingerprint

CHANNEL_PATTERN = re.compile(r"^@[a-zA-Z][a-zA-Z0-9_]{4,31}$")
CHANNEL_ID_PATTERN = re.compile(r"^-100[0-9]{5,20}$")


def safe_channel_locator(value: str) -> str:
    value = str(value or "").strip()
    if not CHANNEL_PATTERN.fullmatch(value) and not CHANNEL_ID_PATTERN.fullmatch(value):
        raise ValueError("Укажи @username канала или его числовой ID -100...")
    return value


async def verify_channel(api, locator: str) -> dict:
    locator = safe_channel_locator(locator)
    me = await api.call("getMe")
    chat = await api.call("getChat", {"chat_id": locator})
    if chat.get("type") != "channel" or not isinstance(chat.get("id"), int):
        raise ValueError("Укажи именно Telegram-канал, а не группу")
    membership = await api.call("getChatMember", {
        "chat_id": chat["id"], "user_id": me["id"]
    })
    status = membership.get("status")
    if status != "creator" and not (
        status == "administrator" and membership.get("can_post_messages") is True
    ):
        raise ValueError("Добавь бота администратором канала с правом публикации")
    return {
        "chat_id": str(chat["id"]),
        "title": (chat.get("title") or locator)[:200],
        "username": chat.get("username"),
        "bot_id": me["id"],
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def save_verified_channel(db, product_id: UUID, verified: dict) -> Artifact:
    """Call only after a successful Bot API admin verification."""
    company = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if company is None:
        raise LookupError("Компания не найдена")
    chat_id = str(verified.get("chat_id", ""))
    if not CHANNEL_ID_PATTERN.fullmatch(chat_id):
        raise ValueError("Неверный числовой ID Telegram-канала")
    rows = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "telegram_channel_connection",
    ).with_for_update()))
    for row in rows:
        try:
            old = json.loads(row.content)
        except (ValueError, TypeError):
            continue
        if old.get("chat_id") == chat_id:
            row.content = json.dumps(verified, ensure_ascii=False)
            row.title = str(verified.get("title") or chat_id)[:200]
            row.status = ArtifactStatus.APPROVED
            row.version += 1
            db.commit()
            return row
    row = ArtifactService(db).create(
        product_id=product_id, kind="telegram_channel_connection",
        title=str(verified.get("title") or chat_id)[:200],
        content=json.dumps(verified, ensure_ascii=False),
        status="approved",
    )
    db.commit()
    return row


def connected_channels(db, product_id: UUID) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "telegram_channel_connection",
        Artifact.status == ArtifactStatus.APPROVED,
    ).order_by(Artifact.created_at, Artifact.id)))


def schedule_post(db, post_id: UUID, connection_id: UUID, when: datetime, *, now=None,
                  plan_id: UUID | None = None, slot_id: str | None = None) -> Publication:
    """Explicit owner scheduling; multiple channels may be targeted independently."""
    now = now or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Время публикации должно иметь часовой пояс")
    if when < now or when > now + timedelta(days=90):
        raise ValueError("Время публикации должно быть в следующие 90 дней")
    source = db.get(Artifact, post_id)
    if source is None or source.kind != "channel_text":
        raise LookupError("Пост не найден")
    db.scalar(select(Product).where(Product.id == source.product_id).with_for_update())
    source = db.scalar(select(Artifact).where(
        Artifact.id == post_id, Artifact.product_id == source.product_id,
        Artifact.kind == "channel_text",
    ).with_for_update().execution_options(populate_existing=True))
    connection = db.get(Artifact, connection_id)
    if (connection is None or connection.product_id != source.product_id or
            connection.kind != "telegram_channel_connection" or
            connection.status != ArtifactStatus.APPROVED):
        raise ValueError("Выбранный Telegram-канал не подключён к этой компании")
    channel = json.loads(connection.content)
    content = json.loads(source.content)
    plan_row = None
    plan = None
    plan_slot = None
    if plan_id is not None or slot_id is not None:
        if plan_id is None or slot_id is None:
            raise ValueError("Для календаря нужны план и публикационный слот")
        plan_row = db.scalar(select(Artifact).where(
            Artifact.id == plan_id, Artifact.product_id == source.product_id,
            Artifact.kind == "editorial_plan",
        ).with_for_update().execution_options(populate_existing=True))
        if plan_row is None:
            raise LookupError("Контент-план не найден")
        plan = json.loads(plan_row.content)
        if plan.get("approval_state") != "active":
            raise ValueError("Подключи действующий, утверждённый контент-план")
        selected = [v for v in plan.get("slots", [])
                    if str(v.get("slot_id")) == str(slot_id)]
        if len(selected) != 1:
            raise LookupError("Слот календаря не найден")
        plan_slot = selected[0]
        if (plan_slot.get("platform") != "telegram" or
                plan_slot.get("task_id") != str(source.task_id) or
                plan_slot.get("publication_url")):
            raise ValueError("Этот пост не связан с Telegram-слотом календаря")
        if datetime.fromisoformat(plan_slot["publish_at"]).astimezone(timezone.utc) != when.astimezone(timezone.utc):
            raise ValueError("Время должно совпадать с подтверждённым календарём")
        if plan_slot.get("telegram_publication_id"):
            raise ValueError("На этот слот уже назначена публикация")
    if source.status != ArtifactStatus.APPROVED or content.get("channel") != "telegram":
        raise ValueError("Сначала утверди готовый пост для Telegram")
    if not content.get("body"):
        raise ValueError("Текст поста пустой")
    existing = list(db.scalars(select(Artifact).where(
        Artifact.product_id == source.product_id,
        Artifact.kind == "telegram_publish_intent",
    ).with_for_update()))
    for intent in existing:
        try:
            data = json.loads(intent.content)
        except (ValueError, TypeError):
            continue
        if data.get("post_id") == str(post_id) and data.get("chat_id") == channel["chat_id"]:
            publication = db.get(Publication, UUID(data["publication_id"]))
            if publication is not None and publication.status not in {"cancelled", "failed_before_send"}:
                raise ValueError("Этот пост уже запланирован или отправлен в данный канал")
    publication = Publication(
        product_id=source.product_id, artifact_id=source.id,
        channel="telegram", status="scheduled",
    )
    db.add(publication)
    db.flush()
    intent_value = {
        "publication_id": str(publication.id),
        "post_id": str(source.id),
        "source_version": source.version,
        "source_digest": source_fingerprint(source),
        "connection_id": str(connection.id),
        "chat_id": channel["chat_id"],
        "scheduled_at": when.astimezone(timezone.utc).isoformat(),
        "plan_id": str(plan_id) if plan_id is not None else None,
        "slot_id": str(slot_id) if slot_id is not None else None,
    }
    ArtifactService(db).create(
        product_id=source.product_id, kind="telegram_publish_intent",
        title="Telegram schedule " + str(publication.id),
        content=json.dumps(intent_value, ensure_ascii=False),
        status="approved",
    )
    if plan_row is not None:
        plan_slot["telegram_publication_id"] = str(publication.id)
        plan_slot["telegram_connection_id"] = str(connection.id)
        plan_slot["state"] = "scheduled"
        plan_row.content = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return publication


def cancel_scheduled_post(db, publication_id: UUID) -> Publication:
    pub = db.get(Publication, publication_id)
    if pub is None:
        raise LookupError("Публикация не найдена")
    db.scalar(select(Product).where(Product.id == pub.product_id).with_for_update())
    pub = db.scalar(select(Publication).where(
        Publication.id == publication_id,
        Publication.product_id == pub.product_id,
    ).with_for_update().execution_options(populate_existing=True))
    if pub.status != "scheduled":
        raise ValueError("Отменить можно только публикацию, которая ещё не отправлена")
    pub.status = "cancelled"
    intent = db.scalar(select(Artifact).where(
        Artifact.product_id == pub.product_id,
        Artifact.kind == "telegram_publish_intent",
        Artifact.title == "Telegram schedule " + str(pub.id),
    ))
    if intent:
        try:
            meta = json.loads(intent.content)
            if meta.get("plan_id"):
                plan = db.scalar(select(Artifact).where(
                    Artifact.id == UUID(meta["plan_id"]),
                    Artifact.product_id == pub.product_id,
                    Artifact.kind == "editorial_plan",
                ).with_for_update().execution_options(populate_existing=True))
                if plan:
                    plan_data = json.loads(plan.content)
                    for slot in plan_data.get("slots", []):
                        if slot.get("telegram_publication_id") == str(pub.id):
                            slot["telegram_publication_id"] = None
                            slot["telegram_connection_id"] = None
                            slot["state"] = "ready_for_review"
                    plan.content = json.dumps(plan_data, ensure_ascii=False)
        except (ValueError, TypeError, KeyError):
            pass
    db.commit()
    return pub



def update_plan_delivery(db, data: dict, state: str, message_id: int | None = None):
    """Reconcile the editorial slot from an actual publisher outcome."""
    if not data.get("plan_id"):
        return
    product_id = UUID(data["post_id"])
    source = db.get(Artifact, product_id)
    if source is None:
        return
    db.scalar(select(Product).where(Product.id == source.product_id).with_for_update())
    plan = db.scalar(select(Artifact).where(
        Artifact.id == UUID(data["plan_id"]), Artifact.product_id == source.product_id,
        Artifact.kind == "editorial_plan",
    ).with_for_update().execution_options(populate_existing=True))
    if plan is None:
        return
    plan_data = json.loads(plan.content)
    for slot in plan_data.get("slots", []):
        if slot.get("telegram_publication_id") == data.get("publication_id"):
            slot["state"] = state
            if message_id is not None:
                slot["telegram_message_id"] = message_id
    plan.content = json.dumps(plan_data, ensure_ascii=False)



def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


async def run_due_telegram_post_once(session_factory, settings, *, api=None, now=None) -> int:
    """Send at most one due approved post. Status 'delivery_unknown' never auto-retries."""
    if not settings.content_telegram_autopost_enabled or not settings.telegram_bot_token:
        return 0
    now = now or datetime.now(timezone.utc)
    from app.telegram_control_bot import TelegramAPI
    bot = api or TelegramAPI(settings.telegram_bot_token, settings.telegram_proxy_url)
    with session_factory() as db:
        # Query only pending publications, not the first N historic intents.
        # This prevents starvation when the archive contains thousands of posts.
        queued_publications = list(db.scalars(select(Publication).where(
            Publication.channel == "telegram",
            Publication.status == "scheduled",
        ).order_by(Publication.created_at, Publication.id).limit(500)))
        selected = None
        for pub in queued_publications:
            intent = db.scalar(select(Artifact).where(
                Artifact.product_id == pub.product_id,
                Artifact.kind == "telegram_publish_intent",
                Artifact.title == "Telegram schedule " + str(pub.id),
            ))
            if intent is None or intent.status != ArtifactStatus.APPROVED:
                pub.status = "failed_before_send"
                db.commit()
                return 1
            try:
                data = json.loads(intent.content)
                when = datetime.fromisoformat(data["scheduled_at"])
                if when.tzinfo is None or data.get("publication_id") != str(pub.id):
                    raise ValueError("Invalid publication intent")
                if when > now:
                    continue
                # If the operator enables publishing after a long outage,
                # never flood channels with a backlog of stale announcements.
                lateness = max(1, min(1440, settings.content_telegram_max_lateness_minutes))
                if now - when > timedelta(minutes=lateness):
                    pub.status = "missed"
                    update_plan_delivery(db, data, "missed")
                    db.commit()
                    return 1
            except (ValueError, TypeError, KeyError):
                pub.status = "failed_before_send"
                db.commit()
                return 1
            selected = (intent.id, data, pub.id)
            break
        if selected is None:
            return 0
        intent_id, data, pub_id = selected
        pub = db.get(Publication, pub_id)
        source = db.get(Artifact, UUID(data["post_id"]))
        connection = db.get(Artifact, UUID(data["connection_id"]))
        valid = (
            pub is not None and pub.channel == "telegram"
            and source is not None and source.id == pub.artifact_id
            and source.product_id == pub.product_id
            and source.kind == "channel_text" and source.status == ArtifactStatus.APPROVED
            and source.version == data["source_version"]
            and source_fingerprint(source) == data["source_digest"]
            and connection is not None and connection.product_id == pub.product_id
            and connection.kind == "telegram_channel_connection"
            and connection.status == ArtifactStatus.APPROVED
            and json.loads(connection.content).get("chat_id") == data["chat_id"]
        )
        text = ""
        if valid:
            text = str(json.loads(source.content).get("body") or "")
            valid = bool(text) and _utf16_units(text) <= 4096
        if not valid:
            db.execute(update(Publication).where(
                Publication.id == pub_id, Publication.status == "scheduled"
            ).values(status="failed_before_send"))
            update_plan_delivery(db, data, "failed")
            db.commit()
            return 1
        claimed = db.execute(update(Publication).where(
            Publication.id == pub_id, Publication.status == "scheduled"
        ).values(status="sending"))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        db.commit()  # Durable at-most-once guard established before network call.
        try:
            result = await bot.call("sendMessage", {
                "chat_id": int(data["chat_id"]), "text": text,
                "link_preview_options": {"is_disabled": True},
            })
            if not isinstance(result.get("message_id"), int):
                raise ValueError("Telegram did not return a message ID")
            pub.status = "published"
            pub.external_reference = "telegram:" + data["chat_id"] + ":" + str(result["message_id"])
            pub.published_at = datetime.now(timezone.utc)
            update_plan_delivery(db, data, "published", result["message_id"])
        except Exception:
            # The Bot API may have delivered despite a network timeout.
            # Never replay automatically: an owner must reconcile this result.
            pub.status = "delivery_unknown"
            try:
                update_plan_delivery(db, data, "delivery_unknown")
            except (ValueError, LookupError, KeyError, TypeError):
                db.rollback()
                db.get(Publication, pub_id).status = "delivery_unknown"
        db.commit()
        return 1
