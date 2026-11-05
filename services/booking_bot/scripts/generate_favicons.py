"""Generate raster favicons from the ServGo calendar+pen brand mark.

Source of truth is webapp/static/favicon.svg. This script reproduces the same
shapes in Pillow so PNG variants (apple-touch, favicon-16/32/48, icon-192/512,
favicon.ico) match the SVG without depending on cairosvg.

Re-run any time the brand mark changes:
    python scripts/generate_favicons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"

# Original viewBox is 48x48; we render at 4x for AA then downscale.
VB = 48

NAVY = (13, 27, 42)        # #0D1B2A
WHITE = (255, 255, 255)
BLUE = (47, 91, 235)       # #2F5BEB

# (16, 32, 48) for .ico; the rest are exported as standalone PNGs.
RASTER_TARGETS = {
    "favicon-16x16.png": 16,
    "favicon-32x32.png": 32,
    "favicon-48x48.png": 48,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
}
ICO_PATH = STATIC / "favicon.ico"
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64)]


def _scale_xywh(x: float, y: float, w: float, h: float, k: float) -> tuple[float, float, float, float]:
    return x * k, y * k, (x + w) * k, (y + h) * k


def draw_mark(size: int) -> Image.Image:
    """Render the calendar+pen mark at `size`x`size`. Uses 4x supersampling."""
    ss = 4
    k = (size * ss) / VB
    canvas = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas, "RGBA")

    # Calendar body.
    d.rounded_rectangle(_scale_xywh(5, 8, 34, 33, k), radius=8 * k, fill=NAVY)
    # Inner white area (where the date cells live).
    d.rounded_rectangle(_scale_xywh(9.5, 18, 25, 18.5, k), radius=4 * k, fill=WHITE)
    # Left and right binding tabs above the body.
    d.rounded_rectangle(_scale_xywh(13, 4, 4.5, 9, k), radius=2.25 * k, fill=NAVY)
    d.rounded_rectangle(_scale_xywh(26.5, 4, 4.5, 9, k), radius=2.25 * k, fill=NAVY)
    # Five date cells, semi-transparent blue (matches SVG opacity 0.5 over white).
    cell_fill = (BLUE[0], BLUE[1], BLUE[2], 128)
    for cx, cy in [(13, 22), (20, 22), (27, 22), (13, 29), (20, 29)]:
        d.rounded_rectangle(_scale_xywh(cx, cy, 4, 4, k), radius=1.2 * k, fill=cell_fill)
    # Pen — closed polygon with white outline.
    pen_pts = [(40.5, 19.5), (44, 23), (31.5, 35.5), (27.5, 36.5), (28.5, 32.5)]
    scaled = [(x * k, y * k) for x, y in pen_pts]
    d.polygon(scaled, fill=BLUE, outline=WHITE)
    # Beef up outline to match the SVG stroke-width=1.6 with round joins.
    d.line(scaled + [scaled[0]], fill=WHITE, width=max(1, int(1.6 * k)), joint="curve")

    if ss > 1:
        canvas = canvas.resize((size, size), Image.LANCZOS)
    return canvas


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    for name, size in RASTER_TARGETS.items():
        img = draw_mark(size)
        out = STATIC / name
        img.save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(ROOT)} ({size}x{size})")

    # Multi-size ICO containing 16, 32, 48, 64 — Pillow takes the largest and
    # generates the rest. We feed the 64px so smaller sizes downscale cleanly.
    biggest = draw_mark(64)
    biggest.save(ICO_PATH, format="ICO", sizes=ICO_SIZES)
    print(f"  {ICO_PATH.relative_to(ROOT)} (multi-size: 16/32/48/64)")


if __name__ == "__main__":
    main()
