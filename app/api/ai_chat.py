"""FastAPI router for ServGo AI Chat (/studio/chat).

Serves the standalone AI Chat workspace and exposes both /studio/api/ai-chat/*
(forwarded through nginx /studio rules) and /api/ai-chat/* endpoints under the
existing Studio authentication and CSRF origin checks.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai_chat.client import AiChatClient, AiChatProviderError
from app.ai_chat.files import MAX_ATTACHMENT_BYTES, ChatFileError, resolve_attachment_path
from app.ai_chat.schemas import (
    ConversationCreateIn,
    ConversationDetailOut,
    ConversationSummaryOut,
    ConversationUpdateIn,
    MessageCreateIn,
)
from app.ai_chat.specialists import specialist_catalog
from app.ai_chat.service import (
    ChatError,
    ChatPrincipal,
    build_default_ai_chat_client,
    create_conversation,
    create_message_turn,
    delete_conversation,
    get_conversation_detail,
    get_owned_attachment,
    list_conversations,
    resolve_authorized_product_id,
    retry_assistant_turn,
    serialize_attachment,
    serialize_conversation,
    stop_request,
    store_uploaded_file,
    stream_request_events,
    update_conversation,
)
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.llm.model_catalog import ai_chat_catalog
from app.models.domain import Product
from app.voice.dictionary import SLUG as VOICE_PROJECT_SLUG

ASSETS = Path(__file__).parent / "ai_chat_assets"
ASSET_VERSION = "5"

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])


class RetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(default=None, max_length=150)


def get_ai_chat_client(settings: Settings = Depends(get_settings)) -> AiChatClient:
    """Dependency for ClaudeHub AI Chat client; overridden in tests."""
    return build_default_ai_chat_client(settings)


def get_catalog_transport():
    """Optional httpx transport override for model catalog discovery tests."""
    return None


def _principal(request: Request) -> ChatPrincipal:
    login = getattr(request.state, "studio_login", None) or "local-owner"
    allowed = getattr(request.state, "studio_product_ids", None)
    return ChatPrincipal(
        owner_account=str(login),
        allowed_product_ids=tuple(str(x) for x in allowed) if allowed is not None else None,
    )


def _handle_chat_error(exc: ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _initial_product_id(request: Request, db: Session, principal: ChatPrincipal, product_id: UUID | None) -> UUID | None:
    if product_id is not None:
        try:
            return resolve_authorized_product_id(db, principal, product_id)
        except ChatError as exc:
            raise _handle_chat_error(exc) from None
    cookie_pid = request.cookies.get("studio_last_product")
    if cookie_pid:
        try:
            parsed = UUID(cookie_pid)
            return resolve_authorized_product_id(db, principal, parsed)
        except (ValueError, ChatError):
            pass
    if principal.allowed_product_ids is not None:
        if not principal.allowed_product_ids:
            return None
        try:
            return resolve_authorized_product_id(db, principal, UUID(principal.allowed_product_ids[0]))
        except (ValueError, ChatError):
            return None
    return db.scalar(select(Product.id).order_by(Product.created_at.asc()).limit(1))


def _authorized_products(db: Session, principal: ChatPrincipal) -> list[dict]:
    query = select(Product).order_by(Product.created_at.asc()).limit(25)
    if principal.allowed_product_ids is not None:
        ids = []
        for raw in principal.allowed_product_ids:
            try:
                ids.append(UUID(raw))
            except ValueError:
                continue
        if not ids:
            return []
        query = select(Product).where(Product.id.in_(ids)).order_by(Product.created_at.asc()).limit(25)
    return [{"id": str(row.id), "name": row.name} for row in db.scalars(query)]


def _company_context_payload(db: Session, settings: Settings, product_id: UUID | None) -> dict | None:
    if product_id is None:
        return None
    prod = db.get(Product, product_id)
    if prod is None:
        return None
    name = prod.name
    category = ""
    one_liner = (prod.description or "").strip()[:160]
    services: list[str] = []
    topics: list[dict] = []

    try:
        from app.company_profile import load_company_profile

        loaded = load_company_profile(settings.products_root, prod.slug)
        if loaded.profile is not None:
            ident = loaded.profile.identity
            name = (ident.display_name or ident.name or name).strip()[:120]
            category = (ident.industry or ident.category or "").strip()[:100]
            if ident.one_liner:
                one_liner = ident.one_liner.strip()[:160]
            services = [
                srv.name.strip()[:80]
                for srv in (loaded.profile.offer.products_services or [])[:3]
                if getattr(srv, "name", None) and srv.name.strip()
            ]
    except Exception:
        pass

    try:
        from app.dispatcher.hints import topic_ideas

        for chip in topic_ideas(settings.products_root, prod.slug)[:4]:
            topic_text = str(chip.params.get("topic") or "").strip()
            if topic_text:
                topics.append({"label": chip.label[:64], "topic": topic_text[:160]})
    except Exception:
        pass

    return {
        "id": str(prod.id),
        "name": name,
        "category": category,
        "oneLiner": one_liner,
        "services": services,
        "topics": topics,
    }


@router.get("/studio/chat", response_class=HTMLResponse)
def ai_chat_page(
    request: Request,
    product_id: UUID | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    resolved_pid = _initial_product_id(request, db, principal, product_id)
    voice_project = ""
    if resolved_pid is not None:
        prod = db.get(Product, resolved_pid)
        if prod is not None and VOICE_PROJECT_SLUG.match(prod.slug or ""):
            voice_project = prod.slug
    company_context = _company_context_payload(db, settings, resolved_pid)
    boot_config = {
        "apiBase": "/studio/api/ai-chat",
        "productId": str(resolved_pid) if resolved_pid else None,
        "companyContext": company_context,
        "companies": _authorized_products(db, principal),
        "studioHome": f"/studio/products/{resolved_pid}" if resolved_pid else "/studio",
        "voiceUrl": "/studio/voice/dictate",
        "voicePreviewUrl": "/studio/voice/preview",
        "voiceProject": voice_project,
        "voiceMaxSeconds": int(settings.voice_max_audio_seconds),
    }
    boot_json = json.dumps(boot_config, ensure_ascii=False).replace("<", "\\u003c")
    template = (ASSETS / "chat.html").read_text(encoding="utf-8")
    page = template.replace("__ASSET_VERSION__", ASSET_VERSION).replace(
        "__BOOT_CONFIG__", boot_json
    ).replace(
        "__STUDIO_HOME__", html.escape(boot_config["studioHome"], quote=True)
    )
    response = HTMLResponse(page)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/studio/assets/ai-chat.css", include_in_schema=False)
def ai_chat_css():
    response = Response((ASSETS / "ai-chat.css").read_text(encoding="utf-8"), media_type="text/css")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/studio/assets/ai-chat.js", include_in_schema=False)
def ai_chat_js():
    response = Response(
        (ASSETS / "ai-chat.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/studio/api/ai-chat/models")
@router.get("/api/ai-chat/models")
async def get_models(
    settings: Settings = Depends(get_settings),
    transport=Depends(get_catalog_transport),
):
    data = await ai_chat_catalog(settings, transport=transport)
    return JSONResponse(data, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/api/ai-chat/specialists")
@router.get("/api/ai-chat/specialists")
def get_specialists():
    return JSONResponse({"specialists": specialist_catalog()}, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/api/ai-chat/conversations", response_model=list[ConversationSummaryOut])
@router.get("/api/ai-chat/conversations", response_model=list[ConversationSummaryOut])
def get_conversations(
    request: Request,
    q: str | None = None,
    product_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    principal = _principal(request)
    try:
        rows = list_conversations(db, principal, query=q, product_id=product_id)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return [serialize_conversation(row) for row in rows]


@router.post("/studio/api/ai-chat/conversations", response_model=ConversationSummaryOut, status_code=201)
@router.post("/api/ai-chat/conversations", response_model=ConversationSummaryOut, status_code=201)
def post_conversation(
    request: Request,
    body: ConversationCreateIn,
    db: Session = Depends(get_db),
):
    principal = _principal(request)
    try:
        conv = create_conversation(db, principal, body)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return serialize_conversation(conv)


@router.get("/studio/api/ai-chat/conversations/{conversation_id}", response_model=ConversationDetailOut)
@router.get("/api/ai-chat/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    principal = _principal(request)
    try:
        conv, messages = get_conversation_detail(db, principal, conversation_id)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return ConversationDetailOut(conversation=serialize_conversation(conv), messages=messages)


@router.patch("/studio/api/ai-chat/conversations/{conversation_id}", response_model=ConversationSummaryOut)
@router.patch("/api/ai-chat/conversations/{conversation_id}", response_model=ConversationSummaryOut)
def patch_conversation(
    conversation_id: UUID,
    body: ConversationUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
):
    principal = _principal(request)
    try:
        conv = update_conversation(db, principal, conversation_id, body)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return serialize_conversation(conv)


@router.delete("/studio/api/ai-chat/conversations/{conversation_id}", status_code=204)
@router.delete("/api/ai-chat/conversations/{conversation_id}", status_code=204)
def remove_conversation(
    conversation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    try:
        delete_conversation(db, settings, principal, conversation_id)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return Response(status_code=204)


@router.post("/studio/api/ai-chat/conversations/{conversation_id}/messages", status_code=201)
@router.post("/api/ai-chat/conversations/{conversation_id}/messages", status_code=201)
def post_message(
    conversation_id: UUID,
    body: MessageCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    try:
        conv, user_msg, assistant_msg, req, deduplicated = create_message_turn(
            db, settings, principal, conversation_id, body
        )
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return {
        "conversation": serialize_conversation(conv).model_dump(mode="json"),
        "user_message": user_msg.model_dump(mode="json"),
        "assistant_message": assistant_msg.model_dump(mode="json"),
        "request": {
            "id": str(req.id),
            "status": req.status,
            "mode": req.mode,
            "model_id": req.model_id,
        },
        "deduplicated": deduplicated,
    }


@router.post("/studio/api/ai-chat/conversations/{conversation_id}/messages/{message_id}/retry", status_code=201)
@router.post("/api/ai-chat/conversations/{conversation_id}/messages/{message_id}/retry", status_code=201)
def post_retry_message(
    conversation_id: UUID,
    message_id: UUID,
    request: Request,
    body: RetryIn | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    try:
        conv, assistant_msg, req = retry_assistant_turn(
            db,
            settings,
            principal,
            conversation_id,
            message_id,
            model_id=body.model_id if body else None,
        )
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return {
        "conversation": serialize_conversation(conv).model_dump(mode="json"),
        "assistant_message": assistant_msg.model_dump(mode="json"),
        "request": {
            "id": str(req.id),
            "status": req.status,
            "mode": req.mode,
            "model_id": req.model_id,
        },
    }


@router.get("/studio/api/ai-chat/requests/{request_id}/events")
@router.get("/api/ai-chat/requests/{request_id}/events")
def get_request_events(
    request_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    client: AiChatClient = Depends(get_ai_chat_client),
):
    principal = _principal(request)
    bind = db.get_bind()
    session_factory = sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)
    # Release any read snapshot before entering long-lived SSE response.
    db.rollback()
    return StreamingResponse(
        stream_request_events(
            session_factory=session_factory,
            settings=settings,
            principal=principal,
            request_id=request_id,
            client=client,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/studio/api/ai-chat/requests/{request_id}/stop")
@router.post("/api/ai-chat/requests/{request_id}/stop")
def post_stop_request(
    request_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    principal = _principal(request)
    try:
        return stop_request(db, principal, request_id)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None


@router.post("/studio/api/ai-chat/files", status_code=201)
@router.post("/api/ai-chat/files", status_code=201)
async def post_upload_file(
    request: Request,
    file: UploadFile = File(...),
    product_id: Optional[UUID] = Form(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "Размер файла превышает 20 МБ.")
        chunks.append(chunk)
    raw = b"".join(chunks)

    try:
        att = await store_uploaded_file(
            db,
            settings,
            principal,
            filename=file.filename or "file",
            data=raw,
            product_id=product_id,
        )
    except ChatFileError as exc:
        raise HTTPException(422, str(exc)) from None
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    return serialize_attachment(att)


@router.get("/studio/api/ai-chat/files/{attachment_id}")
@router.get("/api/ai-chat/files/{attachment_id}")
def get_uploaded_file(
    attachment_id: UUID,
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    principal = _principal(request)
    try:
        att = get_owned_attachment(db, principal, attachment_id)
        path = resolve_attachment_path(settings.products_root, att.storage_path)
    except ChatError as exc:
        raise _handle_chat_error(exc) from None
    except LookupError:
        raise HTTPException(404, "Файл не найден") from None

    is_safe_image = att.mime_type in {"image/png", "image/jpeg", "image/webp"}
    disposition = "inline" if (is_safe_image and not download) else "attachment"
    response = FileResponse(
        path,
        media_type=att.mime_type,
        filename=att.filename,
        content_disposition_type=disposition,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
