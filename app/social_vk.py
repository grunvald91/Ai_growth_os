"""VK community OAuth, encrypted credentials and at-most-once approved-post publishing.

Each publication is an owner-approved snapshot and has a durable intent. A
network failure after claiming delivery is ambiguous, never retried blindly.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.comments.contracts import ConnectInput
from app.comments.gateway import CommentGateway
from app.comments.models import CommentChannel
from app.comments.secrets import CommentSecrets
from app.comments.service import CommentService
from app.comments.vk import VKAdapter
from app.content.revisions import source_fingerprint
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.services.campaign_lifecycle import closing_campaign_for_material

OAUTH_COOKIE = "sg_vk_publish_oauth"
OAUTH_PATH = "/studio/social/vk/callback"
OAUTH_TTL = 600
API_TIMEOUT = 25


def redirect_uri(settings):
    origin = (settings.social_vk_redirect_base or "").rstrip("/")
    value = urlsplit(origin)
    if (value.scheme != "https" or not value.netloc or value.username or value.password
            or value.path or value.query or value.fragment):
        raise ValueError("Администратор должен настроить публичный HTTPS origin VK")
    return origin + OAUTH_PATH


def client_id(settings):
    app_id = str(settings.social_vk_client_id or "").strip()
    if not re.fullmatch(r"[1-9]\d{3,20}", app_id):
        raise ValueError("Не настроено приложение VK ID (SOCIAL_VK_CLIENT_ID)")
    return app_id


def group_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d{0,14}", value):
        raise ValueError("Укажите положительный числовой ID сообщества VK")
    return value


class VKStore(CommentSecrets):
    """Share the durable encrypted integration key, not an unencrypted token table."""

    def oauth_path(self, channel_id):
        return self.root / (str(UUID(str(channel_id))) + ".vk-oauth")

    def save_oauth(self, channel_id, data):
        with self.locked():
            encrypted = self.cipher().encrypt(json.dumps(data).encode("utf-8"))
            path = self.oauth_path(channel_id)
            temp = Path(str(path) + ".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as writer:
                writer.write(encrypted)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temp, path)

    def load_oauth(self, channel_id):
        with self.locked():
            try:
                value = self.cipher().decrypt(self.oauth_path(channel_id).read_bytes())
                return json.loads(value)
            except (OSError, InvalidToken, ValueError):
                return {}

    def forget(self, channel_id):
        with self.locked():
            for path in (self.path(channel_id), self.oauth_path(channel_id)):
                path.unlink(missing_ok=True)


def begin_oauth(settings, product_id, account_id, *, owner_login=None):
    group_id(account_id)
    app_id = client_id(settings)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    payload = {
        "state": state, "verifier": verifier, "product_id": str(product_id),
        "account_id": account_id, "owner_login": owner_login,
        "redirect_uri": redirect_uri(settings),
    }
    store = VKStore(settings.products_root)
    with store.locked():
        cookie = store.cipher().encrypt(json.dumps(payload).encode()).decode("ascii")
    # These are the VK ID authorization-code + PKCE fields used by the official SDK.
    query = urlencode({
        "client_id": app_id, "redirect_uri": payload["redirect_uri"],
        "response_type": "code", "state": state, "scope": "groups wall",
        "code_challenge": challenge, "code_challenge_method": "s256",
    })
    return "https://id.vk.ru/authorize?" + query, cookie


def read_oauth_cookie(settings, cookie, state):
    if not isinstance(cookie, str) or not cookie:
        raise ValueError("Подключение VK истекло. Начните заново")
    try:
        store = VKStore(settings.products_root)
        with store.locked():
            raw = store.cipher().decrypt(cookie.encode("ascii"), ttl=OAUTH_TTL)
        data = json.loads(raw)
    except (OSError, InvalidToken, UnicodeError, ValueError, TypeError, AttributeError):
        raise ValueError("Подключение VK истекло. Начните заново") from None
    if (not isinstance(data, dict) or not isinstance(state, str)
            or not secrets.compare_digest(data.get("state", ""), state)
            or data.get("redirect_uri") != redirect_uri(settings)):
        raise ValueError("Состояние OAuth не совпало")
    group_id(data.get("account_id"))
    UUID(data["product_id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,150}", data.get("verifier", "")):
        raise ValueError("Неверный PKCE-параметр")
    return data


async def token_request(settings, *, params, data):
    # Fixed official host; never follow redirects or forward a bearer token.
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, trust_env=False, follow_redirects=False) as client:
            response = await client.post("https://id.vk.ru/oauth2/auth", params=params, data=data)
        result = response.json()
        if response.status_code != 200 or not isinstance(result, dict) or result.get("error"):
            raise ValueError("VK ID отклонил доступ. Проверьте права приложения")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("VK ID отклонил"):
            raise
        raise ValueError("Не удалось связаться с VK ID") from None


def expiry(value):
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        seconds = 3600
    return (datetime.now(timezone.utc) + timedelta(seconds=max(60, min(seconds, 365 * 86400)))).isoformat()


async def finish_oauth(db, settings, payload, state, code, device_id):
    if not code or len(code) > 4000 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", device_id or ""):
        raise ValueError("VK ID не вернул действительный код и device_id")
    product_id = UUID(payload["product_id"])
    if db.get(Product, product_id) is None:
        raise LookupError("Компания недоступна")
    result = await token_request(settings, params={
        "grant_type": "authorization_code", "client_id": client_id(settings),
        "redirect_uri": redirect_uri(settings), "code_verifier": payload["verifier"],
        "state": state, "device_id": device_id,
    }, data={"code": code})
    if result.get("state") != state or not result.get("access_token"):
        raise ValueError("VK ID не подтвердил параметры подключения")
    connection = await CommentService(db, settings).connect(product_id, ConnectInput(
        platform="vk", account_id=payload["account_id"], token=result["access_token"]))
    # OAuth success supersedes any previous community-token publishing connection.
    for old in db.scalars(select(Artifact).where(Artifact.product_id == product_id,
            Artifact.kind == "vk_publish_connection", Artifact.status == ArtifactStatus.APPROVED)):
        old.status = ArtifactStatus.REJECTED
    db.commit()
    VKStore(settings.products_root).save_oauth(connection.id, {
        "device_id": device_id, "refresh_token": result.get("refresh_token"),
        "expires_at": expiry(result.get("expires_in")),
    })
    return connection


async def active_token(settings, channel):
    store = VKStore(settings.products_root)
    access = store.load_token(channel.id)
    info = store.load_oauth(channel.id)
    if not info.get("expires_at") or not info.get("refresh_token"):
        return access  # A legacy manual token; provider validation is authoritative.
    try:
        end = datetime.fromisoformat(info["expires_at"])
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        end = datetime.min.replace(tzinfo=timezone.utc)
    if end > datetime.now(timezone.utc) + timedelta(minutes=5):
        return access
    state = secrets.token_urlsafe(24)
    result = await token_request(settings, params={
        "grant_type": "refresh_token", "client_id": client_id(settings),
        "redirect_uri": redirect_uri(settings), "state": state,
        "device_id": info["device_id"],
    }, data={"refresh_token": info["refresh_token"]})
    if result.get("state") != state or not result.get("access_token"):
        raise ValueError("VK не обновил доступ. Подключите сообщество заново")
    info["expires_at"] = expiry(result.get("expires_in"))
    info["refresh_token"] = result.get("refresh_token") or info["refresh_token"]
    store.save_token(channel.id, result["access_token"])
    store.save_oauth(channel.id, info)
    return result["access_token"]


def connected(db, product_id):
    # Dedicated publisher token takes precedence over legacy comment-token connection.
    row = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "vk_publish_connection",
        Artifact.status == ArtifactStatus.APPROVED).order_by(Artifact.created_at.desc()).limit(1))
    if row:
        try:
            details = json.loads(row.content)
            account_id = group_id(details["group_id"])
        except (TypeError, ValueError, KeyError):
            return None
        return SimpleNamespace(id=row.id, product_id=product_id, account_id=account_id,
            account_name="Сообщество VK #" + account_id, is_community_token=True)
    account = db.scalar(select(CommentChannel).where(
        CommentChannel.product_id == product_id,
        CommentChannel.platform == "vk", CommentChannel.status == "connected"))
    if account:
        return SimpleNamespace(id=account.id, product_id=product_id,
            account_id=account.account_id, account_name=account.account_name,
            is_community_token=False)
    return None


def connect_community(db, settings, product_id, account_id, token):
    """Owner supplies the official community key; never claim permission before a real post."""
    group_id(account_id)
    if not isinstance(token, str) or not token or len(token) > 8000 or any(
            ord(ch) < 32 for ch in token):
        raise ValueError("Неверный ключ доступа сообщества")
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    running = db.scalar(select(Publication.id).where(
        Publication.product_id == product_id, Publication.channel == "vk",
        Publication.status == "sending").limit(1))
    if running:
        raise ValueError("Нельзя менять ключ во время отправки VK")
    existing = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "vk_publish_connection",
        Artifact.status == ArtifactStatus.APPROVED).with_for_update()))
    row = Artifact(product_id=product_id, kind="vk_publish_connection",
        title="Публикации VK: сообщество #" + account_id,
        status=ArtifactStatus.APPROVED,
        content=json.dumps({"group_id": account_id, "auth_type": "community_token"}))
    db.add(row)
    db.flush()
    VKStore(settings.products_root).save_token(row.id, token)
    for previous in existing:
        previous.status = ArtifactStatus.REJECTED
    db.commit()
    for previous in existing:
        VKStore(settings.products_root).forget(previous.id)
    return row


def approved_posts(db, product_id):
    rows = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "channel_text",
        Artifact.status == ArtifactStatus.APPROVED,
    ).order_by(Artifact.created_at.desc()).limit(150))
    output = []
    for row in rows:
        try:
            body = json.loads(row.content)
        except (TypeError, ValueError):
            continue
        if body.get("channel") == "vk" and isinstance(body.get("body"), str) and body["body"].strip():
            output.append(row)
    return output


def schedule(db, product_id, post_id, when):
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Для публикации укажите часовой пояс")
    now = datetime.now(timezone.utc)
    if not now - timedelta(seconds=10) <= when <= now + timedelta(days=90):
        raise ValueError("Дата публикации должна быть в пределах следующих 90 дней")
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    channel = connected(db, product_id)
    if channel is None:
        raise ValueError("Подключите сообщество VK")
    post = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.id == post_id,
        Artifact.kind == "channel_text",
    ).with_for_update().execution_options(populate_existing=True))
    if post is None or post.status != ArtifactStatus.APPROVED:
        raise ValueError("Утвердите материал для ВКонтакте")
    try:
        value = json.loads(post.content)
    except (ValueError, TypeError):
        raise ValueError("Некорректный материал") from None
    if value.get("channel") != "vk" or not isinstance(value.get("body"), str):
        raise ValueError("Это не подготовленный пост VK")
    text = value["body"].strip()
    if not text or len(text) > 15000:
        raise ValueError("Для VK требуется текст длиной до 15000 символов")
    pending = db.scalar(select(Task.id).where(
        Task.product_id == product_id, Task.assigned_agent == "content_editor",
        Task.title == "EDIT: " + str(post.id),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if pending or closing_campaign_for_material(db, product_id, post.id):
        raise ValueError("Редактирование или остановка кампании запрещает отправку")
    existing = db.scalar(select(Publication.id).where(
        Publication.product_id == product_id, Publication.artifact_id == post.id,
        Publication.channel == "vk",
        Publication.status.notin_(["cancelled", "failed_before_send"])).limit(1))
    if existing:
        raise ValueError("Публикация этого материала уже назначена или выполнена")
    pub = Publication(product_id=product_id, artifact_id=post.id, channel="vk",
                      status="scheduled", scheduled_at=when.astimezone(timezone.utc))
    db.add(pub)
    db.flush()
    db.add(Artifact(product_id=product_id, kind="vk_publish_intent",
        title="VK schedule " + str(pub.id), status=ArtifactStatus.APPROVED,
        content=json.dumps({
            "publication_id": str(pub.id), "post_id": str(post.id),
            "source_version": post.version, "source_digest": source_fingerprint(post),
            "channel_id": str(channel.id), "group_id": channel.account_id,
            "text": text, "scheduled_at": pub.scheduled_at.isoformat(),
        }, ensure_ascii=False)))
    db.commit()
    return pub


def cancel(db, product_id, publication_id):
    pub = db.scalar(select(Publication).where(
        Publication.product_id == product_id, Publication.id == publication_id,
        Publication.channel == "vk").with_for_update())
    if pub is None:
        raise LookupError("Публикация не найдена")
    if pub.status != "scheduled":
        raise ValueError("Можно отменить только ещё не отправленный пост")
    pub.status = "cancelled"
    db.commit()
    return pub


async def deliver_once(session_factory, settings, *, sender=None, now=None):
    """Claim, commit, send once; all uncertain outcomes require manual inspection."""
    now = now or datetime.now(timezone.utc)
    with session_factory() as db:
        stale = list(db.scalars(select(Publication).where(
            Publication.channel == "vk", Publication.status == "sending",
            ((Publication.sending_started_at < now - timedelta(minutes=15)) |
             (Publication.sending_started_at.is_(None)))).with_for_update(skip_locked=True)))
        for old in stale:
            old.status = "delivery_unknown"
        if stale:
            db.commit()
        if not settings.content_vk_autopost_enabled:
            return 0
        pub = db.scalar(select(Publication).where(
            Publication.channel == "vk", Publication.status == "scheduled",
            Publication.scheduled_at <= now
        ).order_by(Publication.scheduled_at, Publication.id)
            .limit(1).with_for_update(skip_locked=True))
        if pub is None:
            return 0
        intent = db.scalar(select(Artifact).where(
            Artifact.product_id == pub.product_id, Artifact.kind == "vk_publish_intent",
            Artifact.title == "VK schedule " + str(pub.id)))
        post = db.scalar(select(Artifact).where(
            Artifact.id == pub.artifact_id, Artifact.product_id == pub.product_id))
        channel = connected(db, pub.product_id)
        try:
            data = json.loads(intent.content) if intent else {}
            if (intent is None or intent.status != ArtifactStatus.APPROVED
                    or post is None or post.status != ArtifactStatus.APPROVED
                    or channel is None or str(channel.id) != data.get("channel_id")
                    or channel.account_id != data.get("group_id")
                    or str(post.id) != data.get("post_id")
                    or post.version != data.get("source_version")
                    or source_fingerprint(post) != data.get("source_digest")
                    or closing_campaign_for_material(db, pub.product_id, post.id)):
                raise ValueError("Материал или доступ изменились после подтверждения")
        except (ValueError, TypeError, KeyError):
            pub.status = "failed_before_send"
            db.commit()
            return 1
        pub.status = "sending"
        pub.sending_started_at = now
        publication_id = pub.id
        publication_product_id = pub.product_id
        group = channel.account_id
        channel_id = channel.id
        text = data["text"]
        db.commit()
    try:
        if sender is None:
            with session_factory() as db:
                ch = connected(db, publication_product_id)
                if ch is None or ch.id != channel_id or ch.account_id != group:
                    raise ValueError("VK отключён или сменилось сообщество")
                if ch.is_community_token:
                    token = VKStore(settings.products_root).load_token(ch.id)
                else:
                    token = await active_token(settings, ch)
            adapter = VKAdapter(CommentGateway(), "vk")
            if not ch.is_community_token:
                await adapter.manager(group, token)
            response = await adapter.call("wall.post", token, owner_id=-int(group),
                    from_group=1, message=text, guid=str(publication_id))
            post_id = response.get("post_id") if isinstance(response, dict) else None
        else:
            post_id = await sender(group, text, str(publication_id))
        if not isinstance(post_id, int) or post_id < 1:
            raise ValueError("VK не подтвердил ID опубликованного поста")
    except Exception:
        with session_factory() as db:
            pub = db.get(Publication, publication_id)
            if pub and pub.status == "sending":
                pub.status = "delivery_unknown"
                db.commit()
        return 1
    with session_factory() as db:
        pub = db.get(Publication, publication_id)
        if pub and pub.status == "sending":
            pub.status = "published"
            pub.external_reference = f"vk:-{group}_{post_id}"
            pub.published_at = datetime.now(timezone.utc)
            db.commit()
    return 1
