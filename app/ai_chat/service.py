"""Core business service for ServGo AI Chat (/studio/chat)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import time
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ai_chat.client import (
    AiChatClient,
    AiChatOutcomeUnknownError,
    AiChatProviderError,
    ClaudeHubAiChatClient,
)
from app.ai_chat.context import build_chat_context
from app.ai_chat.files import (
    MAX_ATTACHMENT_BYTES,
    MAX_FILES_PER_MESSAGE,
    remove_attachment_file,
    resolve_attachment_path,
    validate_and_extract_upload,
    write_attachment_bytes,
)
from app.ai_chat.models import (
    AiChatAttachment,
    AiChatConversation,
    AiChatMessage,
    AiChatRequest,
)
from app.ai_chat.schemas import (
    AttachmentOut,
    ConversationCreateIn,
    ConversationSummaryOut,
    ConversationUpdateIn,
    MessageCreateIn,
    MessageOut,
)
from app.core.config import Settings
from app.llm.costing import estimate_cost_usd
from app.llm.model_catalog import ConfirmedModelCapabilities, resolve_model_capabilities
from app.llm.router_store import RouterStoreError, read_state
from app.models.domain import CostRecord, Product

_logger = logging.getLogger(__name__)
_ACTIVE_CANCELS: dict[UUID, asyncio.Event] = {}


class ChatError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ChatNotFoundError(ChatError):
    def __init__(self, message: str = "Диалог или объект не найден") -> None:
        super().__init__(message, status_code=404)


class ChatValidationError(ChatError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class ChatLimitError(ChatError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=429)


class ChatConflictError(ChatError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


@dataclass(frozen=True)
class ChatPrincipal:
    owner_account: str
    allowed_product_ids: tuple[str, ...] | None  # None means unrestricted admin


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_authorized_product_id(
    db: Session,
    principal: ChatPrincipal,
    requested_product_id: UUID | None,
) -> UUID | None:
    if requested_product_id is None:
        return None
    if principal.allowed_product_ids is not None and str(requested_product_id) not in principal.allowed_product_ids:
        raise ChatNotFoundError("Компания не найдена")
    product = db.get(Product, requested_product_id)
    if product is None:
        raise ChatNotFoundError("Компания не найдена")
    return product.id


def _assert_product_scope(principal: ChatPrincipal, product_id: UUID | None) -> None:
    if principal.allowed_product_ids is None or product_id is None:
        return
    if str(product_id) not in principal.allowed_product_ids:
        raise ChatNotFoundError("Доступ к объекту ограничен")


def get_owned_conversation(
    db: Session,
    principal: ChatPrincipal,
    conversation_id: UUID,
) -> AiChatConversation:
    conv = db.get(AiChatConversation, conversation_id)
    if conv is None or conv.owner_account != principal.owner_account:
        raise ChatNotFoundError("Диалог не найден")
    _assert_product_scope(principal, conv.product_id)
    return conv


def get_owned_attachment(
    db: Session,
    principal: ChatPrincipal,
    attachment_id: UUID,
) -> AiChatAttachment:
    att = db.get(AiChatAttachment, attachment_id)
    if att is None or att.owner_account != principal.owner_account:
        raise ChatNotFoundError("Файл не найден")
    _assert_product_scope(principal, att.product_id)
    return att


def _attachment_url(attachment_id: UUID) -> str:
    return f"/studio/api/ai-chat/files/{attachment_id}"


def serialize_attachment(att: AiChatAttachment) -> AttachmentOut:
    is_img = att.mime_type.startswith("image/") or att.kind == "generated_image"
    return AttachmentOut(
        id=att.id,
        kind=att.kind,
        filename=att.filename,
        mime_type=att.mime_type,
        byte_size=att.byte_size,
        is_image=is_img,
        url=_attachment_url(att.id),
        extraction_status=att.extraction_status,
        extraction_note=att.extraction_note,
        width=att.width,
        height=att.height,
    )


def serialize_conversation(conv: AiChatConversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=conv.id,
        product_id=conv.product_id,
        title=conv.title,
        selected_model=conv.selected_model,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


def serialize_message(
    msg: AiChatMessage,
    *,
    attachments: list[AiChatAttachment] | None = None,
    request_id: UUID | None = None,
) -> MessageOut:
    return MessageOut(
        id=msg.id,
        conversation_id=msg.conversation_id,
        seq=msg.seq,
        role=msg.role,
        mode=msg.mode,
        content=msg.content,
        model_id=msg.model_id,
        status=msg.status,
        quote_message_id=msg.quote_message_id,
        quote_text=msg.quote_text,
        attachments=[serialize_attachment(a) for a in (attachments or []) if a.kind == "upload"],
        generated_images=list(msg.generated_images or []),
        context_truncated=bool(msg.context_truncated),
        context_omitted_count=int(msg.context_omitted_count or 0),
        error_message=msg.error_message,
        request_id=request_id,
        created_at=msg.created_at,
    )


def list_conversations(
    db: Session,
    principal: ChatPrincipal,
    *,
    query: str | None = None,
    product_id: UUID | None = None,
    limit: int = 100,
) -> list[AiChatConversation]:
    stmt = select(AiChatConversation).where(AiChatConversation.owner_account == principal.owner_account)
    if principal.allowed_product_ids is not None:
        allowed_uuids = [UUID(x) for x in principal.allowed_product_ids]
        stmt = stmt.where(
            (AiChatConversation.product_id.is_(None))
            | (AiChatConversation.product_id.in_(allowed_uuids))
        )
    if product_id is not None:
        resolve_authorized_product_id(db, principal, product_id)
        stmt = stmt.where(AiChatConversation.product_id == product_id)
    if query and query.strip():
        needle = f"%{query.strip()}%"
        stmt = stmt.where(AiChatConversation.title.ilike(needle))
    stmt = stmt.order_by(
        AiChatConversation.last_message_at.desc().nullslast(),
        AiChatConversation.updated_at.desc(),
        AiChatConversation.created_at.desc(),
    ).limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt))


def create_conversation(
    db: Session,
    principal: ChatPrincipal,
    body: ConversationCreateIn,
) -> AiChatConversation:
    pid = resolve_authorized_product_id(db, principal, body.product_id)
    title = " ".join((body.title or "").split())[:200] or "Новый чат"
    conv = AiChatConversation(
        owner_account=principal.owner_account,
        product_id=pid,
        title=title,
        selected_model=body.model_id,
        last_message_at=None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def update_conversation(
    db: Session,
    principal: ChatPrincipal,
    conversation_id: UUID,
    body: ConversationUpdateIn,
) -> AiChatConversation:
    conv = get_owned_conversation(db, principal, conversation_id)
    if body.title is not None:
        cleaned = " ".join(body.title.split())[:200]
        if not cleaned:
            raise ChatValidationError("Название диалога не может быть пустым.")
        conv.title = cleaned
    if body.selected_model is not None:
        conv.selected_model = body.selected_model
    db.commit()
    db.refresh(conv)
    return conv


def delete_conversation(
    db: Session,
    settings: Settings,
    principal: ChatPrincipal,
    conversation_id: UUID,
) -> None:
    conv = get_owned_conversation(db, principal, conversation_id)
    attachments = list(
        db.scalars(
            select(AiChatAttachment).where(AiChatAttachment.conversation_id == conv.id)
        )
    )
    storage_paths = [att.storage_path for att in attachments]

    # Explicit child cleanup ensures deterministic deletion on both SQLite and PostgreSQL.
    for req in db.scalars(select(AiChatRequest).where(AiChatRequest.conversation_id == conv.id)):
        if req.id in _ACTIVE_CANCELS:
            _ACTIVE_CANCELS[req.id].set()
        db.delete(req)
    for att in attachments:
        db.delete(att)
    for msg in db.scalars(select(AiChatMessage).where(AiChatMessage.conversation_id == conv.id)):
        db.delete(msg)
    db.delete(conv)
    db.commit()

    for sp in storage_paths:
        remove_attachment_file(settings.products_root, sp)


def get_conversation_detail(
    db: Session,
    principal: ChatPrincipal,
    conversation_id: UUID,
) -> tuple[AiChatConversation, list[MessageOut]]:
    conv = get_owned_conversation(db, principal, conversation_id)
    messages = list(
        db.scalars(
            select(AiChatMessage)
            .where(AiChatMessage.conversation_id == conv.id)
            .order_by(AiChatMessage.seq.asc(), AiChatMessage.created_at.asc())
        )
    )
    attachments = list(
        db.scalars(
            select(AiChatAttachment)
            .where(AiChatAttachment.conversation_id == conv.id)
            .order_by(AiChatAttachment.created_at.asc())
        )
    )
    att_by_msg: dict[UUID, list[AiChatAttachment]] = {}
    for att in attachments:
        if att.message_id is not None:
            att_by_msg.setdefault(att.message_id, []).append(att)

    requests = list(
        db.scalars(
            select(AiChatRequest)
            .where(AiChatRequest.conversation_id == conv.id)
            .order_by(AiChatRequest.created_at.desc())
        )
    )
    req_by_assistant: dict[UUID, UUID] = {}
    for req in requests:
        if req.assistant_message_id is not None and req.assistant_message_id not in req_by_assistant:
            req_by_assistant[req.assistant_message_id] = req.id

    out = [
        serialize_message(
            msg,
            attachments=att_by_msg.get(msg.id, []),
            request_id=req_by_assistant.get(msg.id),
        )
        for msg in messages
    ]
    return conv, out


async def store_uploaded_file(
    db: Session,
    settings: Settings,
    principal: ChatPrincipal,
    *,
    filename: str,
    data: bytes,
    product_id: UUID | None = None,
) -> AiChatAttachment:
    pid = resolve_authorized_product_id(db, principal, product_id)
    parsed = await validate_and_extract_upload(filename, data)
    att_id = uuid4()
    storage_rel = write_attachment_bytes(
        settings.products_root,
        att_id,
        parsed.extension,
        parsed.normalized_bytes,
    )
    att = AiChatAttachment(
        id=att_id,
        owner_account=principal.owner_account,
        product_id=pid,
        conversation_id=None,
        message_id=None,
        kind="upload",
        filename=parsed.filename,
        storage_path=storage_rel,
        mime_type=parsed.mime_type,
        byte_size=parsed.byte_size,
        sha256=parsed.sha256,
        extracted_text=parsed.extracted_text,
        extraction_status=parsed.extraction_status,
        extraction_note=parsed.extraction_note,
        width=parsed.width,
        height=parsed.height,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return att


def _derive_title(content: str, mode: str, attachments: list[AiChatAttachment]) -> str:
    text = " ".join((content or "").split())
    if text:
        if mode == "image" and not text.lower().startswith("изображение"):
            base = f"Изображение: {text}"
        else:
            base = text
    elif attachments:
        base = f"Файл: {attachments[0].filename}"
    else:
        base = "Новый чат"
    return base if len(base) <= 72 else base[:71].rstrip() + "…"


def _check_user_limits(db: Session, settings: Settings, principal: ChatPrincipal) -> None:
    now = _utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)

    hour_count = db.scalar(
        select(func.count(AiChatRequest.id)).where(
            AiChatRequest.owner_account == principal.owner_account,
            AiChatRequest.created_at >= hour_ago,
        )
    ) or 0
    if hour_count >= settings.ai_chat_max_requests_per_hour:
        raise ChatLimitError(
            f"Достигнут часовой лимит запросов ({settings.ai_chat_max_requests_per_hour}). Попробуйте позже."
        )

    day_count = db.scalar(
        select(func.count(AiChatRequest.id)).where(
            AiChatRequest.owner_account == principal.owner_account,
            AiChatRequest.created_at >= day_ago,
        )
    ) or 0
    if day_count >= settings.ai_chat_max_requests_per_day:
        raise ChatLimitError(
            f"Достигнут дневной лимит запросов ({settings.ai_chat_max_requests_per_day})."
        )

    if settings.ai_chat_daily_spend_limit_usd:
        try:
            limit_usd = Decimal(settings.ai_chat_daily_spend_limit_usd)
        except InvalidOperation:
            limit_usd = None
        if limit_usd is not None and limit_usd > 0:
            spent = db.scalar(
                select(func.coalesce(func.sum(AiChatRequest.estimated_cost_usd), 0)).where(
                    AiChatRequest.owner_account == principal.owner_account,
                    AiChatRequest.created_at >= day_ago,
                )
            ) or Decimal("0")
            if Decimal(str(spent)) >= limit_usd:
                raise ChatLimitError("Достигнут дневной лимит расходов AI Chat.")


def _validate_send_request(
    db: Session,
    settings: Settings,
    principal: ChatPrincipal,
    conv: AiChatConversation,
    body: MessageCreateIn,
    cap: ConfirmedModelCapabilities,
) -> list[AiChatAttachment]:
    text = (body.content or "").strip()
    if len(body.content or "") > settings.ai_chat_max_message_chars:
        raise ChatValidationError(
            f"Сообщение слишком длинное (максимум {settings.ai_chat_max_message_chars} символов)."
        )

    if body.mode == "image":
        if not cap.supports_image_generation:
            raise ChatValidationError(
                f"Модель «{cap.name}» не поддерживает генерацию изображений. Переключите модель или выберите текстовый режим."
            )
        if body.attachment_ids:
            raise ChatValidationError("В режиме генерации изображений прикрепление файлов пока не поддерживается.")
        if not text:
            raise ChatValidationError("Опишите изображение, которое нужно сгенерировать.")
        if body.image_size and cap.image_sizes and body.image_size not in cap.image_sizes:
            raise ChatValidationError("Выбранное разрешение не поддерживается этой моделью.")
        if body.image_size and not cap.image_sizes:
            raise ChatValidationError("Выбранная модель не поддерживает выбор разрешения.")
        return []

    if not cap.supports_text:
        raise ChatValidationError(
            f"Модель «{cap.name}» предназначена только для генерации изображений и не отвечает в текстовом чате."
        )
    if not text and not body.attachment_ids:
        raise ChatValidationError("Введите сообщение или прикрепите файл.")

    max_files = min(MAX_FILES_PER_MESSAGE, cap.max_files_per_message)
    if len(body.attachment_ids) > max_files:
        raise ChatValidationError(f"Можно прикрепить не более {max_files} файлов за одно сообщение.")

    attachments: list[AiChatAttachment] = []
    seen_ids: set[UUID] = set()
    total_bytes = 0
    max_bytes = min(MAX_ATTACHMENT_BYTES, cap.max_file_bytes)

    for att_id in body.attachment_ids:
        if att_id in seen_ids:
            continue
        seen_ids.add(att_id)
        att = get_owned_attachment(db, principal, att_id)
        if att.kind != "upload":
            raise ChatValidationError("Некорректный тип вложения.")
        if att.message_id is not None or (att.conversation_id is not None and att.conversation_id != conv.id):
            raise ChatValidationError("Файл уже прикреплён к другому сообщению.")
        if conv.product_id is not None and att.product_id is not None and att.product_id != conv.product_id:
            raise ChatNotFoundError("Файл относится к другой компании.")
        total_bytes += att.byte_size
        is_image = att.mime_type.startswith("image/")
        if is_image and not cap.supports_vision:
            raise ChatValidationError(
                f"Модель «{cap.name}» не поддерживает анализ изображений. Выберите модель с поддержкой изображений."
            )
        if not is_image and not cap.supports_files:
            raise ChatValidationError(
                f"Модель «{cap.name}» не поддерживает прикрепление файлов."
            )
        attachments.append(att)

    if total_bytes > max_bytes:
        raise ChatValidationError("Суммарный размер вложений превышает 20 МБ.")

    return attachments


def create_message_turn(
    db: Session,
    settings: Settings,
    principal: ChatPrincipal,
    conversation_id: UUID,
    body: MessageCreateIn,
) -> tuple[AiChatConversation, MessageOut, MessageOut, AiChatRequest, bool]:
    conv = get_owned_conversation(db, principal, conversation_id)

    if body.client_key:
        existing_user = db.scalar(
            select(AiChatMessage).where(
                AiChatMessage.conversation_id == conv.id,
                AiChatMessage.client_key == body.client_key,
            )
        )
        if existing_user is not None:
            existing_req = db.scalar(
                select(AiChatRequest).where(
                    AiChatRequest.conversation_id == conv.id,
                    AiChatRequest.user_message_id == existing_user.id,
                )
            )
            existing_assistant = (
                db.get(AiChatMessage, existing_req.assistant_message_id)
                if existing_req and existing_req.assistant_message_id
                else None
            )
            atts = list(
                db.scalars(select(AiChatAttachment).where(AiChatAttachment.message_id == existing_user.id))
            )
            if existing_assistant is not None and existing_req is not None:
                return (
                    conv,
                    serialize_message(existing_user, attachments=atts),
                    serialize_message(existing_assistant, request_id=existing_req.id),
                    existing_req,
                    True,
                )

    if body.quote_message_id is not None:
        quoted = db.get(AiChatMessage, body.quote_message_id)
        if quoted is None or quoted.conversation_id != conv.id:
            raise ChatNotFoundError("Цитируемое сообщение не найдено в этом диалоге.")

    cap = resolve_model_capabilities(body.model_id)
    attachments = _validate_send_request(db, settings, principal, conv, body, cap)
    _check_user_limits(db, settings, principal)

    max_seq = db.scalar(
        select(func.coalesce(func.max(AiChatMessage.seq), 0)).where(
            AiChatMessage.conversation_id == conv.id
        )
    ) or 0

    now = _utcnow()
    user_msg = AiChatMessage(
        conversation_id=conv.id,
        seq=max_seq + 1,
        role="user",
        mode=body.mode,
        content=(body.content or "").strip(),
        model_id=body.model_id,
        status="completed",
        quote_message_id=body.quote_message_id,
        quote_text=body.quote_text.strip() if body.quote_text and body.quote_text.strip() else None,
        client_key=body.client_key,
    )
    assistant_msg = AiChatMessage(
        conversation_id=conv.id,
        seq=max_seq + 2,
        role="assistant",
        mode=body.mode,
        content="",
        model_id=body.model_id,
        status="pending",
    )
    db.add(user_msg)
    db.add(assistant_msg)
    db.flush()

    for att in attachments:
        att.conversation_id = conv.id
        att.message_id = user_msg.id

    req = AiChatRequest(
        owner_account=principal.owner_account,
        product_id=conv.product_id,
        conversation_id=conv.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant_msg.id,
        mode=body.mode,
        model_id=body.model_id,
        provider="claudehub",
        status="queued",
        idempotency_key=body.client_key,
        error_detail=json.dumps({"image_size": body.image_size}) if body.image_size else None,
    )
    db.add(req)

    if conv.title == "Новый чат" and max_seq == 0:
        conv.title = _derive_title(user_msg.content, body.mode, attachments)
    conv.selected_model = body.model_id
    conv.last_message_at = now

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if body.client_key:
            return create_message_turn(db, settings, principal, conversation_id, body)
        raise
    db.refresh(conv)
    db.refresh(user_msg)
    db.refresh(assistant_msg)
    db.refresh(req)
    return (
        conv,
        serialize_message(user_msg, attachments=attachments),
        serialize_message(assistant_msg, request_id=req.id),
        req,
        False,
    )


def retry_assistant_turn(
    db: Session,
    settings: Settings,
    principal: ChatPrincipal,
    conversation_id: UUID,
    message_id: UUID,
    *,
    model_id: str | None = None,
) -> tuple[AiChatConversation, MessageOut, AiChatRequest]:
    """Explicit conscious retry requested by the user for an assistant response."""
    conv = get_owned_conversation(db, principal, conversation_id)
    target = db.get(AiChatMessage, message_id)
    if target is None or target.conversation_id != conv.id or target.role != "assistant":
        raise ChatNotFoundError("Сообщение для повтора не найдено.")

    active_req = db.scalar(
        select(AiChatRequest).where(
            AiChatRequest.conversation_id == conv.id,
            AiChatRequest.assistant_message_id == target.id,
        ).order_by(AiChatRequest.created_at.desc())
    )
    if active_req is not None and active_req.status in {"queued", "streaming"}:
        raise ChatConflictError("Генерация ещё выполняется. Сначала остановите текущий запрос.")

    source_user = db.scalar(
        select(AiChatMessage)
        .where(
            AiChatMessage.conversation_id == conv.id,
            AiChatMessage.role == "user",
            AiChatMessage.seq < target.seq,
        )
        .order_by(AiChatMessage.seq.desc())
    )
    if source_user is None:
        raise ChatValidationError("Не найдено исходное сообщение пользователя для повтора.")

    chosen_model = (model_id or target.model_id or conv.selected_model or settings.llm_model).strip()
    cap = resolve_model_capabilities(chosen_model)
    if source_user.mode == "image" and not cap.supports_image_generation:
        raise ChatValidationError(f"Модель «{cap.name}» не поддерживает генерацию изображений.")
    if source_user.mode == "text" and not cap.supports_text:
        raise ChatValidationError(f"Модель «{cap.name}» не поддерживает текстовые диалоги.")

    source_attachments = list(
        db.scalars(select(AiChatAttachment).where(AiChatAttachment.message_id == source_user.id))
    )
    for att in source_attachments:
        if att.kind == "upload" and att.mime_type.startswith("image/") and not cap.supports_vision:
            raise ChatValidationError(
                f"Модель «{cap.name}» не поддерживает анализ изображений в этом сообщении."
            )

    _check_user_limits(db, settings, principal)

    max_seq = db.scalar(
        select(func.coalesce(func.max(AiChatMessage.seq), 0)).where(
            AiChatMessage.conversation_id == conv.id
        )
    ) or 0

    assistant_msg = AiChatMessage(
        conversation_id=conv.id,
        seq=max_seq + 1,
        role="assistant",
        mode=source_user.mode,
        content="",
        model_id=chosen_model,
        status="pending",
    )
    db.add(assistant_msg)
    db.flush()

    req = AiChatRequest(
        owner_account=principal.owner_account,
        product_id=conv.product_id,
        conversation_id=conv.id,
        user_message_id=source_user.id,
        assistant_message_id=assistant_msg.id,
        mode=source_user.mode,
        model_id=chosen_model,
        provider="claudehub",
        status="queued",
        idempotency_key=f"retry:{uuid4()}",
    )
    db.add(req)
    conv.selected_model = chosen_model
    conv.last_message_at = _utcnow()
    db.commit()
    db.refresh(conv)
    db.refresh(assistant_msg)
    db.refresh(req)
    return conv, serialize_message(assistant_msg, request_id=req.id), req


def stop_request(
    db: Session,
    principal: ChatPrincipal,
    request_id: UUID,
) -> dict:
    req = db.get(AiChatRequest, request_id)
    if req is None or req.owner_account != principal.owner_account:
        raise ChatNotFoundError("Запрос не найден.")
    _assert_product_scope(principal, req.product_id)

    event = _ACTIVE_CANCELS.get(req.id)
    if event is not None:
        event.set()

    if req.status in {"queued", "streaming"}:
        req.status = "stopped"
        req.finished_at = _utcnow()
        if req.assistant_message_id is not None:
            msg = db.get(AiChatMessage, req.assistant_message_id)
            if msg is not None and msg.status in {"pending", "streaming"}:
                msg.status = "stopped"
        db.commit()

    return {"id": str(req.id), "status": req.status}


def _estimate_request_cost(
    settings: Settings,
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    if input_tokens is None or output_tokens is None:
        return None
    try:
        profiles = read_state(settings).config.profiles
    except (RouterStoreError, ValueError, OSError):
        return None
    profile = next((p for p in profiles if p.get("model") == model_id), None)
    if not profile:
        return None
    in_price = profile.get("input_usd_per_million")
    out_price = profile.get("output_usd_per_million")
    if in_price is None or out_price is None:
        return None
    try:
        return estimate_cost_usd(
            input_tokens,
            output_tokens,
            input_usd_per_million=Decimal(str(in_price)),
            output_usd_per_million=Decimal(str(out_price)),
        )
    except (InvalidOperation, ValueError):
        return None


def _record_cost_if_scoped(
    db: Session,
    req: AiChatRequest,
) -> None:
    if req.product_id is None:
        return
    amount = req.estimated_cost_usd if req.estimated_cost_usd is not None else Decimal("0")
    db.add(
        CostRecord(
            product_id=req.product_id,
            category="ai_chat",
            provider=req.provider,
            amount=amount,
            currency="USD",
            input_tokens=req.input_tokens,
            output_tokens=req.output_tokens,
            incurred_at=req.finished_at or _utcnow(),
            details={
                "ai_chat_request_id": str(req.id),
                "conversation_id": str(req.conversation_id),
                "model_id": req.model_id,
                "mode": req.mode,
                "status": req.status,
                "cost_known": False,
                "estimate_known": req.estimated_cost_usd is not None,
            },
        )
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_default_ai_chat_client(settings: Settings) -> AiChatClient:
    return ClaudeHubAiChatClient(
        api_key=settings.xai_api_key or "",
        base_url=settings.xai_base_url,
        timeout_seconds=settings.xai_timeout_seconds,
    )


async def stream_request_events(
    *,
    session_factory: sessionmaker,
    settings: Settings,
    principal: ChatPrincipal,
    request_id: UUID,
    client: AiChatClient,
) -> AsyncIterator[str]:
    """Execute or replay an AI Chat request as an SSE stream.

    Uses short-lived DB sessions so long provider streams never hold DB connections open.
    """
    with session_factory() as db:
        req = db.get(AiChatRequest, request_id)
        if req is None or req.owner_account != principal.owner_account:
            yield _sse("error", {"status": "error", "error": "Запрос не найден.", "outcome_unknown": False})
            return
        try:
            _assert_product_scope(principal, req.product_id)
        except ChatNotFoundError:
            yield _sse("error", {"status": "error", "error": "Доступ к запросу ограничен.", "outcome_unknown": False})
            return

        assistant_msg = db.get(AiChatMessage, req.assistant_message_id) if req.assistant_message_id else None
        if assistant_msg is None:
            yield _sse("error", {"status": "error", "error": "Сообщение ответа не найдено.", "outcome_unknown": False})
            return

        if req.status in {"completed", "stopped", "error", "outcome_unknown"}:
            yield _sse(
                "done",
                {
                    "status": assistant_msg.status,
                    "message": serialize_message(assistant_msg, request_id=req.id).model_dump(mode="json"),
                },
            )
            return

        if req.status == "streaming":
            # Another connection already started this request; return current snapshot cleanly.
            yield _sse("status", {"status": "streaming", "request_id": str(req.id)})
            yield _sse(
                "done",
                {
                    "status": assistant_msg.status,
                    "message": serialize_message(assistant_msg, request_id=req.id).model_dump(mode="json"),
                },
            )
            return

        # Claim queued request for execution.
        req.status = "streaming"
        req.started_at = _utcnow()
        assistant_msg.status = "streaming"

        image_size = None
        if req.error_detail:
            try:
                parsed_meta = json.loads(req.error_detail)
                if isinstance(parsed_meta, dict):
                    image_size = parsed_meta.get("image_size")
            except ValueError:
                image_size = None
            req.error_detail = None

        history = list(
            db.scalars(
                select(AiChatMessage)
                .where(AiChatMessage.conversation_id == req.conversation_id)
                .order_by(AiChatMessage.seq.asc(), AiChatMessage.created_at.asc())
            )
        )
        attachments = list(
            db.scalars(
                select(AiChatAttachment).where(AiChatAttachment.conversation_id == req.conversation_id)
            )
        )
        att_by_msg: dict[UUID, list[AiChatAttachment]] = {}
        for att in attachments:
            if att.message_id is not None:
                att_by_msg.setdefault(att.message_id, []).append(att)

        user_msg = db.get(AiChatMessage, req.user_message_id) if req.user_message_id else None
        cap = resolve_model_capabilities(req.model_id)
        db.commit()

        conv_id = req.conversation_id
        assistant_msg_id = assistant_msg.id
        model_id = req.model_id
        mode = req.mode
        user_prompt = user_msg.content if user_msg else ""

    cancel_event = asyncio.Event()
    _ACTIVE_CANCELS[request_id] = cancel_event

    yield _sse("status", {"status": "streaming", "request_id": str(request_id)})

    try:
        if mode == "image":
            result = await client.generate_image(
                model=model_id,
                prompt=user_prompt,
                capabilities=cap,
                size=image_size,
            )
            att_id = uuid4()
            rel_path = write_attachment_bytes(settings.products_root, att_id, ".png", result.png_bytes)
            image_meta = {
                "attachment_id": str(att_id),
                "filename": f"generated-{str(att_id)[:8]}.png",
                "mime_type": "image/png",
                "url": _attachment_url(att_id),
                "width": result.width,
                "height": result.height,
                "model_id": result.model,
                "prompt": user_prompt,
            }
            with session_factory() as db:
                req = db.get(AiChatRequest, request_id)
                msg = db.get(AiChatMessage, assistant_msg_id)
                if req and msg:
                    att = AiChatAttachment(
                        id=att_id,
                        owner_account=principal.owner_account,
                        product_id=req.product_id,
                        conversation_id=conv_id,
                        message_id=msg.id,
                        kind="generated_image",
                        filename=image_meta["filename"],
                        storage_path=rel_path,
                        mime_type="image/png",
                        byte_size=len(result.png_bytes),
                        sha256=result.sha256,
                        extraction_status="ready",
                        width=result.width,
                        height=result.height,
                    )
                    db.add(att)
                    msg.generated_images = [image_meta]
                    msg.status = "completed"
                    req.status = "completed"
                    req.finished_at = _utcnow()
                    _record_cost_if_scoped(db, req)
                    db.commit()
                    db.refresh(msg)
                    yield _sse("image", {"images": [image_meta]})
                    yield _sse(
                        "done",
                        {
                            "status": "completed",
                            "message": serialize_message(msg, request_id=req.id).model_dump(mode="json"),
                        },
                    )
            return

        # Text chat mode
        ctx = build_chat_context(
            history=history,
            attachments_by_message=att_by_msg,
            capabilities=cap,
            products_root=settings.products_root,
            current_user_message_id=user_msg.id if user_msg else None,
        )
        with session_factory() as db:
            msg = db.get(AiChatMessage, assistant_msg_id)
            if msg:
                msg.context_truncated = ctx.context_truncated
                msg.context_omitted_count = ctx.context_omitted_count
                db.commit()

        yield _sse(
            "meta",
            {
                "context_truncated": ctx.context_truncated,
                "context_omitted_count": ctx.context_omitted_count,
                "model_id": model_id,
            },
        )

        chunks_acc: list[str] = []
        last_flush = time.monotonic()
        in_tokens: int | None = None
        out_tokens: int | None = None

        async for chunk in client.stream_chat(
            model=model_id,
            system_prompt=ctx.system_prompt,
            messages=ctx.messages,
            capabilities=cap,
            conversation_id=str(conv_id),
            cancel_event=cancel_event,
        ):
            if cancel_event.is_set():
                break
            if chunk.delta:
                chunks_acc.append(chunk.delta)
                yield _sse("delta", {"text": chunk.delta})
            if chunk.input_tokens is not None:
                in_tokens = chunk.input_tokens
            if chunk.output_tokens is not None:
                out_tokens = chunk.output_tokens
            if time.monotonic() - last_flush >= 1.5 and chunks_acc:
                with session_factory() as db:
                    msg = db.get(AiChatMessage, assistant_msg_id)
                    if msg and msg.status == "streaming":
                        msg.content = "".join(chunks_acc)
                        db.commit()
                last_flush = time.monotonic()

        full_text = "".join(chunks_acc)
        final_status = "stopped" if cancel_event.is_set() else "completed"

        with session_factory() as db:
            req = db.get(AiChatRequest, request_id)
            msg = db.get(AiChatMessage, assistant_msg_id)
            if req and msg:
                if req.status == "stopped":
                    final_status = "stopped"
                msg.content = full_text
                msg.status = final_status
                req.status = final_status
                req.input_tokens = in_tokens
                req.output_tokens = out_tokens
                req.estimated_cost_usd = _estimate_request_cost(settings, model_id, in_tokens, out_tokens)
                req.finished_at = _utcnow()
                _record_cost_if_scoped(db, req)
                db.commit()
                db.refresh(msg)
                yield _sse(
                    "done",
                    {
                        "status": final_status,
                        "message": serialize_message(msg, request_id=req.id).model_dump(mode="json"),
                    },
                )
    except AiChatOutcomeUnknownError as exc:
        _logger.warning("ai_chat_outcome_unknown request_id=%s", request_id)
        with session_factory() as db:
            req = db.get(AiChatRequest, request_id)
            msg = db.get(AiChatMessage, assistant_msg_id)
            if req and msg:
                msg.status = "error"
                msg.error_message = str(exc)
                req.status = "outcome_unknown"
                req.error_code = "outcome_unknown"
                req.error_detail = str(exc)
                req.finished_at = _utcnow()
                db.commit()
        yield _sse("error", {"status": "error", "error": str(exc), "outcome_unknown": True})
    except AiChatProviderError as exc:
        _logger.warning("ai_chat_provider_error request_id=%s", request_id)
        with session_factory() as db:
            req = db.get(AiChatRequest, request_id)
            msg = db.get(AiChatMessage, assistant_msg_id)
            if req and msg:
                msg.status = "error"
                msg.error_message = str(exc)
                req.status = "error"
                req.error_code = "provider_error"
                req.error_detail = str(exc)
                req.finished_at = _utcnow()
                db.commit()
        yield _sse("error", {"status": "error", "error": str(exc), "outcome_unknown": False})
    except Exception:
        _logger.exception("ai_chat_unexpected_error request_id=%s", request_id)
        err_msg = "Внутренняя ошибка при обработке запроса."
        with session_factory() as db:
            req = db.get(AiChatRequest, request_id)
            msg = db.get(AiChatMessage, assistant_msg_id)
            if req and msg:
                msg.status = "error"
                msg.error_message = err_msg
                req.status = "error"
                req.error_code = "internal_error"
                req.error_detail = err_msg
                req.finished_at = _utcnow()
                db.commit()
        yield _sse("error", {"status": "error", "error": err_msg, "outcome_unknown": False})
    finally:
        _ACTIVE_CANCELS.pop(request_id, None)
