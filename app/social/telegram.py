"""Telegram Bot API publishing with no browser tokens and no duplicate blind retries."""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID
import httpx
from sqlalchemy import select
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.social.models import TelegramChannel, TelegramPost, TelegramMedia


def now_utc():
    return datetime.now(timezone.utc)


def storage_path(root, product_id, media):
    base = (Path(root) / "_social_media" / str(product_id)).resolve()
    path = (base / media.file_name).resolve()
    if not path.is_relative_to(base) or not re.fullmatch(r"[0-9a-f-]+\.(jpg|png|mp4)", media.file_name):
        raise ValueError("Недопустимый файл публикации")
    return path


def configured(settings):
    token = getattr(settings, "social_telegram_bot_token", None)
    if not token or not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise ValueError("Администратор ServGo ещё не настроил бота автопубликации")
    if not settings.comment_platform_proxy_url:
        raise ValueError("Не настроен защищённый Telegram-прокси")
    return token


async def api(settings, method, data=None, *, files=None, client=None):
    token = configured(settings)
    # Fixed official endpoint: never pass a URL from a post or uploaded document.
    url = "https://api.telegram.org/bot" + token + "/" + method
    try:
        if client is not None:
            response = await client.post(url, data=data if files else None, json=None if files else data, files=files)
        else:
            async with httpx.AsyncClient(proxy=settings.comment_platform_proxy_url, trust_env=False,
                                         follow_redirects=False, timeout=40) as http:
                response = await http.post(url, data=data if files else None, json=None if files else data, files=files)
        payload = response.json()
        if response.status_code != 200 or not payload.get("ok"):
            # Platform message may contain sensitive information; never return it.
            raise ValueError("Telegram отклонил запрос: " + str(payload.get("error_code") or response.status_code))
        return payload["result"]
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Telegram отклонил"):
            raise
        raise ValueError("Нет подтверждения Telegram. Проверьте прокси и доступ бота.") from None


async def start_connect(db, settings, product_id, chat_reference):
    if not db.get(Product, product_id):
        raise LookupError("Бизнес не найден")
    if not re.fullmatch(r"(?:-100\d{5,20}|@[A-Za-z][\w]{4,31})", chat_reference):
        raise ValueError("Введите @имя_канала или числовой ID канала -100…")
    bot = await api(settings, "getMe")
    chat = await api(settings, "getChat", {"chat_id": chat_reference})
    if chat.get("type") != "channel":
        raise ValueError("Нужен Telegram-канал, не группа")
    user = await api(settings, "getChatMember", {"chat_id": chat["id"], "user_id": bot["id"]})
    if user.get("status") not in {"administrator", "creator"} or not user.get("can_post_messages", False):
        raise ValueError("Добавьте бота администратором канала с правом публиковать сообщения")
    canonical = str(chat["id"])
    existing = db.scalar(select(TelegramChannel).where(TelegramChannel.chat_id == canonical).with_for_update())
    if existing and existing.product_id != product_id:
        raise ValueError("Канал уже закреплён за другим бизнесом. Обратитесь в поддержку.")
    if existing and existing.status == "active":
        return existing
    row = existing or TelegramChannel(product_id=product_id, chat_id=canonical, title=chat.get("title", canonical))
    row.title = chat.get("title", canonical)[:200]
    row.username = chat.get("username")
    row.status = "pending"
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def verify_telegram_login(token, data):
    """Verify signed Telegram Login Widget identity; the logged-in person must be channel admin."""
    import hashlib
    import hmac
    if not isinstance(data, dict) or len(data) > 12:
        raise ValueError("Некорректные данные входа Telegram")
    provided_hash = data.get("hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", provided_hash):
        raise ValueError("Не удалось проверить подпись Telegram")
    if not str(data.get("id", "")).isdigit() or not str(data.get("auth_date", "")).isdigit():
        raise ValueError("Некорректный аккаунт Telegram")
    signed = "\n".join(f"{key}={value}" for key, value in sorted(data.items()) if key != "hash")
    secret = hashlib.sha256(token.encode("utf-8")).digest()
    expected = hmac.new(secret, signed.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_hash):
        raise ValueError("Подпись Telegram не совпадает")
    age = int(datetime.now(timezone.utc).timestamp()) - int(data["auth_date"])
    if not 0 <= age <= 300:
        raise ValueError("Вход устарел. Авторизуйтесь через Telegram повторно")
    return int(data["id"])


async def confirm_connect(db, settings, product_id, channel_id, auth_data):
    row = db.scalar(select(TelegramChannel).where(
        TelegramChannel.id == channel_id, TelegramChannel.product_id == product_id).with_for_update())
    if not row:
        raise LookupError("Подключение не найдено")
    if row.status != "pending":
        raise ValueError("Подключение уже выполнено или отменено")
    operator_id = verify_telegram_login(configured(settings), auth_data)
    chat = await api(settings, "getChat", {"chat_id": row.chat_id})
    admin = await api(settings, "getChatMember", {"chat_id": row.chat_id, "user_id": operator_id})
    if admin.get("status") not in {"administrator", "creator"}:
        raise ValueError("Этот Telegram-аккаунт не является администратором выбранного канала")
    bot = await api(settings, "getMe")
    member = await api(settings, "getChatMember", {"chat_id": row.chat_id, "user_id": bot["id"]})
    if member.get("status") not in {"administrator", "creator"} or not member.get("can_post_messages"):
        raise ValueError("Боту необходимо право публикации")
    row.status = "active"
    row.title = chat.get("title", row.title)[:200]
    db.commit()
    return row

def create_posts(db, product_id, channel_ids, text, media_ids, scheduled_at, *, plan_slot_key=None):
    if not db.get(Product, product_id):
        raise LookupError("Бизнес не найден")
    if len(channel_ids) != len(set(channel_ids)) or not 1 <= len(channel_ids) <= 30:
        raise ValueError("Выберите от одного до 30 разных каналов")
    if not isinstance(text, str) or len(text) > 4096 or not (text.strip() or media_ids):
        raise ValueError("Пустая публикация или слишком длинный текст")
    if len(media_ids) > 10 or len(media_ids) == 1 and len(text) > 1024 or len(media_ids) > 1 and len(text) > 1024:
        raise ValueError("Для публикаций с медиа максимум 1024 символа и 10 файлов")
    scheduled_at = scheduled_at.astimezone(timezone.utc)
    if scheduled_at < now_utc() - timedelta(seconds=5) or scheduled_at > now_utc() + timedelta(days=365):
        raise ValueError("Время публикации должно быть в будущем, не дальше года")
    channels = list(db.scalars(select(TelegramChannel).where(
        TelegramChannel.id.in_(channel_ids), TelegramChannel.product_id == product_id, TelegramChannel.status == "active")))
    if len(channels) != len(channel_ids):
        raise ValueError("Один или несколько каналов не подключены")
    media = list(db.scalars(select(TelegramMedia).where(
        TelegramMedia.id.in_(media_ids), TelegramMedia.product_id == product_id))) if media_ids else []
    lookup = {m.id: m for m in media}
    if len(lookup) != len(set(media_ids)):
        raise ValueError("Медиафайл не найден в вашем бизнесе")
    ids = []
    for channel in channels:
        if plan_slot_key:
            duplicate = db.scalar(select(TelegramPost.id).where(
                TelegramPost.channel_id == channel.id, TelegramPost.plan_slot_key == plan_slot_key))
            if duplicate:
                continue
        record = TelegramPost(product_id=product_id, channel_id=channel.id,
            plan_slot_key=plan_slot_key, text=text,
            media=[str(x) for x in media_ids], scheduled_at=scheduled_at, status="scheduled")
        db.add(record)
        db.flush()
        ids.append(str(record.id))
    db.commit()
    return ids


def auto_plan_queue(db, settings):
    """Only owner-approved plans AND explicitly enabled destinations; never publish raw scripts."""
    plans = list(db.scalars(select(Artifact).where(Artifact.kind == "editorial_plan")))
    count = 0
    for artifact in plans:
        try:
            plan = json.loads(artifact.content)
        except (ValueError, TypeError):
            continue
        if plan.get("approval_state") != "active":
            continue
        channels = list(db.scalars(select(TelegramChannel).where(
            TelegramChannel.product_id == artifact.product_id, TelegramChannel.status == "active",
            TelegramChannel.auto_plan.is_(True))))
        if not channels:
            continue
        for slot in plan.get("slots", []):
            if slot.get("platform") != "telegram" or not slot.get("task_id"):
                continue
            scheduled = datetime.fromisoformat(slot["publish_at"])
            if scheduled <= now_utc():
                continue
            task = db.get(Task, UUID(slot["task_id"]))
            if not task or task.product_id != artifact.product_id or task.status != TaskStatus.COMPLETED:
                continue
            materials = list(db.scalars(select(Artifact).where(
                Artifact.task_id == task.id, Artifact.product_id == artifact.product_id,
                Artifact.kind == "channel_text")))
            for material in materials:
                try:
                    content = json.loads(material.content)
                except ValueError:
                    continue
                if content.get("channel") != "telegram":
                    continue
                text = (content.get("title", "") + "\n\n" + content.get("body", "")).strip()
                if not text:
                    continue
                try:
                    ids = create_posts(db, artifact.product_id, [ch.id for ch in channels],
                        text[:4096], [], scheduled, plan_slot_key=str(artifact.id) + ":" + str(slot["slot_id"]))
                    count += len(ids)
                except ValueError:
                    db.rollback()
                break
    return count


async def deliver_once(session_factory, settings):
    """Claim before sending. Any crash or ambiguity stays 'sending' until manual reconciliation."""
    with session_factory() as db:
        record = db.scalar(select(TelegramPost).where(
            TelegramPost.status == "scheduled", TelegramPost.scheduled_at <= now_utc())
            .order_by(TelegramPost.scheduled_at).with_for_update(skip_locked=True).limit(1))
        if not record:
            return 0
        channel = db.get(TelegramChannel, record.channel_id)
        if channel is None or channel.status != "active":
            record.status, record.error_code = "cancelled", "channel_inactive"
            db.commit()
            return 1
        record.status = "sending"
        post_id, product_id, chat_id = record.id, record.product_id, channel.chat_id
        text, media_ids = record.text, list(record.media or [])
        db.commit()
    try:
        with session_factory() as db:
            assets = list(db.scalars(select(TelegramMedia).where(
                TelegramMedia.id.in_([UUID(x) for x in media_ids]), TelegramMedia.product_id == product_id)))
            by_id = {str(x.id): x for x in assets}
            if len(by_id) != len(set(media_ids)):
                raise ValueError("Медиафайлы удалены")
            data = [(by_id[x].kind, storage_path(settings.products_root, product_id, by_id[x])) for x in media_ids]
        for _, path in data:
            if not path.is_file():
                raise ValueError("Медиафайлы недоступны")
        if not data:
            result = await api(settings, "sendMessage", {"chat_id": chat_id, "text": text})
            message_ids = [result["message_id"]]
        elif len(data) == 1:
            kind, path = data[0]
            with open(path, "rb") as fp:
                result = await api(settings, "sendPhoto" if kind == "photo" else "sendVideo",
                    {"chat_id": chat_id, "caption": text},
                    files={"photo" if kind == "photo" else "video": (path.name, fp, "image/jpeg" if path.suffix == ".jpg" else "image/png" if path.suffix == ".png" else "video/mp4")})
            message_ids = [result["message_id"]]
        else:
            from contextlib import ExitStack
            with ExitStack() as stack:
                files, group = {}, []
                for i, (kind, path) in enumerate(data):
                    fp = stack.enter_context(open(path, "rb"))
                    files["asset" + str(i)] = (path.name, fp, "video/mp4" if kind == "video" else "image/png" if path.suffix == ".png" else "image/jpeg")
                    item = {"type": kind, "media": "attach://asset" + str(i)}
                    if i == 0 and text:
                        item["caption"] = text
                    group.append(item)
                result = await api(settings, "sendMediaGroup", {
                    "chat_id": chat_id, "media": json.dumps(group, ensure_ascii=False)}, files=files)
            message_ids = [x["message_id"] for x in result]
    except Exception as exc:
        # A network timeout may mean Telegram actually posted. No automatic retry.
        with session_factory() as db:
            row = db.get(TelegramPost, post_id)
            if row and row.status == "sending":
                row.status, row.error_code = "delivery_unknown", type(exc).__name__[:100]
                db.commit()
        return 1
    with session_factory() as db:
        row = db.get(TelegramPost, post_id)
        if row and row.status == "sending":
            row.status, row.external_message_ids = "sent", message_ids
            db.commit()
    return 1
