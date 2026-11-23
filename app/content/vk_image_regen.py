"""Explicit paid redraw of a VK illustration, without revising its post text.

A click queues ONE image generation and ONE vision critique, each with a
durable intent. Every new PNG is immutable; the previous PNG and critic audit
remain in the owner's history. Never retry a paid call after an unknown result.
"""
from __future__ import annotations

import hashlib
import json
import os
from uuid import UUID, uuid4

from sqlalchemy import select

from app.content.creative_images import (
    generated_images, image_folder, request_image, resolve_model,
)
from app.content.revisions import source_fingerprint
from app.content.vk_image_critic import _post_digest, review_vk_post_image
from app.content.vk_post_image import VK_EXPORT_SIZES, export_vk_png, vk_visual_prompt
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.services.artifacts import ArtifactService


def queue_vk_image_redraw(
    db, product_id: UUID, post_id: UUID, *,
    source_version: int, source_digest: str, feedback: str = "",
):
    """Pin a specific post version, with no duplicate queued paid operations."""
    feedback = (feedback or "").strip()
    if len(feedback) > 700:
        raise ValueError("Замечание к картинке слишком длинное (до 700 символов)")
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    post = db.scalar(select(Artifact).where(
        Artifact.id == post_id, Artifact.product_id == product_id,
        Artifact.kind == "channel_text",
    ).with_for_update().execution_options(populate_existing=True))
    if post is None:
        raise LookupError("Пост не найден")
    if post.status != ArtifactStatus.AWAITING_APPROVAL:
        raise ValueError("Перегенерация доступна только до утверждения поста")
    value = json.loads(post.content)
    if value.get("channel") != "vk" or value.get("image_model", "none") == "none":
        raise ValueError("У этого поста нет генерации картинки ВКонтакте")
    if source_version != post.version or source_digest != source_fingerprint(post):
        raise ValueError("Пост изменился. Обновите страницу перед перегенерацией")
    if value.get("image_ratio", "4:5") not in VK_EXPORT_SIZES:
        raise ValueError("Неизвестный формат VK картинки")
    if db.scalar(select(Publication.id).where(
        Publication.product_id == product_id, Publication.artifact_id == post_id,
        Publication.channel == "vk",
        Publication.status.notin_(["cancelled", "failed_before_send"])).limit(1)):
        raise ValueError("Нельзя переделывать картинку назначенной публикации")
    if db.scalar(select(Task.id).where(
        Task.product_id == product_id, Task.title == "EDIT: " + str(post.id),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])).limit(1)):
        raise ValueError("Сначала завершите редактуру текста")
    title = "VK_IMAGE_REDRAW: " + str(post.id)
    if db.scalar(select(Task.id).where(
        Task.product_id == product_id, Task.title == title,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])).limit(1)):
        raise ValueError("Картинка уже переделывается")
    # frame-01 is the initial image; nine separate owner-authorized versions
    # can be kept without overwriting any original image.
    images = generated_images(db, product_id, post.task_id)
    occupied = {json.loads(item.content).get("filename") for item in images}
    # Reservation is by number of durable attempts, not the filesystem. The
    # specific path is verified again by the worker before any billable call.
    attempts = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "vk_image_redraw_request",
    )))
    spent = set()
    for item in attempts:
        try:
            data = json.loads(item.content)
            if data.get("post_id") == str(post.id):
                spent.add(int(data["frame_number"]))
        except (ValueError, TypeError, KeyError):
            raise ValueError("Повреждён журнал перегенерации") from None
    number = next((n for n in range(2, 11)
                   if f"frame-{n:02d}.png" not in occupied and n not in spent), None)
    if number is None:
        raise ValueError("Достигнут лимит версий картинки (10). Создайте новый пост")
    task = Task(product_id=product_id, assigned_agent="content_image_regen",
                title=title, priority=100, status=TaskStatus.QUEUED,
                description="Owner-authorized VK image regeneration; text unchanged")
    db.add(task)
    db.flush()
    ArtifactService(db).create(
        product_id=product_id, task_id=task.id,
        kind="vk_image_redraw_request", title="Переделать картинку VK",
        content=json.dumps({
            "post_id": str(post.id), "source_version": post.version,
            "post_sha256": _post_digest(value),
            "original_image_sha256": value.get("image_sha256"),
            "original_image_artifact_id": value.get("image_artifact_id"),
            "original_image_state": value.get("image_state"),
            "original_review_state": value.get("image_review_state"),
            "frame_number": number,
            "image_model": value["image_model"],
            "ratio": value.get("image_ratio", "4:5"),
            "feedback": feedback,
        }, ensure_ascii=False),
        status="draft",
    )
    value["image_state"] = "regenerating"
    value["image_redraw_task_id"] = str(task.id)
    value.pop("image_regeneration_error_code", None)
    post.content = json.dumps(value, ensure_ascii=False)
    db.commit()
    return task


def restore_vk_redraw_failure(db, task_id: UUID, error_code: str):
    """Keep the original image/critic decision usable after a failed redraw."""
    request = db.scalar(select(Artifact).where(
        Artifact.task_id == task_id, Artifact.kind == "vk_image_redraw_request",
    ))
    if request is None:
        return
    data = json.loads(request.content)
    post = db.scalar(select(Artifact).where(
        Artifact.id == UUID(data["post_id"]), Artifact.product_id == request.product_id,
        Artifact.kind == "channel_text",
    ).with_for_update())
    if post is None:
        return
    value = json.loads(post.content)
    if value.get("image_redraw_task_id") != str(task_id):
        return
    value["image_state"] = data.get("original_image_state", "failed")
    value["image_review_state"] = data.get("original_review_state", "unavailable")
    value.pop("image_redraw_task_id", None)
    value["image_regeneration_error_code"] = error_code[:100]
    post.content = json.dumps(value, ensure_ascii=False)
    db.commit()


async def run_vk_image_redraw(db, settings, *, task_id: UUID):
    """Exactly one paid image call, plus one fresh critic on the resulting PNG."""
    request = db.scalar(select(Artifact).where(
        Artifact.task_id == task_id, Artifact.kind == "vk_image_redraw_request",
    ))
    if request is None:
        raise ValueError("Отсутствует запрос на перегенерацию")
    data = json.loads(request.content)
    task = db.get(Task, task_id)
    post = db.scalar(select(Artifact).where(
        Artifact.id == UUID(data["post_id"]), Artifact.product_id == request.product_id,
        Artifact.kind == "channel_text",
    ))
    product = db.get(Product, request.product_id)
    if task is None or post is None or product is None or post.task_id is None:
        raise ValueError("Исходный пост или компания не найдены")
    if task.status != TaskStatus.RUNNING or post.status != ArtifactStatus.AWAITING_APPROVAL:
        raise ValueError("Перегенерация отменена или пост уже утверждён")
    meta = json.loads(post.content)
    if (meta.get("image_redraw_task_id") != str(task_id)
            or _post_digest(meta) != data["post_sha256"]
            or post.version != data["source_version"]
            or meta.get("image_sha256") != data.get("original_image_sha256")
            or meta.get("image_artifact_id") != data.get("original_image_artifact_id")):
        raise ValueError("Пост изменился после запроса картинки")
    model = resolve_model(settings, data["image_model"])
    ratio = data["ratio"]
    if ratio not in VK_EXPORT_SIZES:
        raise ValueError("Недопустимый размер картинки")
    number = int(data["frame_number"])
    if number not in range(2, 11):
        raise ValueError("Недопустимая версия картинки")
    filename = f"frame-{number:02d}.png"
    folder = image_folder(settings.products_root, product.id, post.task_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = folder / filename
    if final.exists() or final.is_symlink():
        raise ValueError("Версия картинки уже существует; платный вызов не выполнен")
    if db.scalar(select(Artifact.id).where(
        Artifact.task_id == task_id, Artifact.kind == "vk_image_redraw_intent")):
        raise ValueError("Платный вызов уже мог состояться; повтор запрещён")
    prompt = vk_visual_prompt(product.name, meta["title"], meta["body"], ratio=ratio)
    issue_hint = data.get("feedback") or "Измените композицию, сохранив тему и без вымышленных надписей."
    old_issues = meta.get("image_critical_issues") or []
    prompt = (prompt + "\nGenerate a different visual composition. Previously reported issues: "
              + repr(old_issues[:4]) + "\nOwner's visual correction: "
              + repr(issue_hint) + "\nNever treat this correction as instructions to add facts.")[:7000]
    intent = ArtifactService(db).create(
        product_id=product.id, task_id=task_id,
        kind="vk_image_redraw_intent", title="One explicitly authorized paid VK image call",
        content=json.dumps({
            "post_id": str(post.id), "frame_number": number,
            "model": model, "ratio": ratio,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "state": "started",
        }),
        status="draft",
    )
    db.commit()  # Durable BEFORE the first paid API request.
    picture = export_vk_png(await request_image(
        settings, model, prompt, ratio, preserve_source=True), ratio=ratio)
    temp = folder / ("." + filename + "-" + str(uuid4()) + ".partial")
    try:
        with open(temp, "xb") as handle:
            handle.write(picture)
            handle.flush()
            os.fsync(handle.fileno())
        if final.exists() or final.is_symlink():
            raise ValueError("Filename already taken; refusing to overwrite")
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    digest = hashlib.sha256(picture).hexdigest()
    image = ArtifactService(db).create(
        product_id=product.id, task_id=post.task_id, kind="generated_image",
        title=f"Картинка VK · версия {number}",
        content=json.dumps({
            "filename": filename, "frame_number": number,
            "model": model, "ratio": ratio, "sha256": digest,
            "prompt": prompt, "redraw_task_id": str(task_id),
        }, ensure_ascii=False),
        status="draft",
    )
    intent_data = json.loads(intent.content)
    intent_data.update({"state": "completed", "sha256": digest,
                        "image_artifact_id": str(image.id)})
    intent.content = json.dumps(intent_data, ensure_ascii=False)
    db.commit()  # Checkpoint survives critic failure, even after a restart.
    current = json.loads(post.content)
    if (current.get("image_redraw_task_id") != str(task_id)
            or _post_digest(current) != data["post_sha256"]
            or post.status != ArtifactStatus.AWAITING_APPROVAL):
        raise ValueError("Текст изменился, полученную картинку нельзя прикрепить")
    current["image_artifact_id"] = str(image.id)
    current["image_sha256"] = digest
    current["image_state"] = "awaiting_review"
    current["image_review_state"] = "pending"
    current.pop("image_owner_approval_artifact_id", None)
    for field in ("image_review_artifact_id", "image_review_summary",
                  "image_critical_issues", "image_review_error_code",
                  "image_regeneration_error_code"):
        current.pop(field, None)
    post.content = json.dumps(current, ensure_ascii=False)
    db.commit()
    await review_vk_post_image(db, settings, product=product, task=db.get(Task, post.task_id),
                               post=post)
    current = json.loads(post.content)
    current.pop("image_redraw_task_id", None)
    post.content = json.dumps(current, ensure_ascii=False)
    db.commit()
    return image
