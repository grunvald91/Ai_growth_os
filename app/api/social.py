"""Owner-facing Social Networks workspace and its Telegram publishing API."""
import os
import re
from datetime import datetime
from typing import Literal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import esc, studio_page_shell
from app.api.comments import same_origin
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Product
from app.social.models import TelegramChannel, TelegramPost, TelegramMedia
from app.social.telegram import start_connect, confirm_connect, create_posts, storage_path, api, configured

router = APIRouter()


@router.get("/studio/assets/social.js")
def social_javascript():
    return Response(Path(__file__).with_name("social_design.js").read_text(encoding="utf-8"), media_type="application/javascript")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Begin(Strict):
    chat: str = Field(min_length=5, max_length=100)


class Schedule(Strict):
    channels: list[UUID] = Field(min_length=1, max_length=30)
    text: str = Field(default="", max_length=4096)
    media: list[UUID] = Field(default_factory=list, max_length=10)
    scheduled_at: datetime


class ChannelOptions(Strict):
    auto_plan: bool


class PostDecision(Strict):
    action: Literal["cancel", "confirm_delivered"]


def error(exc):
    return HTTPException(404 if isinstance(exc, LookupError) else 409, str(exc))


def get_channel(db, product_id, channel_id):
    row = db.scalar(select(TelegramChannel).where(
        TelegramChannel.product_id == product_id, TelegramChannel.id == channel_id))
    if not row:
        raise LookupError("Канал не найден")
    return row


def serialize(row):
    return {"id": str(row.id), "title": row.title, "chat_id": row.chat_id,
            "username": row.username, "status": row.status, "auto_plan": row.auto_plan,
            "verification": "telegram_login" if row.status == "pending" else None}


@router.get("/studio/products/{product_id}/social", response_class=HTMLResponse)
async def page(product_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Бизнес не найден")
    channels = list(db.scalars(select(TelegramChannel).where(
        TelegramChannel.product_id == product_id).order_by(TelegramChannel.created_at)))
    posts = list(db.scalars(select(TelegramPost).where(
        TelegramPost.product_id == product_id).order_by(TelegramPost.created_at.desc()).limit(30)))
    bot_url = None
    bot_name = (settings.social_telegram_bot_username or "").lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", bot_name):
        bot_url = "https://t.me/" + bot_name + "?startchannel&admin=post_messages"
    else:
        bot_name = None
    cards = []
    for row in channels:
        login_host = (settings.social_oauth_redirect_base or "").rstrip("/")
        login_url = (login_host + f"/products/{product_id}/social/channels/{row.id}/telegram-login")
        proof = (f'<p>Бот уже добавлен? Подтвердите через Telegram аккаунт '
                 'администратора этого канала:</p>'
                 f'<script async src="https://telegram.org/js/telegram-widget.js?22" '
                 f'data-telegram-login="{esc(bot_name)}" data-size="large" '
                 f'data-auth-url="{esc(login_url)}"></script>'
                 if row.status == "pending" and bot_name and login_host.startswith("https://") else
                 '<p>Для входа через Telegram администратор должен настроить HTTPS-домен в BotFather.</p>'
                 if row.status == "pending" else "")
        controls = (f'<label class="c-check"><input type="checkbox" data-auto="{row.id}" '
                    f'{"checked" if row.auto_plan else ""} {"disabled" if row.status != "active" else ""}> '
                    'Автопубликация по утверждённому контент-плану</label>')
        cards.append(f'<section class="panel c-card"><h3>{esc(row.title)}</h3>'
            f'<p>{esc(row.chat_id)} · {"Подключён" if row.status == "active" else "Ожидает подтверждения"}</p>'
            + proof + controls +
            f'<button type="button" class="btn ghost" data-remove="{row.id}">Отключить</button></section>')
    options = ''.join(f'<label class="c-check"><input type="checkbox" name="channels" value="{ch.id}"> '
                      f'{esc(ch.title)}</label>' for ch in channels if ch.status == "active")
    channel_titles = {channel.id: channel.title for channel in channels}
    def history_action(post):
        if post.status == "scheduled":
            return f'<button type="button" class="btn ghost" data-post-id="{post.id}" data-post-action="cancel">Отменить</button>'
        if post.status in {"sending", "delivery_unknown"}:
            return (f'<button type="button" class="btn ghost" data-post-id="{post.id}" data-post-action="confirm_delivered">'
                    'Подтвердить после проверки в Telegram</button>'
                    f'<button type="button" class="btn ghost" data-post-id="{post.id}" data-post-action="cancel">'
                    'Закрыть без повтора</button>')
        return ""
    history = ''.join('<tr><td>' + esc(channel_titles.get(post.channel_id, "Канал отключён")) +
                      '</td><td>' + esc(post.scheduled_at.isoformat()) +
                      '</td><td>' + esc(post.text[:70]) + '</td><td>' +
                      esc(post.status) + '</td><td>' + history_action(post) +
                      '</td></tr>' for post in posts)
    link = (f'<a class="btn primary" href="{esc(bot_url)}" target="_blank" rel="noopener noreferrer">'
            'Добавить бота в канал ↗</a>') if bot_url else '<p>Бот публикаций ещё не настроен администратором.</p>'
    base = f"/studio/products/{product_id}"
    content = f"""
<link rel="stylesheet" href="/studio/assets/comments.css?v=2">
<div id="social-app" data-api="/products/{product_id}/social">
<nav class="c-tabs"><a class="btn ghost" href="{base}/comments">Комментарии</a>
<a class="btn primary" href="{base}/social">Каналы и публикации</a>
<a class="btn ghost" href="{base}/calendar">Контент-план</a></nav>
<p id="social-feedback" role="status" aria-live="polite"></p>
<section class="panel c-card">
<h2>Подключение Telegram-канала</h2>
<p>Один бот может публиковать в нескольких каналах. Токен бота вводить не нужно.</p>
{link}
<form id="social-connect">
<label>После добавления бота укажите @канал или ID -100… <input name="chat" required placeholder="@my_business_channel"></label>
<button type="submit" class="btn primary">Продолжить подключение</button>
</form></section>
<div class="c-platform-grid">{''.join(cards) or '<p>Каналы пока не подключены.</p>'}</div>
<section class="panel c-card">
<h2>Запланировать публикацию</h2>
<p>Каждому выбранному каналу создаётся самостоятельное задание. Тексты по согласованному контент-плану можно включить отдельно у каждого канала.</p>
<form id="social-post">
<fieldset><legend>Каналы</legend>{options or 'Сначала подключите канал.'}</fieldset>
<label>Текст поста<textarea name="text" rows="5" maxlength="4096" placeholder="Текст поста или подпись к медиа"></textarea></label>
<label>Фото или MP4 (до 10 файлов, каждый до 20 МБ)<input name="files" type="file" multiple accept="image/jpeg,image/png,video/mp4"></label>
<label>Дата и время по вашему устройству<input name="when" type="datetime-local" required></label>
<button class="btn primary" type="submit">Поставить в расписание</button>
</form></section>
<section class="panel c-card"><h2>Последние публикации</h2>
<div style="overflow-x:auto"><table><thead><tr><th>Канал</th><th>Время</th><th>Материал</th><th>Статус</th><th>Действия</th></tr></thead>
<tbody>{history or '<tr><td colspan="5">Публикаций пока нет</td></tr>'}</tbody></table></div>
<p>Статус «delivery_unknown» требует ручной проверки в Telegram: повторная отправка заблокирована, чтобы избежать дублей.</p></section>
</div><script src="/studio/assets/social.js" defer></script>
"""
    return HTMLResponse(studio_page_shell(product, active="comments", title="Социальные сети",
                                          subtitle="Комментарии, каналы и автопубликации", body=content),
                        headers={"Cache-Control": "no-store"})


@router.post("/products/{product_id}/social/channels", dependencies=[Depends(same_origin)])
async def connect(product_id: UUID, value: Begin, db: Session = Depends(get_db), settings=Depends(get_settings)):
    try:
        return serialize(await start_connect(db, settings, product_id, value.chat.strip()))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise error(exc)


@router.get("/products/{product_id}/social/channels/{channel_id}/telegram-login")
async def telegram_login(product_id: UUID, channel_id: UUID, request: Request,
                         db: Session = Depends(get_db), settings=Depends(get_settings)):
    """Signed Telegram identity plus channel-admin verification, on the owner's authenticated website."""
    destination = f"/studio/products/{product_id}/social"
    try:
        data = dict(request.query_params)
        if len(data) != len(request.query_params):
            raise ValueError("Некорректные параметры входа")
        await confirm_connect(db, settings, product_id, channel_id, data)
    except (ValueError, LookupError) as exc:
        db.rollback()
        return HTMLResponse("<h2>Подключение не завершено</h2><p>" + esc(str(exc)) +
                            "</p><a href='" + destination +
                            "'>Вернуться к Telegram-каналам</a>", status_code=409)
    return RedirectResponse(destination, status_code=303)


@router.post("/products/{product_id}/social/channels/{channel_id}/options", dependencies=[Depends(same_origin)])
def options(product_id: UUID, channel_id: UUID, value: ChannelOptions, db: Session = Depends(get_db)):
    try:
        row = get_channel(db, product_id, channel_id)
        if row.status != "active":
            raise ValueError("Сначала подтвердите подключение")
        row.auto_plan = value.auto_plan
        db.commit()
        return serialize(row)
    except (ValueError, LookupError) as exc:
        raise error(exc)


@router.post("/products/{product_id}/social/channels/{channel_id}/disconnect", dependencies=[Depends(same_origin)])
def disconnect(product_id: UUID, channel_id: UUID, db: Session = Depends(get_db)):
    try:
        row = get_channel(db, product_id, channel_id)
        row.status, row.auto_plan = "disconnected", False
        for post in db.scalars(select(TelegramPost).where(
            TelegramPost.channel_id == row.id, TelegramPost.status == "scheduled")):
            post.status = "cancelled"
        db.commit()
        return {"status": "disconnected"}
    except (ValueError, LookupError) as exc:
        raise error(exc)


@router.post("/products/{product_id}/social/upload", dependencies=[Depends(same_origin)])
async def upload(product_id: UUID, file: UploadFile = File(...), db: Session = Depends(get_db), settings=Depends(get_settings)):
    if not db.get(Product, product_id):
        raise HTTPException(404, "Бизнес не найден")
    if file.content_type not in {"image/jpeg", "image/png", "video/mp4"}:
        raise HTTPException(422, "Поддерживаются JPG, PNG, MP4")
    # Read no more than configured application limit into memory.
    data = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Файл превышает 20 МБ")
    if file.content_type == "image/jpeg" and not data.startswith(bytes.fromhex("ffd8ff")):
        raise HTTPException(422, "Некорректный JPG")
    if file.content_type == "image/png" and not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        raise HTTPException(422, "Некорректный PNG")
    if file.content_type == "video/mp4" and data[4:8] != b"ftyp":
        raise HTTPException(422, "Некорректный MP4")
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "video/mp4": ".mp4"}[file.content_type]
    media = TelegramMedia(product_id=product_id,
        kind="video" if extension == ".mp4" else "photo",
        file_name=str(uuid4()) + extension, file_size=len(data))
    target = storage_path(settings.products_root, product_id, media)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as dest:
        dest.write(data)
        dest.flush()
        os.fsync(dest.fileno())
    try:
        db.add(media)
        db.commit()
        return {"id": str(media.id), "kind": media.kind, "bytes": media.file_size}
    except Exception:
        target.unlink(missing_ok=True)
        raise


@router.post("/products/{product_id}/social/posts", dependencies=[Depends(same_origin)])
def schedule(product_id: UUID, value: Schedule, db: Session = Depends(get_db)):
    if value.scheduled_at.tzinfo is None or value.scheduled_at.utcoffset() is None:
        raise HTTPException(422, "Укажите часовой пояс")
    try:
        ids = create_posts(db, product_id, value.channels, value.text, value.media, value.scheduled_at)
        return {"scheduled": len(ids), "ids": ids}
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise error(exc)


@router.post("/products/{product_id}/social/posts/{post_id}/resolve", dependencies=[Depends(same_origin)])
def resolve_post(product_id: UUID, post_id: UUID, decision: PostDecision, db: Session = Depends(get_db)):
    """Owner may cancel a queued post or manually reconcile ambiguous delivery.
    Manual confirmation is not Telegram API proof and does not fabricate message IDs.
    """
    post = db.scalar(select(TelegramPost).where(
        TelegramPost.id == post_id, TelegramPost.product_id == product_id).with_for_update())
    if not post:
        raise HTTPException(404, "Публикация не найдена")
    if decision.action == "cancel" and post.status in {"scheduled", "delivery_unknown"}:
        post.status, post.error_code = "cancelled", "owner_reconciled"
    elif decision.action == "confirm_delivered" and post.status in {"sending", "delivery_unknown"}:
        post.status, post.error_code = "owner_confirmed", "owner_confirmed_not_api_verified"
    else:
        raise HTTPException(409, "Статус публикации уже изменился. Обновите страницу.")
    db.commit()
    return {"id": str(post.id), "status": post.status}


@router.get("/products/{product_id}/comments/oauth/{platform}/start")
def oauth_start(product_id: UUID, platform: str, db: Session = Depends(get_db),
                settings=Depends(get_settings)):
    from app.social.oauth import authorize
    try:
        return RedirectResponse(authorize(db, settings, product_id, platform), status_code=303)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise error(exc)


@router.get("/social/oauth/{platform}/callback")
async def oauth_callback(platform: str, state: str = "", code: str = "",
                         error_message: str = "", db: Session = Depends(get_db),
                         settings=Depends(get_settings)):
    """Fixed callback shared across all companies; product scope is bound to one-time state."""
    from app.social.oauth import complete
    if error_message:
        return HTMLResponse("<h1>Подключение отменено</h1><p><a href='/studio'>Вернуться в Growth Studio</a></p>",
                            status_code=409)
    try:
        channel = await complete(db, settings, platform, state, code)
    except (ValueError, LookupError) as exc:
        db.rollback()
        return HTMLResponse("<h1>Не удалось подключить аккаунт</h1><p>" + esc(str(exc)) +
                            "</p><p><a href='/studio'>Вернуться в Growth Studio</a></p>", status_code=409)
    return RedirectResponse(f"/studio/products/{channel.product_id}/comments?tab=platforms", status_code=303)
