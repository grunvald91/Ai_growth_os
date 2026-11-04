"""Tenant-scoped Brand Kit composition API; all actions are local, no image-model calls."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import Field
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.content_studio import _owner_product
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.multipart_limits import limited_multipart_form
from app.brand_kit.contracts import Strict
from app.brand_kit.design_service import (
    MAX_BACKGROUND_UPLOAD, create_composition, store_background,
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
    if "Повторный ключ" in str(exc):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc)[:350])


@router.post("/studio/products/{product_id}/brand-kit/backgrounds")
async def upload_story_background(product_id: UUID, request: Request,
                                  db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    form = await limited_multipart_form(
        request, max_body=MAX_BACKGROUND_UPLOAD + 16_000,
        max_files=1, max_fields=2)
    source = form.get("file")
    if not isinstance(source, UploadFile):
        raise HTTPException(422, "Выберите файл PNG, JPEG или WebP")
    try:
        data = await source.read(MAX_BACKGROUND_UPLOAD + 1)
        record = await run_in_threadpool(
            store_background, db, get_settings().products_root, product_id, data)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from None
    finally:
        await source.close()
    return JSONResponse({
        "id": str(record.id), "width": record.width, "height": record.height,
        "source": "upload",
        "url": f"/studio/products/{product_id}/brand-kit/backgrounds/{record.id}",
    }, status_code=201, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/brand-kit/backgrounds/{background_id}")
def story_background(product_id: UUID, background_id: UUID,
                     db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    try:
        _, path = verified_background(db, get_settings().products_root, product_id, background_id)
    except (ValueError, LookupError):
        raise HTTPException(404, "Фон не найден") from None
    return FileResponse(path, media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/studio/products/{product_id}/brand-kit/compose")
def compose_story(product_id: UUID, payload: ComposeRequest,
                  db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    try:
        render = create_composition(
            db, get_settings().products_root, product_id, payload.version_id,
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
                            download: bool = False, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    try:
        _, path = verified_render(db, get_settings().products_root, product_id, render_id)
    except (ValueError, LookupError):
        raise HTTPException(404, "Изображение не найдено") from None
    return FileResponse(path, media_type="image/png",
        filename=f"story-{render_id}.png" if download else None,
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})
