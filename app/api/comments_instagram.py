"""Authenticated Instagram OAuth setup and public signed Meta comment webhook.

The protected router must be mounted with Studio auth + CSRF. Only the webhook
router is public: it authenticates the raw request with the Meta app secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select

from app.api.comments import service
from app.api.content_studio import esc, studio_page_shell
from app.comments.instagram_oauth import (
    MAX_STATE_AGE_SECONDS, MetaUnifiedClient, configured, new_state,
    redirect_uri, state_product_id, verify_state,
)
from app.comments.models import BusinessComment, CommentChannel
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Artifact, ArtifactStatus, Product

studio_router = APIRouter()
public_router = APIRouter()
COOKIE_NAME = "servgo_comment_ig_oauth"
MAX_WEBHOOK_BYTES = 256 * 1024


def cookie_path(product_id):
    # One common callback for all companies; never send the nonce to unrelated paths.
    return "/studio/integrations/instagram/oauth"


def owner(request: Request):
    return getattr(request.state, "studio_login", None) or "development-owner"


def pending_artifact(svc, product_id, artifact_id, login, *, lock=False):
    svc.product(product_id)
    query = select(Artifact).where(
        Artifact.id == artifact_id, Artifact.product_id == product_id,
        Artifact.kind == "comment_instagram_oauth_pending",
    )
    row = svc.db.scalar(query.with_for_update() if lock else query)
    if not row or row.status != ArtifactStatus.DRAFT:
        raise HTTPException(404, "Подключение Instagram не найдено или завершено")
    try:
        data = json.loads(row.content)
        if data["login"] != login or float(data["expires_at"]) < time.time():
            raise HTTPException(403, "Срок подключения истёк. Начните заново")
        if not isinstance(data["accounts"], list):
            raise ValueError("missing accounts")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(403, "Недействительный запрос подключения") from None
    return row, data


@studio_router.get("/products/{product_id}/comments/instagram/oauth/start")
async def instagram_oauth_start(product_id: UUID, request: Request, svc=Depends(service)):
    svc.product(product_id)
    if not configured(svc.settings):
        raise HTTPException(503, "Настройте Meta App ID, Secret, Config ID и публичный адрес в сервисе")
    state, nonce = new_state(svc.settings, product_id, owner(request))
    uri = redirect_uri(svc.settings, product_id)
    url = MetaUnifiedClient(svc.settings).authorization_url(uri, state)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(COOKIE_NAME, nonce, max_age=MAX_STATE_AGE_SECONDS,
                        httponly=True, secure=uri.startswith("https://"),
                        samesite="lax", path=cookie_path(product_id))
    response.headers["Cache-Control"] = "no-store"
    return response


@studio_router.get("/studio/integrations/instagram/oauth/callback")
async def instagram_oauth_callback(request: Request,
                                   code: str = "", state: str = "",
                                   svc=Depends(service)):
    hinted = state_product_id(state)
    try:
        product_id = UUID(hinted) if hinted else None
    except ValueError:
        product_id = None
    if product_id is None or not verify_state(
            svc.settings, state, request.cookies.get(COOKIE_NAME),
            product_id, owner(request)):
        raise HTTPException(403, "Истекла проверка безопасности Meta OAuth")
    allowed = getattr(request.state, "studio_product_ids", None)
    if allowed is not None and str(product_id) not in allowed:
        raise HTTPException(404, "Компания не найдена")
    svc.product(product_id)
    if not code:
        raise HTTPException(400, "Meta не выдала разрешение на подключение")
    client = MetaUnifiedClient(svc.settings)
    try:
        token = await client.exchange_code(code, redirect_uri(svc.settings, product_id))
        accounts = await client.accounts(token)
    except ValueError:
        raise HTTPException(409, "Meta не предоставила Instagram-аккаунты или доступ к ним") from None
    if not accounts:
        raise HTTPException(409, "Выберите профессиональный Instagram-аккаунт в Meta и повторите")
    artifact = Artifact(
        product_id=product_id, kind="comment_instagram_oauth_pending",
        title="Ожидает выбора Instagram-аккаунта",
        content=json.dumps({"login": owner(request), "accounts": accounts,
                            "expires_at": time.time() + MAX_STATE_AGE_SECONDS}),
    )
    svc.db.add(artifact)
    svc.db.flush()
    svc.secrets.save_token(artifact.id, token)
    svc.db.commit()
    response = RedirectResponse(
        f"/products/{product_id}/comments/instagram/oauth/select?pending={artifact.id}",
        status_code=303)
    response.delete_cookie(COOKIE_NAME, path=cookie_path(product_id))
    response.headers["Cache-Control"] = "no-store"
    return response


@studio_router.get("/products/{product_id}/comments/instagram/oauth/select",
                   response_class=HTMLResponse)
async def instagram_oauth_select(product_id: UUID, pending: UUID, request: Request,
                                 svc=Depends(service)):
    product = svc.product(product_id)
    _, data = pending_artifact(svc, product_id, pending, owner(request))
    options = "".join(
        f'<label style="display:block;padding:12px"><input type="radio" name="account_id" '
        f'value="{esc(account["id"])}" required> @{esc(account["username"])} '
        f'({esc(account["id"])})</label>'
        for account in data["accounts"]
    )
    form = (
        '<section class="panel" style="padding:24px;max-width:660px;margin:20px auto">'
        '<h2>Выберите аккаунт Instagram для подключения</h2>'
        '<p>Подключается только выбранный аккаунт к текущей компании. '
        'Ответы публикуются лишь после ручного подтверждения.</p>'
        f'<form method="post" action="/products/{product_id}/comments/instagram/oauth/finish">'
        f'<input type="hidden" name="pending" value="{pending}">{options}'
        '<button class="btn primary" type="submit">Подключить выбранный аккаунт</button></form>'
        '</section>'
    )
    return HTMLResponse(studio_page_shell(
        product, active="comments", title="Instagram", subtitle="Выбор Meta-аккаунта",
        body=form), headers={"Cache-Control": "no-store"})


@studio_router.post("/products/{product_id}/comments/instagram/oauth/finish")
async def instagram_oauth_finish(product_id: UUID, request: Request,
                                 svc=Depends(service)):
    if int(request.headers.get("content-length") or 0) > 4096:
        raise HTTPException(413, "Слишком большой запрос")
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Слишком большой запрос")
    params = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=False)
    try:
        pending_id = UUID(params["pending"][0])
        account_id = params["account_id"][0]
    except (KeyError, ValueError, IndexError):
        raise HTTPException(422, "Выберите Instagram-аккаунт") from None
    artifact, data = pending_artifact(svc, product_id, pending_id, owner(request), lock=True)
    if account_id not in {item["id"] for item in data["accounts"]}:
        raise HTTPException(403, "Аккаунт не выдавался этим OAuth-подключением")
    token = svc.secrets.load_token(artifact.id)
    client = MetaUnifiedClient(svc.settings)
    try:
        name = await client.verify_account(token, account_id)
        artifact.status = ArtifactStatus.APPROVED
        await svc.connect_verified_instagram(product_id, account_id, name, token)
    except ValueError:
        svc.db.rollback()
        raise HTTPException(409, "Не удалось подтвердить разрешения Instagram или привязку к компании") from None
    svc.secrets.delete(artifact.id)
    return RedirectResponse(f"/studio/products/{product_id}/comments?tab=platforms",
                            status_code=303, headers={"Cache-Control": "no-store"})


async def capped_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_WEBHOOK_BYTES):
        raise HTTPException(413, "Body too large")
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Body too large")
    return bytes(data)


@public_router.get("/api/integrations/instagram/webhook")
async def instagram_webhook_verify(request: Request, settings=Depends(get_settings)):
    # Only the exact operator-set verify token can complete Meta's handshake.
    mode = request.query_params.get("hub.mode")
    supplied = request.query_params.get("hub.verify_token") or ""
    challenge = request.query_params.get("hub.challenge") or ""
    expected = settings.comment_meta_webhook_verify_token
    if (not expected or mode != "subscribe"
            or not hmac.compare_digest(supplied, expected)
            or not (1 <= len(challenge) <= 256)
            or not all(c.isalnum() or c in "_.-" for c in challenge)):
        raise HTTPException(403, "Verification failed")
    return PlainTextResponse(challenge, headers={"Cache-Control": "no-store"})


@public_router.post("/api/integrations/instagram/webhook")
async def instagram_webhook_receive(request: Request, settings=Depends(get_settings),
                                    db=Depends(get_db)):
    if not settings.comment_meta_app_secret:
        raise HTTPException(503, "Meta webhook not configured")
    raw = await capped_body(request)
    supplied = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.comment_meta_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid webhook JSON") from None
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        raise HTTPException(422, "Unexpected webhook payload")
    entries = payload.get("entry")
    if not isinstance(entries, list) or len(entries) > 100:
        raise HTTPException(422, "Invalid webhook batch")
    imported = 0
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("id", "")).isdigit():
            continue
        account_id = str(entry["id"])
        matches = list(db.scalars(select(CommentChannel).where(
            CommentChannel.platform == "instagram",
            CommentChannel.account_id == account_id,
            CommentChannel.status == "connected",
        ).limit(2)))
        if len(matches) != 1:
            continue  # Unknown or ambiguous ownership must never fan out.
        channel = matches[0]
        # Serialize webhook with manual refresh and parallel delivery for tenant.
        db.scalar(select(Product).where(Product.id == channel.product_id).with_for_update())
        changes = entry.get("changes")
        if not isinstance(changes, list) or len(changes) > 100:
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "comments":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            comment_id = str(value.get("id") or value.get("comment_id") or "")
            media = value.get("media")
            media_id = str((media or {}).get("id") or value.get("media_id") or "") if isinstance(media, dict) or not media else ""
            creator = value.get("from") or {}
            creator_id = str(creator.get("id") or "") if isinstance(creator, dict) else ""
            comment = value.get("text") or ""
            if (not comment_id.isdigit() or not media_id.isdigit()
                    or not isinstance(comment, str) or not comment.strip()
                    or not creator_id or creator_id == account_id):
                continue
            existing = db.scalar(select(BusinessComment.id).where(
                BusinessComment.product_id == channel.product_id,
                BusinessComment.platform == "instagram",
                BusinessComment.external_id == comment_id))
            if existing:
                continue
            target = {"channel_id": str(channel.id), "media_id": media_id,
                      "ingest": "meta_webhook"}
            if value.get("timestamp") and isinstance(value["timestamp"], str):
                target["published_at"] = value["timestamp"]
            if value.get("parent_id") and str(value["parent_id"]).isdigit():
                target["parent_id"] = str(value["parent_id"])
            db.add(BusinessComment(
                product_id=channel.product_id, platform="instagram",
                external_id=comment_id, text=comment[:8000],
                post_text="", source="platform", status="new", target=target,
            ))
            db.flush()
            imported += 1
        db.commit()
    return JSONResponse({"accepted": True, "imported": imported},
                        headers={"Cache-Control": "no-store"})
