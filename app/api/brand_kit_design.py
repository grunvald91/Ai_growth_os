"""Tenant-scoped Brand Kit composition API; all actions are local, no image-model calls."""
from __future__ import annotations

import html
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.content_studio import _owner_product, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.multipart_limits import IMAGE_UPLOAD_SLOTS, limited_multipart_form
from app.brand_kit.contracts import Strict
from app.brand_kit.design_models import BrandBackground, BrandComposition, BrandRender
from app.brand_kit.models import BrandAsset
from app.brand_kit.design_service import (
    MAX_BACKGROUND_UPLOAD, create_composition, delete_story_render, delete_unused_background, store_background,
    verified_background, verified_render,
)
from app.brand_kit.renderer import StoryFrame
from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])


class ComposeRequest(Strict):
    version_id: UUID
    frame_key: str = Field(min_length=4, max_length=80)
    frame: StoryFrame
    background_id: UUID | None = None


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if "Повторный ключ" in str(exc) or "действующая версия" in str(exc) or "используется в готовых" in str(exc):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc)[:350])


@router.post("/studio/products/{product_id}/brand-kit/backgrounds")
async def upload_story_background(product_id: UUID, request: Request,
                                  db: Session = Depends(get_db),
                                  settings = Depends(get_settings)):
    _owner_product(db, product_id)
    async with IMAGE_UPLOAD_SLOTS:
        form = await limited_multipart_form(
            request, max_body=MAX_BACKGROUND_UPLOAD + 16_000,
            max_files=1, max_fields=2)
        source = form.get("file")
        if not isinstance(source, UploadFile):
            await form.close()
            raise HTTPException(422, "Выберите файл PNG, JPEG или WebP")
        try:
            data = await source.read(MAX_BACKGROUND_UPLOAD + 1)
            record = await run_in_threadpool(
                store_background, db, settings.products_root, product_id, data)
        except (LookupError, ValueError) as exc:
            raise _error(exc) from None
        finally:
            await form.close()
    return JSONResponse({
        "id": str(record.id), "width": record.width, "height": record.height,
        "source": "upload",
        "url": f"/studio/products/{product_id}/brand-kit/backgrounds/{record.id}",
    }, status_code=201, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/brand-kit/backgrounds/{background_id}")
def story_background(product_id: UUID, background_id: UUID,
                     db: Session = Depends(get_db),
                     settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        _, path = verified_background(db, settings.products_root, product_id, background_id)
    except (ValueError, LookupError):
        raise HTTPException(404, "Фон не найден") from None
    return FileResponse(path, media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/studio/products/{product_id}/brand-kit/compose")
def compose_story(product_id: UUID, payload: ComposeRequest,
                  db: Session = Depends(get_db),
                  settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        render = create_composition(
            db, settings.products_root, product_id, payload.version_id,
            payload.frame, frame_key=payload.frame_key,
            background_id=payload.background_id)
    except (ValueError, LookupError) as exc:
        raise _error(exc) from None
    base = f"/studio/products/{product_id}/brand-kit/renders/{render.id}"
    return JSONResponse({
        "id": str(render.id), "composition_id": str(render.composition_id),
        "width": render.width, "height": render.height,
        "sha256": render.sha256, "url": base, "download_url": base + "?download=true",
    }, status_code=201, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/brand-kit/renders/{render_id}")
def download_composed_story(product_id: UUID, render_id: UUID,
                            download: bool = False, db: Session = Depends(get_db),
                            settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        _, path = verified_render(db, settings.products_root, product_id, render_id)
    except (ValueError, LookupError):
        raise HTTPException(404, "Изображение не найдено") from None
    return FileResponse(path, media_type="image/png",
        filename=f"story-{render_id}.png" if download else None,
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.delete("/studio/products/{product_id}/brand-kit/backgrounds/{background_id}")
def remove_story_background(product_id: UUID, background_id: UUID,
                            db: Session = Depends(get_db),
                            settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        delete_unused_background(db, settings.products_root, product_id, background_id)
    except (ValueError, LookupError) as exc:
        raise _error(exc) from None
    return JSONResponse({"deleted": True}, headers={"Cache-Control": "no-store"})


@router.delete("/studio/products/{product_id}/brand-kit/renders/{render_id}")
def remove_story_render(product_id: UUID, render_id: UUID,
                        db: Session = Depends(get_db),
                        settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        delete_story_render(db, settings.products_root, product_id, render_id)
    except (ValueError, LookupError) as exc:
        raise _error(exc) from None
    return JSONResponse({"deleted": True}, headers={"Cache-Control": "no-store"})


def _storage_total(db: Session, product_id: UUID, model) -> tuple[int, int]:
    return db.execute(select(func.count(), func.coalesce(func.sum(model.byte_size), 0)).where(
        model.product_id == product_id)).one()


@router.get("/studio/products/{product_id}/brand-kit/library",
            response_class=HTMLResponse)
def brand_asset_library(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    root = f"/studio/products/{product_id}/brand-kit"
    quotas = (
        (BrandAsset, "Логотипы", 32, 48_000_000),
        (BrandBackground, "Фоны", 80, 128_000_000),
        (BrandRender, "Готовые кадры", 200, 256_000_000),
    )
    summaries = []
    for model, name, count_limit, bytes_limit in quotas:
        used_count, used_bytes = _storage_total(db, product_id, model)
        summaries.append(
            f'<div class="sg-surface"><b>{html.escape(name)}</b>'
            f'<p>{used_count} из {count_limit} файлов · '
            f'{used_bytes / 1_000_000:.1f} из {bytes_limit / 1_000_000:.0f} МБ</p></div>')
    backgrounds = list(db.scalars(select(BrandBackground).where(
        BrandBackground.product_id == product_id).order_by(
        BrandBackground.created_at.desc(), BrandBackground.id.desc()).limit(50)))
    renders = list(db.execute(select(BrandRender, BrandComposition).join(
        BrandComposition, BrandComposition.id == BrandRender.composition_id).where(
        BrandRender.product_id == product_id,
        BrandComposition.product_id == product_id).order_by(
        BrandRender.created_at.desc(), BrandRender.id.desc()).limit(50)))

    bg_items = []
    for item in backgrounds:
        in_use = db.scalar(select(BrandComposition.id).where(
            BrandComposition.product_id == product_id,
            BrandComposition.background_id == item.id).limit(1)) is not None
        action = ('<small>Используется в готовом кадре</small>' if in_use else
                  f'<form method="post" action="{root}/library/backgrounds/{item.id}/delete" '
                  'onsubmit="return confirm(\'Удалить этот неиспользуемый фон?\')">'
                  '<button class="btn danger" type="submit">Удалить фон</button></form>')
        bg_items.append(
            f'<div class="sg-surface"><a href="{root}/backgrounds/{item.id}" target="_blank">'
            f'<img src="{root}/backgrounds/{item.id}" width="96" height="75" '
            'loading="lazy" style="object-fit:contain" alt="Фон компании"></a>'
            f'<p>{item.byte_size / 1_000_000:.2f} МБ</p>{action}</div>')
    render_items = []
    for item, composition in renders:
        render_items.append(
            f'<div class="sg-surface"><a href="{root}/renders/{item.id}" target="_blank">'
            f'<img src="{root}/renders/{item.id}" width="80" height="144" '
            'loading="lazy" style="object-fit:contain" alt="Готовый кадр"></a>'
            f'<p>{html.escape(composition.frame_key)} · {item.byte_size / 1_000_000:.2f} МБ</p>'
            f'<form method="post" action="{root}/library/renders/{item.id}/delete" '
            'onsubmit="return confirm(\'Удалить кадр и его историю повторной сборки?\')">'
            '<button class="btn danger" type="submit">Удалить кадр</button></form></div>')
    body = (
        f'<p><a class="btn secondary" href="{root}">← Настройки стиля</a></p>'
        '<p class="sg-muted">Место ограничено отдельно по логотипам, фонам и кадрам. '
        'Удаление необратимо. Фоны, используемые в готовых кадрах, защищены.</p>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px">'
        + "".join(summaries) + '</div>'
        '<h2>Фоны</h2><div style="display:flex;flex-wrap:wrap;gap:10px">'
        + ("".join(bg_items) or '<p>Фоны не загружены.</p>') + '</div>'
        '<h2>Готовые кадры</h2><div style="display:flex;flex-wrap:wrap;gap:10px">'
        + ("".join(render_items) or '<p>Готовых кадров нет.</p>') + '</div>'
    )
    return HTMLResponse(studio_page_shell(
        product, active="profile", title="Хранилище фирменного стиля",
        subtitle="Материалы компании и освобождение места.", body=body),
        headers={"Cache-Control": "private, no-store"})


@router.post("/studio/products/{product_id}/brand-kit/library/backgrounds/{background_id}/delete")
def remove_background_from_library(product_id: UUID, background_id: UUID,
                                   db: Session = Depends(get_db),
                                   settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        delete_unused_background(db, settings.products_root, product_id, background_id)
    except (ValueError, LookupError) as exc:
        raise _error(exc) from None
    return RedirectResponse(
        f"/studio/products/{product_id}/brand-kit/library", status_code=303)


@router.post("/studio/products/{product_id}/brand-kit/library/renders/{render_id}/delete")
def remove_render_from_library(product_id: UUID, render_id: UUID,
                               db: Session = Depends(get_db),
                               settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        delete_story_render(db, settings.products_root, product_id, render_id)
    except (ValueError, LookupError) as exc:
        raise _error(exc) from None
    return RedirectResponse(
        f"/studio/products/{product_id}/brand-kit/library", status_code=303)
