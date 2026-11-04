"""Verified tenant-only backgrounds and immutable, idempotent Stories composition."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_models import BrandBackground, BrandComposition, BrandRender
from app.brand_kit.models import BrandActive, BrandAsset, BrandVersion
from app.brand_kit.renderer import HEIGHT, WIDTH, StoryFrame, render_story
from app.brand_kit.service import verified_asset
from app.brand_kit.quotas import enforce_quota
from app.models.domain import Product, Artifact

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
    # A stale partial upload from a crashed process must never prevent a retry.
    temporary = path.with_name(path.name + "." + uuid4().hex + ".partial")
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
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.load()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Файл фона повреждён") from exc
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    data = output.getvalue()
    if not 0 < len(data) <= MAX_BACKGROUND_STORED:
        raise ValueError("Сохранённый фон слишком большой")
    digest = hashlib.sha256(data).hexdigest()
    # Serialize quota accounting with all other writes for this tenant.
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    existing = db.scalar(select(BrandBackground).where(
        BrandBackground.product_id == product_id, BrandBackground.sha256 == digest))
    if existing is not None:
        verified_background(db, root, product_id, existing.id)
        return existing
    enforce_quota(db, product_id, BrandBackground, additional_bytes=len(data),
                  max_files=80, max_bytes=128_000_000)
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


def _restore_previous_render(db, root: Path, product_id: UUID,
                             composition: BrandComposition,
                             render: BrandRender) -> None:
    """Restore only the original checksum; never silently change a published image.

    A bad/missing background or a changed renderer is reported, not hidden.
    There are no paid model calls anywhere in this recovery path.
    """
    brand = BrandStyle.model_validate(composition.brand_snapshot)
    frame = StoryFrame.model_validate(composition.spec)
    asset_id = getattr(brand.logos, frame.logo_variant) or brand.logos.original
    if asset_id is None:
        raise LookupError("Оригинальный логотип недоступен для восстановления")
    logo, logo_file = verified_asset(db, root, product_id, asset_id)
    if logo.kind not in ("original", frame.logo_variant):
        raise LookupError("Логотип не принадлежит нужному варианту")
    background_bytes = None
    if composition.background_id is not None:
        _, file = verified_background(db, root, product_id, composition.background_id)
        background_bytes = file.read_bytes()
    candidate = render_story(brand, frame, logo=logo_file.read_bytes(),
                             background=background_bytes).png
    if (hashlib.sha256(candidate).hexdigest() != render.sha256
            or len(candidate) != render.byte_size):
        raise LookupError("Восстановленный PNG отличается от сохранённой версии")
    if render.filename != str(render.id) + ".png":
        raise LookupError("Неверный идентификатор PNG")
    destination = _folder(root, "_brand_renders", product_id, create=True) / render.filename
    # os.replace replaces a symlink itself (never opens its target).
    temporary = destination.with_name(
        destination.name + "." + uuid4().hex + ".partial")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    verified_render(db, root, product_id, render.id)


def create_composition(db, root: Path, product_id: UUID, version_id: UUID,
                       frame: StoryFrame, *, frame_key: str,
                       background_id: UUID | None = None,
                       pinned_task_id: UUID | None = None,
                       style_override: BrandStyle | None = None) -> BrandRender:
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
    # Pre-editor compositions (stages A-C) have no crop fields. Their
    # idempotency digest must remain byte-identical on default-center retries.
    if spec.get("crop_x") == 0.5:
        spec.pop("crop_x")
    if spec.get("crop_y") == 0.5:
        spec.pop("crop_y")
    if pinned_task_id is not None:
        # The public /compose route has no pinned_task_id input. Only jobs
        # whose immutable creative_request recorded this exact version/JSON
        # can render after a newer brand version was approved.
        from app.content.brand_story_pipeline import assert_task_brand
        assert_task_brand(db, product_id, pinned_task_id, version)
    if style_override is not None and pinned_task_id is None:
        raise ValueError("Редактирование стиля доступно только заданию")
    signature = json.dumps({
        **({"style_override": style_override.model_dump(mode="json")} if style_override else {}),
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
        try:
            verified_render(db, root, product_id, prior_render.id)
        except LookupError:
            _restore_previous_render(db, root, product_id, prior, prior_render)
        return prior_render
    current = db.get(BrandActive, product_id)
    if (current is None or current.version_id != version_id) and pinned_task_id is None:
        raise ValueError("Для нового кадра требуется действующая версия фирменного стиля")
    style = style_override if style_override is not None else BrandStyle.model_validate(version.config)
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
    enforce_quota(db, product_id, BrandRender, additional_bytes=len(output.png),
                  max_files=200, max_bytes=256_000_000)
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


def delete_unused_background(db, root: Path, product_id: UUID, background_id: UUID) -> None:
    """Explicit tenant deletion; existing compositions keep their referenced backgrounds."""
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    row = db.scalar(select(BrandBackground).where(
        BrandBackground.id == background_id, BrandBackground.product_id == product_id))
    if row is None:
        raise LookupError("Фон не найден")
    if db.scalar(select(BrandComposition.id).where(
            BrandComposition.product_id == product_id,
            BrandComposition.background_id == background_id).limit(1)):
        raise ValueError("Фон используется в готовых материалах")
    if row.filename != str(row.id) + ".png":
        raise LookupError("Недопустимое имя файла")
    path = _folder(root, "_brand_backgrounds", product_id) / row.filename
    if path.exists() and not path.is_file() and not path.is_symlink():
        raise LookupError("Недопустимый тип файла")
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)


def restore_story_render(db, root: Path, product_id: UUID,
                         render_id: UUID) -> Path:
    """Restore an immutable PNG from snapshot/logo/background without any AI call."""
    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    render = db.scalar(select(BrandRender).where(
        BrandRender.product_id == product_id, BrandRender.id == render_id))
    if render is None:
        raise LookupError("Готовая версия Stories не найдена")
    try:
        return verified_render(db, root, product_id, render_id)[1]
    except LookupError:
        composition = db.scalar(select(BrandComposition).where(
            BrandComposition.product_id == product_id,
            BrandComposition.id == render.composition_id))
        if composition is None:
            raise LookupError("Исходная композиция не найдена")
        _restore_previous_render(db, root, product_id, composition, render)
        return verified_render(db, root, product_id, render_id)[1]


def delete_story_render(db, root: Path, product_id: UUID, render_id: UUID) -> None:
    """Explicit removal of one composed image and its private idempotency key."""
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    row = db.scalar(select(BrandRender).where(
        BrandRender.id == render_id, BrandRender.product_id == product_id))
    if row is None:
        raise LookupError("Кадр не найден")
    if row.filename != str(row.id) + ".png":
        raise LookupError("Недопустимое имя файла")
    # Growth keeps immutable revision pointers in JSON artifacts, not a FK.
    # A deletion here otherwise destroys a finished task and its history.
    references = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "creative_story_render",
        Artifact.content.contains(str(render_id))))
    for item in references:
        try:
            if json.loads(item.content).get("render_id") == str(render_id):
                raise ValueError("Готовый кадр используется в задании Growth")
        except (TypeError, json.JSONDecodeError):
            # Reject corrupt metadata rather than deleting a possibly used PNG.
            raise ValueError("Невозможно проверить ссылки на готовый кадр") from None
    path = _folder(root, "_brand_renders", product_id) / row.filename
    if path.exists() and not path.is_file() and not path.is_symlink():
        raise LookupError("Недопустимый тип файла")
    composition = db.scalar(select(BrandComposition).where(
        BrandComposition.id == row.composition_id,
        BrandComposition.product_id == product_id))
    if composition is None:
        raise LookupError("Композиция недоступна")
    db.delete(row)
    db.flush()
    db.delete(composition)
    db.commit()
    path.unlink(missing_ok=True)
