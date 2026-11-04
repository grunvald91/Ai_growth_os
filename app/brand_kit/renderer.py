"""Deterministic 9:16 Stories renderer. No AI calls or user-controlled file paths."""
from __future__ import annotations

import io
import math
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import Field, field_validator

from app.brand_kit.contracts import BrandStyle, Strict, contrast_ratio

WIDTH, HEIGHT = 900, 1600
SAFE_LEFT, SAFE_RIGHT = 72, 828
SAFE_TOP, SAFE_BOTTOM = 248, 1304
LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT = 238, 104
MAX_BACKGROUND_PIXELS = 12_000_000


class StoryFrame(Strict):
    template: Literal["hook", "explanation", "cta"]
    heading: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=360)
    cta: str = Field(default="", max_length=90)
    logo_variant: Literal["original", "light", "dark"] = "original"

    @field_validator("heading", "body", "cta")
    @classmethod
    def readable_copy(cls, value: str) -> str:
        value = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
        if any(unicodedata.category(c) in ("Cc", "Cf") and c != "\n" for c in value):
            raise ValueError("Недопустимые управляющие символы в тексте")
        if "\t" in value or value.count("\n") > 6:
            raise ValueError("Слишком много переносов в тексте")
        if any(len(line) > 130 for line in value.split("\n")):
            raise ValueError("Слишком длинная строка")
        return value.strip()


@dataclass(frozen=True)
class RenderResult:
    png: bytes
    heading_box: tuple[int, int, int, int]
    body_box: tuple[int, int, int, int] | None
    cta_box: tuple[int, int, int, int] | None
    logo_box: tuple[int, int, int, int]
    background_mode: str


def _font_candidates(family: str, bold: bool) -> tuple[str, ...]:
    suffix = "-Bold" if bold else ""
    system = "/usr/share/fonts/truetype"
    return {
        "DejaVu Sans": (f"{system}/dejavu/DejaVuSans{suffix}.ttf",),
        "Noto Sans": (f"{system}/noto/NotoSans{suffix or '-Regular'}.ttf",),
        "Roboto": (f"{system}/roboto/hinted/Roboto{suffix or '-Regular'}.ttf",
                   f"{system}/roboto/unhinted/RobotoTTF/Roboto{suffix or '-Regular'}.ttf"),
        "PT Sans": (f"{system}/pt-sans/PT_Sans-Web-{('Bold' if bold else 'Regular')}.ttf",),
        "Montserrat": (f"{system}/montserrat/Montserrat-{('Bold' if bold else 'Regular')}.ttf",),
        "Arial": (f"{system}/msttcorefonts/Arial_{('Bold' if bold else 'Regular')}.ttf",),
    }.get(family, ())


@lru_cache(maxsize=1)
def _manrope_data() -> bytes:
    """Convert repo's OFL Manrope WOFF2 into in-memory TrueType for Pillow."""
    from fontTools.ttLib import TTFont
    source = Path(__file__).resolve().parents[1] / "api" / "assets" / "manrope.woff2"
    if not source.is_file():
        raise ValueError("Manrope отсутствует на сервере")
    font = TTFont(source, lazy=False)
    try:
        font.flavor = None
        output = io.BytesIO()
        font.save(output)
        data = output.getvalue()
        if not data or len(data) > 12_000_000:
            raise ValueError("Не удалось загрузить Manrope")
        return data
    finally:
        font.close()


@lru_cache(maxsize=64)
def load_font(family: str, size: int, bold: bool) -> ImageFont.FreeTypeFont:
    if not 16 <= size <= 120:
        raise ValueError("Недопустимый размер шрифта")
    if family == "Manrope":
        try:
            font = ImageFont.truetype(io.BytesIO(_manrope_data()), size)
        except (OSError, ImportError, ValueError) as exc:
            raise ValueError("Manrope недоступен для растрового рендера") from exc
    else:
        path = next((p for p in _font_candidates(family, bold) if Path(p).is_file()), None)
        if path is None:
            raise ValueError(f"Шрифт {family} не установлен на сервере")
        font = ImageFont.truetype(path, size)
    # Do not silently replace non-Cyrillic faces: missing letters are an output defect.
    if not font.getmask("Привет, Ёж!").getbbox():
        raise ValueError("Шрифт не поддерживает кириллицу")
    return font


def validate_brand_fonts(brand: BrandStyle) -> None:
    load_font(brand.typography.heading, 56, True)
    load_font(brand.typography.body, 34, False)


def _wrap(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont,
          max_width: int) -> list[str]:
    if not text:
        return []
    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            result.append("")
            continue
        current = ""
        for word in paragraph.split():
            if draw.textlength(word, font=font) > max_width:
                raise ValueError("Слово не помещается в безопасную ширину")
            proposed = word if not current else current + " " + word
            if draw.textlength(proposed, font=font) <= max_width:
                current = proposed
            else:
                result.append(current)
                current = word
        if current:
            result.append(current)
    return result


def _fit(text: str, draw: ImageDraw.ImageDraw, family: str, *, bold: bool,
         max_width: int, max_height: int, max_size: int, min_size: int,
         max_lines: int) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(max_size, min_size - 1, -2):
        font = load_font(family, size, bold)
        try:
            lines = _wrap(text, draw, font, max_width)
        except ValueError:
            continue
        line_height = math.ceil(size * 1.28)
        if len(lines) <= max_lines and len(lines) * line_height <= max_height:
            return font, lines, line_height
    raise ValueError("Текст не помещается в безопасную область Stories")


def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16),
            int(color[5:7], 16), alpha)


def _background(brand: BrandStyle, source: bytes | None,
                image_position: str) -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), _rgba(brand.palette.background))
    if source is None:
        return canvas
    if not 0 < len(source) <= 16_000_000:
        raise ValueError("Фон превышает лимит")
    try:
        with Image.open(io.BytesIO(source)) as picture:
            if picture.width < 240 or picture.height < 240:
                raise ValueError("Фон слишком маленький")
            if picture.width * picture.height > MAX_BACKGROUND_PIXELS:
                raise ValueError("Слишком большой фон")
            if getattr(picture, "n_frames", 1) != 1:
                raise ValueError("Анимированный фон не поддерживается")
            oriented = ImageOps.exif_transpose(picture)
            rgb = oriented.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Некорректное изображение фона") from exc
    if image_position == "full":
        return ImageOps.fit(rgb, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
    # Section backgrounds cover a single half without introducing white padding.
    box = (0, 0, WIDTH, HEIGHT // 2) if image_position == "top" else (
        0, HEIGHT // 2, WIDTH, HEIGHT)
    fitted = ImageOps.fit(rgb, (WIDTH, HEIGHT // 2), method=Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, box[:2])
    return canvas


def _logo(raw: bytes) -> Image.Image:
    if not 0 < len(raw) <= 6_000_000:
        raise ValueError("Некорректный файл логотипа")
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.width * picture.height > MAX_BACKGROUND_PIXELS:
                raise ValueError("Слишком большой логотип")
            if getattr(picture, "n_frames", 1) != 1:
                raise ValueError("Анимированный логотип недопустим")
            picture.load()
            return picture.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Невозможно открыть логотип") from exc


def _logo_box(logo: Image.Image, position: str) -> tuple[int, int, int, int]:
    scale = min(1.0, LOGO_MAX_WIDTH / logo.width, LOGO_MAX_HEIGHT / logo.height)
    w, h = max(1, round(logo.width * scale)), max(1, round(logo.height * scale))
    x = SAFE_LEFT if position.endswith("left") else SAFE_RIGHT - w
    y = SAFE_TOP if position.startswith("top") else SAFE_BOTTOM - h
    return x, y, x + w, y + h


def _lines(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont,
           x0: int, y0: int, width: int, height: int, line_height: int,
           color: str, alignment: str) -> tuple[int, int, int, int]:
    end = y0 + len(lines) * line_height
    if end > y0 + height:
        raise ValueError("Текст выходит за отведённую высоту")
    for index, line in enumerate(lines):
        text_width = math.ceil(draw.textlength(line, font=font))
        x = x0 if alignment == "left" else (
            x0 + (width - text_width) // 2 if alignment == "center" else
            x0 + width - text_width)
        draw.text((x, y0 + index * line_height), line, font=font,
                  fill=color, anchor="lt", stroke_width=0)
    return x0, y0, x0 + width, end


def render_story(brand: BrandStyle, frame: StoryFrame, *,
                 logo: bytes, background: bytes | None = None) -> RenderResult:
    """Assemble a pixel-exact PNG using only approved colors and the original logo."""
    if contrast_ratio(brand.palette.text, brand.palette.background) < 4.5:
        raise ValueError("Цвет текста не читается на фирменном фоне")
    validate_brand_fonts(brand)
    layout = getattr(brand, frame.template)
    image = _background(brand, background, layout.image_position)
    emblem = _logo(logo)
    x1, y1, x2, y2 = _logo_box(emblem, layout.logo_position)
    emblem = emblem.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # The logo has a contained contrast backing; it is never regenerated by AI.
    draw.rounded_rectangle(
        (x1 - 16, y1 - 14, x2 + 16, y2 + 14), radius=17,
        fill=_rgba(brand.palette.background, 243))
    panel_top = 452 if layout.logo_position.startswith("top") else 282
    panel_bottom = 1164 if layout.logo_position.startswith("bottom") else 1282
    panel_x, panel_width = SAFE_LEFT, SAFE_RIGHT - SAFE_LEFT
    pad_x, pad_y = 34, 37
    text_width = panel_width - 2 * pad_x
    max_block_height = panel_bottom - panel_top - 2 * pad_y
    tdraw = ImageDraw.Draw(image)
    title_font, title_lines, title_step = _fit(
        frame.heading, tdraw, brand.typography.heading, bold=True,
        max_width=text_width, max_height=max_block_height, max_size=78,
        min_size=38, max_lines=6)
    body_font, body_lines, body_step = (
        _fit(frame.body, tdraw, brand.typography.body, bold=False,
             max_width=text_width, max_height=295, max_size=39,
             min_size=24, max_lines=7)
        if frame.body else (None, [], 0))
    cta_font, cta_lines, cta_step = (
        _fit(frame.cta, tdraw, brand.typography.heading, bold=True,
             max_width=text_width - 50, max_height=100, max_size=35,
             min_size=22, max_lines=2)
        if frame.cta else (None, [], 0))
    if frame.template == "cta" and not frame.cta:
        raise ValueError("Завершающий кадр требует призыва")
    gap_a = 24 if body_lines else 0
    gap_b = 27 if cta_lines else 0
    title_h, body_h = len(title_lines) * title_step, len(body_lines) * body_step
    cta_h = len(cta_lines) * cta_step
    block_height = title_h + gap_a + body_h + gap_b + cta_h
    if block_height > max_block_height:
        raise ValueError("Текст не помещается в безопасную область Stories")
    # Distinct composition rhythms without compromising the safe regions.
    block_y = panel_top + pad_y + (
        max(0, (max_block_height - block_height) // 2)
        if frame.template == "hook" else
        max(0, (max_block_height - block_height) // 3)
        if frame.template == "explanation" else
        max(0, max_block_height - block_height - 12))
    panel_actual_top = max(panel_top, block_y - pad_y)
    panel_actual_bottom = min(panel_bottom, block_y + block_height + pad_y)
    draw.rounded_rectangle(
        (panel_x, panel_actual_top, SAFE_RIGHT, panel_actual_bottom),
        radius=26, fill=_rgba(brand.palette.background, 244))
    image = Image.alpha_composite(image, overlay)
    image.alpha_composite(emblem, (x1, y1))
    rendered = ImageDraw.Draw(image)
    x_text = panel_x + pad_x
    heading_box = _lines(rendered, title_lines, title_font,
                         x_text, block_y, text_width, title_h,
                         title_step, brand.palette.text, layout.text_align)
    body_box = None
    cta_box = None
    current_y = heading_box[3]
    if body_lines:
        current_y += gap_a
        body_box = _lines(rendered, body_lines, body_font,
                          x_text, current_y, text_width, body_h,
                          body_step, brand.palette.text, layout.text_align)
        current_y = body_box[3]
    if cta_lines:
        current_y += gap_b
        # Accent border guarantees branded CTA while retaining approved contrast.
        pill_left = x_text
        pill_right = x_text + text_width
        rendered.rounded_rectangle(
            (pill_left - 10, current_y - 10, pill_right + 10,
             current_y + cta_h + 10), radius=18,
            outline=brand.palette.accent, width=5)
        cta_box = _lines(rendered, cta_lines, cta_font,
                         x_text, current_y, text_width, cta_h,
                         cta_step, brand.palette.text, layout.text_align)
    for box in (heading_box, body_box, cta_box, (x1, y1, x2, y2)):
        if box is not None and not (
            SAFE_LEFT <= box[0] <= box[2] <= SAFE_RIGHT and
            SAFE_TOP <= box[1] <= box[3] <= SAFE_BOTTOM):
            raise ValueError("Композиция выходит за безопасные поля")
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return RenderResult(
        png=output.getvalue(), heading_box=heading_box,
        body_box=body_box, cta_box=cta_box,
        logo_box=(x1, y1, x2, y2),
        background_mode=layout.image_position)
