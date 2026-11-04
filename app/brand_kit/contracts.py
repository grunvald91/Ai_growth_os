"""Strict, serializable Brand Kit contract; no external URLs or arbitrary font CSS."""
from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


FONTS = ("Manrope", "DejaVu Sans")  # Guaranteed: bundled Manrope and Docker DejaVu.
FONT_CSS = {name: "'" + name + "',Arial,sans-serif" for name in FONTS}
ACCENTS = ("minimal", "photo", "illustration", "decorative")
GENERATORS = ("gemini", "openai", "own")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Palette(Strict):
    primary: str = "#2F5BEB"
    secondary: str = "#0D1B2A"
    accent: str = "#2F5BEB"
    background: str = "#FFFFFF"
    text: str = "#0D1B2A"

    @field_validator("primary", "secondary", "accent", "background", "text")
    @classmethod
    def valid_hex(cls, value: str) -> str:
        if not HEX_COLOR.fullmatch(value):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return value.upper()


class Typography(Strict):
    heading: Literal["Manrope", "DejaVu Sans"] = "Manrope"
    body: Literal["Manrope", "DejaVu Sans"] = "Manrope"


class Logos(Strict):
    original: UUID | None = None
    light: UUID | None = None
    dark: UUID | None = None


class FrameLayout(Strict):
    logo_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-left"
    image_position: Literal["full", "top", "bottom"] = "full"
    text_align: Literal["left", "center", "right"] = "left"


class BrandStyle(Strict):
    schema_version: Literal[1] = 1
    palette: Palette = Field(default_factory=Palette)
    typography: Typography = Field(default_factory=Typography)
    visual_character: Literal["minimal", "photo", "illustration", "decorative"] = "photo"
    preferred_generator: Literal["gemini", "openai", "own"] = "gemini"
    logos: Logos = Field(default_factory=Logos)
    hook: FrameLayout = Field(default_factory=FrameLayout)
    explanation: FrameLayout = Field(default_factory=FrameLayout)
    cta: FrameLayout = Field(default_factory=FrameLayout)


def contrast_ratio(text: str, background: str) -> float:
    def lum(hex_color: str) -> float:
        components = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in components]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    a, b = lum(text), lum(background)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)
