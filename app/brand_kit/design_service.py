"""Verified tenant-only backgrounds and immutable, idempotent Stories composition."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_models import BrandBackground, BrandComposition, BrandRender
from app.brand_kit.models import BrandAsset, BrandVersion
from app.brand_kit.renderer import HEIGHT, WIDTH, StoryFrame, render_story
from app.brand_kit.service import verified_asset
from app.models.domain import Product

MAX_BACKGROUND_UPLOAD = 12_000_000
MAX_BACKGROUND_STORED = 16_000_000
MAX_RENDER_STORED = 16_000_000


def _folder(root: Path, subdir: str, product_id: UUID, *, create: bool = False) -> Path:
    root = Path(root).resolve()
    base = root / subdir
    path = base / str(UUID(str(product_id)))
    if base.is_symlink() or path.is_symlink():
        raise LookupError("Небезопасное хранилище")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.resolve().is_relative_to(root):
        raise LookupError("Выход за пределы хранилища")
    return path


def _verified_file(root: Path, subdir: str, product_id: UUID, filename: str,
                   size: int, digest: str, *, limit: int) -> Path:
    folder = _folder(root, subdir, product_id)
    path = folder / filename
    if (path.is_symlink() or path.name != filename or path.suffix != ".png"
            or not path.is_file() or not path.resolve().is_relative_to(folder.resolve())
            or not 0 < size <= limit or path.stat().st_size != size):
        raise LookupError("Сохранённое изображение недоступно")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise LookupError("Контрольная сумма изображения не совпадает")
    return path


def verified_background(db, root: Path, product_id: UUID,
                        background_id: UUID) -> tuple[BrandBackground, Path]:
    row = db.scalar(select(BrandBackground).where(
        BrandBackground.id == background_id, BrandBackground.product_id == product_id))
    if row is None:
        raise LookupError("Фон не найден")
    if row.filename != str(row.id) + ".png":
        raise LookupError("Неверное имя фона")
    return row, _verified_file(root, "_brand_backgrounds", product_id, row.filename,
                               row.byte_size, row.sha256, limit=MAX_BACKGROUND_STORED)


def verified_render(db, root: Path, product_id: UUID,
                    render_id: UUID) -> tuple[BrandRender, Path]:
    row = db.scalar(select(BrandRender).where(
        BrandRender.id == render_id, BrandRender.product_id == product_id))
    if row is None:
        raise LookupError("Изображение не найдено")
    if row.filename != str(row.id) + ".png" or (row.width, row.height) != (WIDTH, HEIGHT):
        raise LookupError("Неожиданный формат изображения")
    path = _verified_file(root, "_brand_renders", product_id, row.filename,
                          row.byte_size, row.sha256, limit=MAX_RENDER_STORED)
    with Image.open(path) as image:
        if image.format != "PNG" or image.size != (WIDTH, HEIGHT):
            raise LookupError("Некорректное сохранённое изображение")
    return row, path


def _write_once(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise LookupError("Файл с таким идентификатором уже существует")
    temporary = path.with_suffix(".partial")
    if temporary.exists() or temporary.is_symlink():
        raise LookupError("Обнаружена незавершённая запись")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def store_background(db, root: Path, product_id: UUID, raw: bytes) -> BrandBackground:
    """Save own image; normalization drops EXIF and makes dedupe reproducible."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BACKGROUND_UPLOAD:
        raise ValueError("Фон: максимально 12 МБ")
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("Фон должен быть PNG, JPEG или WebP")
            if original.width < 240 or original.height < 240:
                raise ValueError("Фон слишком маленький")
            if original.width * original.height > 12_000_000:
                raise ValueError("Фон слишком большой по разрешению")
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError("Анимированный фон запрещён")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.load()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Файл фона повреждён") from exc
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    data = output.getvalue()
    if not 0 < len(data) <= MAX_BACKGROUND_STORED:
        raise ValueError("Сохранённый фон слишком большой")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(BrandBackground).where(
        BrandBackground.product_id == product_id, BrandBackground.sha256 == digest))
    if existing is not None:
        verified_background(db, root, product_id, existing.id)
        return existing
    row = BrandBackground(
        id=uuid4(), product_id=product_id, filename="", sha256=digest,
        byte_size=len(data), width=image.width, height=image.height,
        source="upload")
    row.filename = str(row.id) + ".png"
    file = _folder(root, "_brand_backgrounds", product_id, create=True) / row.filename
    try:
        _write_once(file, data)
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        file.unlink(missing_ok=True)
        winner = db.scalar(select(BrandBackground).where(
            BrandBackground.product_id == product_id, BrandBackground.sha256 == digest))
        if winner is None:
            raise
        verified_background(db, root, product_id, winner.id)
        return winner
    except Exception:
        db.rollback()
        file.unlink(missing_ok=True)
        raise
    return row


def create_composition(db, root: Path, product_id: UUID, version_id: UUID,
                       frame: StoryFrame, *, frame_key: str,
                       background_id: UUID | None = None) -> BrandRender:
    """Pure paid-call-free composition; stable ID guards retries and mismatched payloads.

    A composition is pinned to the exact approved brand JSON. Switching active
    profile versions never changes a previously created image.
    """
    if not 4 <= len(frame_key) <= 80 or not all(
            c.isascii() and (c.isalnum() or c in "_:-.") for c in frame_key):
        raise ValueError("Неверный ключ кадра")
    # Product lock serializes concurrent composition requests for the same company.
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    version = db.scalar(select(BrandVersion).where(
        BrandVersion.product_id == product_id, BrandVersion.id == version_id,
        BrandVersion.status == "approved"))
    if version is None:
        raise LookupError("Утверждённая версия фирменного стиля не найдена")
    spec = frame.model_dump(mode="json")
    signature = json.dumps({
        "version_id": str(version_id),
        "background_id": str(background_id) if background_id else None,
        "frame": spec,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_hash = hashlib.sha256(signature).hexdigest()
    prior = db.scalar(select(BrandComposition).where(
        BrandComposition.product_id == product_id,
        BrandComposition.frame_key == frame_key))
    if prior is not None:
        if prior.request_sha256 != request_hash:
            raise ValueError("Повторный ключ использован с другими параметрами")
        prior_render = db.scalar(select(BrandRender).where(
            BrandRender.product_id == product_id,
            BrandRender.composition_id == prior.id))
        if prior_render is None:
            raise LookupError("Композиция не завершена — требуется восстановление")
        verified_render(db, root, product_id, prior_render.id)
        return prior_render
    style = BrandStyle.model_validate(version.config)
    variant_id = getattr(style.logos, frame.logo_variant) or style.logos.original
    if variant_id is None:
        raise ValueError("Фирменный логотип не выбран")
    logo_asset, logo_path = verified_asset(db, root, product_id, variant_id)
    if logo_asset.kind not in (frame.logo_variant, "original"):
        raise ValueError("Вариант логотипа не соответствует фирменному стилю")
    background = None
    if background_id is not None:
        _, image_path = verified_background(db, root, product_id, background_id)
        background = image_path.read_bytes()
    output = render_story(style, frame, logo=logo_path.read_bytes(),
                          background=background)
    if not 0 < len(output.png) <= MAX_RENDER_STORED:
        raise ValueError("Итоговое изображение превысило лимит")
    composition = BrandComposition(
        id=uuid4(), product_id=product_id, version_id=version_id,
        background_id=background_id, frame_key=frame_key,
        request_sha256=request_hash, spec=spec, brand_snapshot=style.model_dump(mode="json"))
    result = BrandRender(
        id=uuid4(), product_id=product_id, composition_id=composition.id,
        filename="", sha256=hashlib.sha256(output.png).hexdigest(),
        byte_size=len(output.png), width=WIDTH, height=HEIGHT)
    result.filename = str(result.id) + ".png"
    folder = _folder(root, "_brand_renders", product_id, create=True)
    path = folder / result.filename
    try:
        _write_once(path, output.png)
        db.add(composition)
        db.add(result)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result
