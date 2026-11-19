"""Pydantic request and response contracts for ServGo AI Chat."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, max_length=150)
    product_id: UUID | None = None
    specialist_id: Literal[
        "business_consultant", "marketer", "smm_specialist",
        "sales_manager", "copywriter", "ads_specialist",
    ] | None = None


class ConversationUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    selected_model: str | None = Field(default=None, max_length=150)


class MessageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(default="", max_length=200_000)
    mode: Literal["text", "image"] = "text"
    model_id: str = Field(min_length=1, max_length=150)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)
    quote_message_id: UUID | None = None
    quote_text: str | None = Field(default=None, max_length=12_000)
    client_key: str | None = Field(default=None, min_length=1, max_length=100)
    image_size: str | None = Field(default=None, max_length=32)


class AttachmentOut(BaseModel):
    id: UUID
    kind: str
    filename: str
    mime_type: str
    byte_size: int
    is_image: bool
    url: str
    extraction_status: str
    extraction_note: str | None = None
    width: int | None = None
    height: int | None = None


class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    seq: int
    role: str
    mode: str
    content: str
    model_id: str | None = None
    status: str
    quote_message_id: UUID | None = None
    quote_text: str | None = None
    attachments: list[AttachmentOut] = Field(default_factory=list)
    generated_images: list[dict] = Field(default_factory=list)
    context_truncated: bool = False
    context_omitted_count: int = 0
    error_message: str | None = None
    request_id: UUID | None = None
    created_at: datetime


class ConversationSummaryOut(BaseModel):
    id: UUID
    product_id: UUID | None = None
    title: str
    selected_model: str | None = None
    specialist_id: str | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(BaseModel):
    conversation: ConversationSummaryOut
    messages: list[MessageOut]
