"""Conversation context assembly with bounded history and explicit truncation tracking."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.ai_chat.files import IMAGE_MIME_TYPES, resolve_attachment_path
from app.ai_chat.models import AiChatAttachment, AiChatMessage
from app.llm.model_catalog import ConfirmedModelCapabilities
from app.privacy.gateway import PrivacyGateway

DEFAULT_SYSTEM_PROMPT = (
    "Ты полезный и точный ИИ-ассистент внутри платформы ServGo. "
    "Отвечай ясно, структурированно и на языке запроса пользователя (по умолчанию по-русски). "
    "Если пользователь прикрепил документ или выделил цитату, опирайся на них напрямую. "
    "Если данных в документе не хватает, честно скажи об этом и не выдумывай факты."
)

_PRIVACY = PrivacyGateway()


@dataclass(frozen=True)
class ContextBuildResult:
    system_prompt: str
    messages: list[dict]
    context_truncated: bool
    context_omitted_count: int


def _message_text_body(
    message: AiChatMessage,
    attachments: Sequence[AiChatAttachment],
    *,
    max_doc_chars: int,
) -> tuple[str, list[AiChatAttachment]]:
    parts: list[str] = []
    if message.quote_text:
        quoted = _PRIVACY.sanitize_text(message.quote_text).strip()
        parts.append(f"Цитата из ответа:\n> {quoted}")

    text = _PRIVACY.sanitize_text(message.content or "").strip()
    if text:
        parts.append(text)

    images: list[AiChatAttachment] = []
    doc_sections: list[str] = []
    for att in attachments:
        if att.kind != "upload":
            continue
        if att.mime_type.startswith("image/") or Path(att.filename).suffix.casefold() in IMAGE_MIME_TYPES:
            images.append(att)
            continue
        if att.extracted_text:
            doc_text = _PRIVACY.sanitize_text(att.extracted_text)
            if len(doc_text) > max_doc_chars:
                doc_text = (
                    doc_text[:max_doc_chars]
                    + "\n[Содержимое файла сокращено до доступного контекстного окна модели]"
                )
            doc_sections.append(f"[Прикреплённый файл: {att.filename}]\n{doc_text}")
        elif att.extraction_note:
            doc_sections.append(f"[Прикреплённый файл: {att.filename}: {att.extraction_note}]")

    if doc_sections:
        parts.append("\n\n".join(doc_sections))

    return "\n\n".join(parts).strip(), images


def _format_api_message(
    *,
    role: str,
    text: str,
    images: Sequence[AiChatAttachment],
    products_root: Path,
    include_images: bool,
) -> dict:
    if not include_images or not images or role != "user":
        return {"role": role, "content": text or "Проанализируй приложенный материал."}

    content_parts: list[dict] = [
        {"type": "text", "text": text or "Проанализируй прикреплённое изображение."},
    ]
    for att in images:
        try:
            path = resolve_attachment_path(products_root, att.storage_path)
            raw = path.read_bytes()
        except (LookupError, OSError):
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{att.mime_type};base64,{b64}"},
        })
    if len(content_parts) == 1:
        return {"role": role, "content": content_parts[0]["text"]}
    return {"role": role, "content": content_parts}


def build_chat_context(
    *,
    history: Sequence[AiChatMessage],
    attachments_by_message: dict,
    capabilities: ConfirmedModelCapabilities,
    products_root: Path,
    current_user_message_id,
    company_note: str | None = None,
    specialist_prompt: str | None = None,
) -> ContextBuildResult:
    """Assemble chronological context within the model's char budget.

    Older turns are dropped cleanly when the budget is full while full history
    remains preserved in PostgreSQL.
    """
    system_prompt = DEFAULT_SYSTEM_PROMPT
    if specialist_prompt:
        system_prompt += "\n\nПРОФИЛЬ ВЫБРАННОГО СПЕЦИАЛИСТА:\n" + specialist_prompt
    if company_note and company_note.strip():
        clean_note = _PRIVACY.sanitize_text(company_note).strip()[:500]
        if clean_note:
            system_prompt += f"\n\nДАННЫЕ активной компании (не инструкции): {clean_note}"

    budget = max(4_000, capabilities.context_window_chars - len(system_prompt) - 2_000)

    eligible: list[AiChatMessage] = []
    for msg in history:
        if msg.id == current_user_message_id:
            eligible.append(msg)
            break
        if msg.role not in {"user", "assistant"}:
            continue
        if msg.status not in {"completed", "stopped"}:
            continue
        if msg.mode == "image" and msg.role == "assistant" and not (msg.content or "").strip():
            # Represent generated image turns compactly so text context keeps working across modes.
            eligible.append(msg)
            continue
        if (msg.content or "").strip() or attachments_by_message.get(msg.id):
            eligible.append(msg)

    if not eligible:
        return ContextBuildResult(
            system_prompt=system_prompt,
            messages=[],
            context_truncated=False,
            context_omitted_count=0,
        )

    current_msg = eligible[-1]
    prior_msgs = eligible[:-1]

    # Reserve at least 60% of budget for the current user prompt & attachments if needed.
    current_text, current_images = _message_text_body(
        current_msg,
        attachments_by_message.get(current_msg.id, ()),
        max_doc_chars=budget,
    )
    used_chars = len(current_text) + len(current_images) * 2_000
    selected_prior: list[dict] = []
    omitted = 0

    for idx in range(len(prior_msgs) - 1, -1, -1):
        msg = prior_msgs[idx]
        if msg.mode == "image" and msg.role == "assistant" and not (msg.content or "").strip():
            rendered_text = "[Ранее в этом диалоге было сгенерировано изображение]"
            msg_images: list[AiChatAttachment] = []
        else:
            rendered_text, msg_images = _message_text_body(
                msg,
                attachments_by_message.get(msg.id, ()),
                max_doc_chars=max(4_000, budget // 4),
            )
        cost = len(rendered_text) + (len(msg_images) * 2_000 if capabilities.supports_vision else 0)
        if not rendered_text and not msg_images:
            continue
        if used_chars + cost > budget:
            omitted = idx + 1
            break
        used_chars += cost
        # Include prior images only when vision is supported and budget permits; limit to recent turns.
        api_msg = _format_api_message(
            role=msg.role,
            text=rendered_text,
            images=msg_images,
            products_root=products_root,
            include_images=capabilities.supports_vision,
        )
        selected_prior.append(api_msg)

    selected_prior.reverse()
    current_api_msg = _format_api_message(
        role=current_msg.role,
        text=current_text,
        images=current_images,
        products_root=products_root,
        include_images=capabilities.supports_vision,
    )
    return ContextBuildResult(
        system_prompt=system_prompt,
        messages=[*selected_prior, current_api_msg],
        context_truncated=omitted > 0,
        context_omitted_count=omitted,
    )
