"""Generate Open Graph cards (1200x630 PNG) for promo.servgo.ru niches.

Writes images to webapp/static/niche/<slug>/og.png and webapp/static/og.png
(root). Re-run any time meta data or palette changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STATIC_NICHE = ROOT / "webapp" / "static" / "niche"
ROOT_OG_PATH = ROOT / "webapp" / "static" / "og.png"

W, H = 1200, 630

# Brand palette (mint, peach, sky, lemon, coral, violet).
PALETTE = {
    "mint":   "#a8e6cf",
    "peach":  "#ffd3b6",
    "sky":    "#a8d8ea",
    "lemon":  "#fff3b0",
    "coral":  "#ff8a80",
    "violet": "#b39ddb",
}

# Per-niche OG content. slug -> (label, sub, color1, color2, text_dark)
NICHES = {
    "_root":       ("ServGo",                     "Онлайн запись и бронирование для бизнеса", "mint",  "sky",    True),
    "beauty":      ("Салоны красоты",             "Запись маникюра, стрижек и процедур",      "peach", "lemon",  True),
    "psy":         ("Психологи и коучи",          "Запись и предоплата без переписки",        "sky",   "mint",   True),
    "shinomontazh":("Шиномонтаж",                 "Онлайн-запись без сезонных очередей",      "coral", "peach",  False),
    "banya":       ("Бани и сауны",               "Онлайн-бронь сеанса 24/7",                 "lemon", "peach",  True),
    "hall-rental": ("Аренда залов и фотостудий",  "Один календарь, депозит онлайн",           "sky",   "violet", True),
    "barbershop":  ("Барбершопы",                 "Запись на бренд, не на барбера",           "violet","coral",  False),
}

FONT_REG = "C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def make_gradient(c1_hex: str, c2_hex: str) -> Image.Image:
    """Diagonal gradient from top-left (c1) to bottom-right (c2)."""
    c1 = hex_to_rgb(c1_hex)
    c2 = hex_to_rgb(c2_hex)
    img = Image.new("RGB", (W, H), c1)
    px = img.load()
    diag = W + H
    for y in range(H):
        for x in range(W):
            t = (x + y) / diag
            px[x, y] = (
                int(c1[0] + (c2[0] - c1[0]) * t),
                int(c1[1] + (c2[1] - c1[1]) * t),
                int(c1[2] + (c2[2] - c1[2]) * t),
            )
    return img


def fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: str, max_size: int, max_width: int) -> ImageFont.FreeTypeFont:
    """Pick the largest font size <= max_size that keeps text within max_width on one line."""
    for size in range(max_size, 24, -2):
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
    return ImageFont.truetype(font_path, 28)


def draw_card(slug: str, label: str, sub: str, color1: str, color2: str, dark_text: bool, out: Path) -> None:
    img = make_gradient(PALETTE[color1], PALETTE[color2])
    draw = ImageDraw.Draw(img, "RGBA")

    fg = (24, 28, 36) if dark_text else (255, 255, 255)
    fg_soft = (24, 28, 36, 200) if dark_text else (255, 255, 255, 220)
    chip_bg = (255, 255, 255, 220) if not dark_text else (24, 28, 36, 50)
    chip_fg = (24, 28, 36) if not dark_text else (24, 28, 36)

    # ServGo brand chip top-left.
    brand_font = ImageFont.truetype(FONT_BOLD, 36)
    mark_font = ImageFont.truetype(FONT_BOLD, 28)
    pad_x, pad_y = 56, 56

    # Round-ish "SG" mark.
    mark_size = 64
    draw.rounded_rectangle(
        (pad_x, pad_y, pad_x + mark_size, pad_y + mark_size),
        radius=14,
        fill=(24, 28, 36) if dark_text else (255, 255, 255),
    )
    mark_text = "SG"
    mb = draw.textbbox((0, 0), mark_text, font=mark_font)
    mx = pad_x + (mark_size - (mb[2] - mb[0])) / 2
    my = pad_y + (mark_size - (mb[3] - mb[1])) / 2 - 4
    draw.text((mx, my), mark_text, font=mark_font, fill=(255, 255, 255) if dark_text else (24, 28, 36))

    brand_x = pad_x + mark_size + 18
    brand_y = pad_y + (mark_size - 44) / 2
    draw.text((brand_x, brand_y), "ServGo", font=brand_font, fill=fg)

    # Headline (niche label).
    max_w = W - 2 * pad_x
    headline_font = fit_text(draw, label, FONT_BOLD, 110, max_w)
    hb = draw.textbbox((0, 0), label, font=headline_font)
    headline_h = hb[3] - hb[1]

    # Subline.
    sub_font = fit_text(draw, sub, FONT_REG, 44, max_w)
    sb = draw.textbbox((0, 0), sub, font=sub_font)
    sub_h = sb[3] - sb[1]

    gap = 24
    total_h = headline_h + gap + sub_h
    block_y = (H - total_h) / 2 + 30

    draw.text((pad_x, block_y), label, font=headline_font, fill=fg)
    draw.text((pad_x, block_y + headline_h + gap), sub, font=sub_font, fill=fg_soft if isinstance(fg_soft, tuple) and len(fg_soft) == 4 else fg)

    # Bottom URL chip.
    url = "promo.servgo.ru" if slug == "_root" else f"promo.servgo.ru/{slug}"
    url_font = ImageFont.truetype(FONT_BOLD, 28)
    ub = draw.textbbox((0, 0), url, font=url_font)
    url_w = ub[2] - ub[0]
    url_h = ub[3] - ub[1]
    chip_pad_x, chip_pad_y = 22, 14
    chip_w = url_w + 2 * chip_pad_x
    chip_h = url_h + 2 * chip_pad_y + 6
    chip_x = pad_x
    chip_y = H - pad_y - chip_h
    draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=chip_h // 2, fill=chip_bg)
    draw.text((chip_x + chip_pad_x, chip_y + chip_pad_y - 4), url, font=url_font, fill=chip_fg)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)


def main() -> None:
    for slug, (label, sub, c1, c2, dark) in NICHES.items():
        if slug == "_root":
            out = ROOT_OG_PATH
        else:
            out = STATIC_NICHE / slug / "og.png"
        draw_card(slug, label, sub, c1, c2, dark, out)
        print(f"  {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
