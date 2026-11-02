"""Validated, company-scoped edits for a still-pending content creation confirmation.

Edits only change the pending request. They do not queue jobs or publish material.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dispatcher.registry import CreativeParams, PackageParams, PostParams, POST_CHANNELS
from app.content.creative import CREATIVE_PLATFORMS

FORMATS = ("post", "story", "short_video", "static_image", "package")
FORMAT_NAMES = {
    "post": "Пост", "story": "Stories", "short_video": "Видеосценарий",
    "static_image": "Бриф изображения", "package": "Полный пакет",
}
DEFAULT_PLATFORMS = {
    "post": "telegram", "story": "telegram", "short_video": "youtube_shorts",
    "static_image": "telegram", "package": None,
}
CHANNELS = {
    "post": POST_CHANNELS,
    "story": ("telegram", "vk", "instagram_reels"),
    "short_video": ("youtube_shorts", "instagram_reels", "vk_clips"),
    "static_image": ("telegram", "vk", "dzen", "instagram_reels"),
    "package": (),
}
EDITABLE_CAPABILITIES = frozenset(("create_post", "create_creative", "create_package"))


class EditCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    confirmation_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=0)
    format: Literal["post", "story", "short_video", "static_image", "package"]
    topic: str = Field(min_length=3, max_length=300)
    frame_count: int | None = Field(default=None, ge=3, le=10)
    platform: str | None = Field(default=None, max_length=40)
    audience: str | None = Field(default=None, max_length=300)
    editorial_brief: str | None = Field(default=None, max_length=3500)

    @model_validator(mode="after")
    def valid_combination(self):
        if self.format != "story" and self.frame_count is not None:
            raise ValueError("Количество экранов можно указывать только для Stories")
        if self.format == "package" and self.platform is not None:
            raise ValueError("У полного пакета площадка не задаётся")
        if self.platform is not None and self.platform not in CHANNELS[self.format]:
            raise ValueError("Эта площадка не подходит для выбранного формата")
        return self


class CreateHints(BaseModel):
    """Optional manual settings, never a command to run an action."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    format: Literal["post", "story", "short_video", "static_image", "package"] | None = None
    platform: str | None = Field(default=None, max_length=40)
    frame_count: int | None = Field(default=None, ge=3, le=10)
    audience: str | None = Field(default=None, max_length=300)
    editorial_brief: str | None = Field(default=None, max_length=3500)

    @model_validator(mode="after")
    def valid_combination(self):
        if self.format is None:
            if self.platform or self.frame_count is not None:
                raise ValueError("Площадка и количество требуют выбранного формата")
        elif self.format != "story" and self.frame_count is not None:
            raise ValueError("Количество экранов можно задавать только для Stories")
        if self.format == "package" and self.platform:
            raise ValueError("Для пакета площадка не указывается")
        if self.platform and self.platform not in CHANNELS[self.format]:
            raise ValueError("Несовместимая площадка и формат")
        return self


def draft_view(capability_id: str | None, params: dict) -> dict | None:
    """Fields the browser can edit. Stored server-side params remain authoritative."""
    if capability_id not in EDITABLE_CAPABILITIES:
        return None
    if capability_id == "create_post":
        fmt = "post"
        platform = params.get("channel", "telegram")
    elif capability_id == "create_package":
        fmt, platform = "package", None
    else:
        fmt = params.get("format")
        platform = params.get("platform") or DEFAULT_PLATFORMS.get(fmt)
    if fmt not in FORMATS:
        return None
    return {
        "format": fmt, "topic": params.get("topic", ""),
        "frame_count": params.get("frame_count") if fmt == "story" else None,
        "platform": platform, "formats": list(FORMATS),
        "audience": params.get("audience") or "",
        "editorial_brief": params.get("editorial_brief") or "",
    }


def target_for_edit(edit: EditCreation, existing: dict | None = None):
    """Produce a validated executor request, keeping context the user did not edit."""
    existing = existing or {}
    audience = edit.audience if edit.audience is not None else existing.get("audience")
    editorial_brief = (edit.editorial_brief if edit.editorial_brief is not None
                       else existing.get("editorial_brief"))
    audience = audience or None
    editorial_brief = editorial_brief or None
    if edit.format == "post":
        return "create_post", PostParams(topic=edit.topic,
            channel=edit.platform or "telegram", audience=audience,
            editorial_brief=editorial_brief)
    if edit.format == "package":
        return "create_package", PackageParams(
            topic=edit.topic, audience=audience, editorial_brief=editorial_brief)
    return "create_creative", CreativeParams(
        topic=edit.topic, format=edit.format,
        platform=edit.platform or DEFAULT_PLATFORMS[edit.format],
        frame_count=edit.frame_count if edit.format == "story" else None,
        audience=audience, editorial_brief=editorial_brief,
    )
