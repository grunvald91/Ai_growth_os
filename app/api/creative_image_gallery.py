"""Compact owner-facing image gallery for creative task and material pages."""
from __future__ import annotations

import html
import json
from uuid import UUID

from app.models.domain import Artifact


def gallery_html(product_id: UUID, task_id: UUID, images: list[Artifact], *,
                 errors: list[Artifact] | None = None,
                 invalid_frames: list[int] | None = None) -> str:
    if not images and not errors and not invalid_frames:
        return ""
    base = f"/studio/products/{product_id}/tasks/{task_id}"
    tiles = []
    succeeded = set()
    for record in images:
        try:
            meta = json.loads(record.content or "{}")
            number = int(meta["frame_number"])
            model = str(meta["model"])
        except (ValueError, KeyError, TypeError):
            continue
        if not 1 <= number <= 10:
            continue
        succeeded.add(number)
        uri = f"{base}/images/{record.id}"
        edit = (
            f'<a class="btn secondary" href="{base}/stories/{record.id}/edit">Изменить бесплатно</a>'
            if record.kind == "creative_story_render" else ""
        )
        if model.startswith("brand:"):
            generator = ("ChatGPT Image" if "openai" in model else
                         "Gemini" if "gemini" in model else "без платной модели")
            label = "Фирменный стиль · " + generator
        else:
            label = "ChatGPT Image" if model.startswith("gpt-") else "Gemini"
        tiles.append(
            '<article class="sg-image-tile">'
            f'<a class="{"is-square" if meta.get("ratio") == "1:1" else "is-portrait"}" href="{uri}" target="_blank" rel="noopener" aria-label="Открыть кадр {number}">'
            f'<img src="{uri}" loading="lazy" decoding="async" alt="Готовый кадр {number}" /></a>'
            '<div class="sg-image-tile-foot"><div>'
            f'<b>Кадр {number}</b><small>{html.escape(label)}</small>'
            '</div>'
            f'<a class="btn secondary" href="{uri}?download=true" download="story-{number:02d}.png">Скачать PNG</a>'
            + edit + '</div></article>'
        )
    failed = []
    for record in errors or []:
        try:
            meta = json.loads(record.content or "{}")
            failed.append(int(meta["frame_number"]))
        except (ValueError, KeyError, TypeError):
            continue
    # Do not report a recovered frame as missing after a successful retry.
    failed = [number for number in failed if number not in succeeded]
    failed.extend(n for n in (invalid_frames or []) if n not in succeeded)
    if not tiles and not failed:
        return ""
    status = (
        f'<p class="sg-image-partial" role="status">Кадры {", ".join(str(x) for x in sorted(set(failed)))} '
        'не созданы или повреждены. Остальные изображения доступны; проверьте задание.</p>'
        if failed else ""
    )
    download = (
        f'<a class="btn primary" href="{base}/images.zip" download="images.zip">'
        f'Скачать все · {len(tiles)} PNG (ZIP)</a>'
        if tiles else "")
    return (
        '<section class="sg-creative-images" aria-label="Сгенерированные изображения">'
        '<div class="sg-image-gallery-head"><div><h3>Готовые изображения</h3>'
        f'<p>{len(tiles)} файлов · отдельное скачивание или весь комплект</p></div>'
        f'{download}</div>'
        + f'<div class="sg-image-gallery">{ "".join(tiles) }</div>'
        + status + '</section>'
    )
