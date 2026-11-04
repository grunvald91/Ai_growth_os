"""Pinned company Brand Kit for Growth Stories; no image-model calls in this module.

Generated images remain immutable paid background checkpoints. Original logos and
Cyrillic copy are composed locally into separate, verified final PNG revisions.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_models import BrandRender
from app.brand_kit.design_service import create_composition, store_background, verified_render
from app.brand_kit.models import BrandActive, BrandVersion
from app.brand_kit.renderer import StoryFrame
from app.content.creative_images import generated_images, verified_file
from app.models.domain import Artifact
from app.services.artifacts import ArtifactService

LOG = logging.getLogger(__name__)


def brand_digest(config: dict) -> str:
    return hashlib.sha256(json.dumps(
        config, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def brand_story_canary_allowed(settings, product_id: UUID) -> bool:
    """Strict company UUID allowlist. Empty/malformed configuration fails closed."""
    if not settings.brand_stories_enabled:
        return False
    entries = [part.strip() for part in settings.brand_story_canary_product_ids.split(",")]
    try:
        return str(product_id) in {str(UUID(part)) for part in entries if part}
    except ValueError:
        return False


def pin_brand_request(db, request, *, settings):
    """Choose approved style once; never enable an unapproved company."""
    if not brand_story_canary_allowed(settings, request.product_id):
        if request.brand_mode == "brand":
            raise ValueError("Фирменные Stories пока не включены для этой компании")
        return request.model_copy(update={"brand_mode": "classic"})
    pointer = db.get(BrandActive, request.product_id)
    version = (db.scalar(select(BrandVersion).where(
        BrandVersion.product_id == request.product_id,
        BrandVersion.id == pointer.version_id, BrandVersion.status == "approved"))
        if pointer is not None else None)
    if version is None:
        if request.brand_mode == "brand":
            raise ValueError("Сначала утвердите фирменный стиль компании")
        return request.model_copy(update={"brand_mode": "classic"})
    brand = BrandStyle.model_validate(version.config)
    model = request.image_model
    if model == "auto":
        model = brand.preferred_generator
        if model == "own":
            model = "none"
    return request.model_copy(update={
        "brand_mode": "brand", "brand_version_id":version.id,
        "brand_snapshot_sha256":brand_digest(version.config),
        "image_model":model,
    })


def assert_task_brand(db, product_id: UUID, task_id: UUID, version: BrandVersion) -> None:
    saved = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_request").order_by(
        Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    if saved is None:
        raise ValueError("Нет подтверждённого задания для этой версии стиля")
    try:
        command = json.loads(saved.content)
    except (ValueError, TypeError):
        raise ValueError("Недостоверный сохранённый запрос") from None
    if (command.get("brand_mode") != "brand"
            or command.get("brand_version_id") != str(version.id)
            or command.get("brand_snapshot_sha256") != brand_digest(version.config)):
        raise ValueError("Задание не закреплено за этой версией фирменного стиля")


def validate_pinned_request(db, request, task_id: UUID) -> BrandVersion:
    if request.brand_version_id is None or not request.brand_snapshot_sha256:
        raise ValueError("Фирменный стиль задания не закреплён")
    version = db.scalar(select(BrandVersion).where(
        BrandVersion.id == request.brand_version_id,
        BrandVersion.product_id == request.product_id, BrandVersion.status == "approved"))
    if version is None:
        raise ValueError("Версия фирменного стиля задания отсутствует")
    assert_task_brand(db, request.product_id, task_id, version)
    if brand_digest(version.config) != request.brand_snapshot_sha256:
        raise ValueError("Сохранённый фирменный стиль был изменён")
    return version


def validate_brand_story_copy(draft) -> None:
    """Reject unrenderable LLM copy before any image provider is charged."""
    if len(draft.cta) > 65:
        raise ValueError("Фирменные Stories: призыв длиннее 65 символов")
    for frame in draft.frames:
        if len(frame.on_screen_text) > 110 or frame.on_screen_text.count("\n") > 2:
            raise ValueError("Фирменные Stories: текст кадра превышает лимит")
        if not frame.on_screen_text.strip():
            raise ValueError("Фирменные Stories: пустой текст кадра")


def story_frame(draft, source, *, count: int) -> StoryFrame:
    template = "hook" if source.number == 1 else (
        "cta" if source.number == count else "explanation")
    # Do not shorten or rewrite Russian copy. Validation failures are visible
    # and retryable after a deliberate edit, rather than silently altering it.
    return StoryFrame.model_validate({
        "template": template,
        "heading": source.on_screen_text,
        "body": "",
        "cta": draft.cta if template == "cta" else "",
    })


def preflight_brand_story_layout(db, root: Path, request, draft, task_id: UUID) -> None:
    """Use real approved fonts/logo/layout, not only character counts, pre-billing."""
    from app.brand_kit.renderer import render_story
    from app.brand_kit.service import verified_asset

    validate_brand_story_copy(draft)
    version = validate_pinned_request(db, request, task_id)
    style = BrandStyle.model_validate(version.config)
    for source in draft.frames:
        frame = story_frame(draft, source, count=len(draft.frames))
        variant = getattr(style.logos, frame.logo_variant) or style.logos.original
        if variant is None:
            raise ValueError("Фирменный стиль не содержит логотип")
        _, logo_path = verified_asset(db, root, request.product_id, variant)
        # Rendering with a solid brand background validates exact text size,
        # glyph support, contrast and safe bounds without calling the image AI.
        render_story(style, frame, logo=logo_path.read_bytes())


def composed_images(db, product_id: UUID, task_id: UUID) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_story_render").order_by(
        Artifact.created_at, Artifact.id)))


def visible_images(db, product_id: UUID, task_id: UUID) -> list[Artifact]:
    request = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_request").order_by(
        Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    if request is None:
        return generated_images(db, product_id, task_id)
    try:
        pinned = json.loads(request.content).get("brand_mode") == "brand"
    except (ValueError, TypeError):
        pinned = False
    if not pinned:
        return generated_images(db, product_id, task_id)
    # Only the latest immutable revision of each completed frame is presented.
    result = {}
    for item in composed_images(db, product_id, task_id):
        try:
            number = int(json.loads(item.content)["frame_number"])
            if 1 <= number <= 10:
                previous = result.get(number)
                current_revision = int(json.loads(item.content).get("revision", 0))
                previous_revision = (int(json.loads(previous.content).get("revision", 0))
                                     if previous is not None else -1)
                if current_revision > previous_revision or (
                        current_revision == previous_revision and
                        (previous is None or (item.created_at, str(item.id)) >
                         (previous.created_at, str(previous.id)))):
                    result[number] = item
        except (ValueError, KeyError, TypeError):
            continue
    return [result[n] for n in sorted(result)]


def verified_gallery_image(db, root: Path, product_id: UUID, task_id: UUID,
                           artifact: Artifact) -> Path:
    if artifact.kind == "generated_image":
        path = verified_file(root, product_id, task_id, artifact)
        meta = json.loads(artifact.content)
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
            raise LookupError("Контрольная сумма файла не совпадает")
        return path
    if artifact.kind != "creative_story_render":
        raise LookupError("Неподдерживаемый тип изображения")
    meta = json.loads(artifact.content)
    render_id = UUID(meta["render_id"])
    row, path = verified_render(db, root, product_id, render_id)
    if row.sha256 != meta.get("sha256"):
        raise LookupError("Контрольная сумма кадра изменилась")
    return path


def verified_visible_images(db, root: Path, product_id: UUID,
                            task_id: UUID) -> tuple[list[Artifact], list[int]]:
    """Only advertise genuinely available, checksum-verified PNG files."""
    valid, missing = [], []
    for item in visible_images(db, product_id, task_id):
        try:
            verified_gallery_image(db, root, product_id, task_id, item)
            valid.append(item)
        except (ValueError, LookupError, OSError, KeyError, TypeError):
            try:
                number = int(json.loads(item.content).get("frame_number", 0))
                if 1 <= number <= 10:
                    missing.append(number)
            except (ValueError, TypeError):
                pass
    return valid, sorted(set(missing))


def compose_saved_stories(db, root: Path, request, draft, task_id: UUID) -> bool:
    """Render only missing frames; preserve partial successes and paid backgrounds."""
    version = validate_pinned_request(db, request, task_id)
    available = {}
    for item in generated_images(db, request.product_id, task_id):
        meta = json.loads(item.content)
        number = int(meta["frame_number"])
        if number in available:
            raise ValueError("Дублирующий checkpoint фона")
        available[number] = item
    finished = {}
    for item in composed_images(db, request.product_id, task_id):
        meta = json.loads(item.content)
        number = int(meta["frame_number"])
        if 1 <= number <= len(draft.frames):
            current = finished.get(number)
            if current is None or int(json.loads(current.content).get("revision", 0)) < int(meta.get("revision", 0)):
                finished[number] = item
    okay = True
    for frame in draft.frames:
        if frame.number in finished:
            # A saved row with missing/corrupted output is NOT a successful checkpoint.
            verified_gallery_image(db, root, request.product_id, task_id, finished[frame.number])
            continue
        try:
            background_id = None
            if request.image_model != "none":
                if frame.number not in available:
                    okay = False
                    continue
                saved = available[frame.number]
                path = verified_file(root, request.product_id, task_id, saved)
                meta = json.loads(saved.content)
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                    raise ValueError("Фон Stories повреждён")
                background_id = store_background(db, root, request.product_id, raw).id
            spec = story_frame(draft, frame, count=len(draft.frames))
            key = f"job-{str(task_id)[:25]}-{frame.number:02d}"
            render = create_composition(db, root, request.product_id,
                                        version.id, spec, frame_key=key,
                                        background_id=background_id,
                                        pinned_task_id=task_id)
            ArtifactService(db).create(
                product_id=request.product_id, task_id=task_id,
                kind="creative_story_render",
                title=f"Stories · кадр {frame.number}",
                content=json.dumps({
                    "frame_number": frame.number, "render_id": str(render.id),
                    "sha256": render.sha256, "filename": f"frame-{frame.number:02d}.png",
                    "ratio": "9:16", "model": ("brand:" + request.image_model),
                    "prompt": "Brand Kit: verified original logo and exact text",
                    "brand_version_id": str(version.id),
                    "revision": 0, "background_id": str(background_id) if background_id else None,
                }, ensure_ascii=False),
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            LOG.warning("Brand composition failed: task=%s frame=%d type=%s",
                        task_id, frame.number, type(exc).__name__)
            ArtifactService(db).create(
                product_id=request.product_id, task_id=task_id,
                kind="creative_image_error", title=f"Кадр {frame.number}: ошибка оформления",
                content=json.dumps({
                    "frame_number": frame.number, "code": type(exc).__name__,
                    "stage": "brand_story_composition",
                }, ensure_ascii=False),
            )
            db.commit()
            okay = False
    return okay and len(visible_images(db, request.product_id, task_id)) == len(draft.frames)
