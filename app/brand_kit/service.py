"""Tenant-scoped storage, review and activation of immutable visual identities."""
from __future__ import annotations

import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.brand_kit.contracts import BrandStyle, contrast_ratio
from app.brand_kit.models import BrandActive, BrandAsset, BrandVersion
from app.models.domain import Product

MAX_FILE_BYTES = 6_000_000
MAX_PIXELS = 12_000_000
IMAGE_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}
ASSET_KINDS = ("original", "light", "dark")


def _asset_dir(root: Path, product_id: UUID, *, create: bool = False) -> Path:
    root = Path(root).resolve()
    library = root / "_brand_assets"
    folder = library / str(UUID(str(product_id)))
    # Never follow a swapped symlink into another tenant's filesystem.
    if library.is_symlink() or folder.is_symlink():
        raise ValueError("Unsafe asset library")
    if create:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not folder.resolve().is_relative_to(root) or folder.is_symlink():
        raise ValueError("Invalid asset location")
    return folder


def verified_asset(db, root: Path, product_id: UUID, asset_id: UUID) -> tuple[BrandAsset, Path]:
    asset = db.scalar(select(BrandAsset).where(
        BrandAsset.id == asset_id, BrandAsset.product_id == product_id))
    if asset is None:
        raise LookupError("Логотип не найден для этой компании")
    folder = _asset_dir(root, product_id)
    valid_name = str(asset.id) + IMAGE_FORMATS_REVERSE.get(asset.media_type, "")
    if asset.filename != valid_name:
        raise LookupError("Недопустимое имя файла")
    path = folder / asset.filename
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
        raise LookupError("Логотип недоступен")
    if path.stat().st_size != asset.byte_size or asset.byte_size > MAX_FILE_BYTES:
        raise LookupError("Нарушена целостность логотипа")
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
        raise LookupError("Нарушена контрольная сумма логотипа")
    return asset, path


IMAGE_FORMATS_REVERSE = {media: extension for media, extension in IMAGE_FORMATS.values()}


def store_asset(db, root: Path, product_id: UUID, kind: str, raw: bytes) -> BrandAsset:
    if kind not in ASSET_KINDS:
        raise ValueError("Неизвестный вариант логотипа")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE_BYTES:
        raise ValueError("Логотип: от 1 байта до 6 МБ")
    # Validate the actual decoded image, never trust the supplied filename or MIME.
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt = image.format
            if fmt not in IMAGE_FORMATS or image.width < 1 or image.height < 1:
                raise ValueError("Разрешены PNG, JPEG и WebP")
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ValueError("Слишком высокое разрешение")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Анимированные логотипы не поддерживаются")
            image.load()
            # Reject images whose visible content would be illegible in the final
            # 238x104 pixel logo placement (including transparent padding).
            scale = min(1.0, 238 / width, 104 / height)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                alpha = image.convert("RGBA").getchannel("A")
                visible = alpha.point(lambda a: 255 if a >= 32 else 0).getbbox()
            else:
                visible = (0, 0, width, height)
            if visible is None or (
                (visible[2] - visible[0]) * scale < 24
                or (visible[3] - visible[1]) * scale < 12
            ):
                raise ValueError("Логотип слишком мал или невидим в Stories")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Файл не является корректным изображением") from exc
    media_type, extension = IMAGE_FORMATS[fmt]
    digest = hashlib.sha256(raw).hexdigest()
    # Serialize allocations per company, including concurrent uploads on PostgreSQL.
    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    previous = db.scalar(select(BrandAsset).where(
        BrandAsset.product_id == product_id, BrandAsset.kind == kind,
        BrandAsset.sha256 == digest))
    if previous is not None:
        verified_asset(db, root, product_id, previous.id)
        return previous
    from app.brand_kit.quotas import enforce_quota
    enforce_quota(db, product_id, BrandAsset, additional_bytes=len(raw),
                  max_files=32, max_bytes=48_000_000)
    asset_id = uuid4()
    filename = str(asset_id) + extension
    folder = _asset_dir(root, product_id, create=True)
    path = folder / filename
    try:
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        record = BrandAsset(
            id=asset_id, product_id=product_id, kind=kind, filename=filename,
            media_type=media_type, sha256=digest,
            byte_size=len(raw), width=width, height=height,
        )
        db.add(record)
        db.commit()
    except IntegrityError:
        # A concurrent upload won the unique (company, variant, sha256) race.
        db.rollback()
        path.unlink(missing_ok=True)
        winner = db.scalar(select(BrandAsset).where(
            BrandAsset.product_id == product_id, BrandAsset.kind == kind,
            BrandAsset.sha256 == digest))
        if winner is None:
            raise
        verified_asset(db, root, product_id, winner.id)
        return winner
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return record


def _assert_assets(db, root: Path, product_id: UUID, style: BrandStyle) -> None:
    for field in ASSET_KINDS:
        asset_id = getattr(style.logos, field)
        if asset_id is None:
            continue
        asset, _ = verified_asset(db, root, product_id, asset_id)
        if asset.kind != field:
            raise ValueError("Вариант логотипа не совпадает с загруженным файлом")


def save_draft(db, root: Path, product_id: UUID, payload: dict) -> BrandVersion:
    style = BrandStyle.model_validate(payload)
    # Serializes concurrent version allocation on PostgreSQL.
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    _assert_assets(db, root, product_id, style)
    previous = db.scalar(select(BrandVersion).where(
        BrandVersion.product_id == product_id).order_by(
            BrandVersion.version.desc()).limit(1))
    snapshot = style.model_dump(mode="json")
    if previous is not None and previous.status == "draft" and previous.config == snapshot:
        return previous
    row = BrandVersion(
        product_id=product_id, version=(previous.version if previous else 0) + 1,
        status="draft", config=snapshot)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def approve_version(db, root: Path, product_id: UUID, version_id: UUID) -> BrandVersion:
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    version = db.scalar(select(BrandVersion).where(
        BrandVersion.id == version_id,
        BrandVersion.product_id == product_id).with_for_update())
    if version is None:
        raise LookupError("Версия фирменного стиля не найдена")
    if version.status not in ("draft", "approved"):
        raise ValueError("Эту версию нельзя утвердить")
    style = BrandStyle.model_validate(version.config)
    _assert_assets(db, root, product_id, style)
    if style.logos.original is None:
        raise ValueError("Сначала загрузите оригинальный логотип")
    if contrast_ratio(style.palette.text, style.palette.background) < 4.5:
        raise ValueError("Низкий контраст: измените цвет текста или фона")
    version.status = "approved"
    if version.approved_at is None:
        version.approved_at = datetime.now(timezone.utc)
    current = db.get(BrandActive, product_id)
    if current is None:
        db.add(BrandActive(product_id=product_id, version_id=version.id))
    else:
        current.version_id = version.id
    db.commit()
    return version


def read_state(db, product_id: UUID) -> tuple[BrandVersion | None, list[BrandVersion], BrandVersion | None]:
    versions = list(db.scalars(select(BrandVersion).where(
        BrandVersion.product_id == product_id).order_by(
            BrandVersion.version.desc()).limit(50)))
    pointer = db.get(BrandActive, product_id)
    active = next((v for v in versions if pointer is not None and v.id == pointer.version_id), None)
    if active is None and pointer is not None:
        active = db.scalar(select(BrandVersion).where(
            BrandVersion.id == pointer.version_id, BrandVersion.product_id == product_id,
            BrandVersion.status == "approved"))
    latest = versions[0] if versions else None
    return active, versions, latest


def delete_unused_logo(db, root: Path, product_id: UUID, asset_id: UUID) -> None:
    """Remove an original asset only when no version refers to its UUID."""
    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    record = db.scalar(select(BrandAsset).where(
        BrandAsset.product_id == product_id, BrandAsset.id == asset_id))
    if record is None:
        raise LookupError("Логотип компании не найден")
    if record.filename != str(record.id) + IMAGE_FORMATS_REVERSE.get(record.media_type, ""):
        raise LookupError("Недопустимое имя файла")
    path = _asset_dir(root, product_id) / record.filename
    # Unlink removes a symlink itself and never follows its target.
    if path.exists() and not path.is_file() and not path.is_symlink():
        raise LookupError("Недопустимый тип файла")
    versions = db.scalars(select(BrandVersion.config).where(
        BrandVersion.product_id == product_id))
    for configuration in versions:
        if str(asset_id) in (configuration.get("logos") or {}).values():
            raise ValueError("Логотип используется в сохранённом фирменном стиле")
    db.delete(record)
    db.commit()
    path.unlink(missing_ok=True)
