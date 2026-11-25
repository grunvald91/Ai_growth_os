"""Owner-confirmed recovery using the same gates as Studio, never a new paid job by guess."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.dispatcher.outcome import Confirmation, Context, Outcome, links_block, task_block
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus


def _allowed(ctx):
    return ctx.studio_role in (None, "owner", "co_owner")


def _denied():
    return Outcome("Это действие доступно владельцу компании.", ok=False)


def _task(ctx, task_id):
    return ctx.db.scalar(select(Task).where(Task.id == task_id, Task.product_id == ctx.product.id)
                         .execution_options(populate_existing=True))


def _post(ctx, material_id):
    return ctx.db.scalar(select(Artifact).where(Artifact.id == material_id,
                         Artifact.product_id == ctx.product.id, Artifact.kind == "channel_text")
                         .execution_options(populate_existing=True))


def _task_result(ctx, task, text, *, href=None):
    href = href or f"/studio/products/{ctx.product.id}/tasks/{task.id}"
    return Outcome(text, task_id=task.id, blocks=[task_block(ctx, task, "Ход задания", href)])


async def describe_retry_content_task(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    task = _task(ctx, params.task_id)
    if task is None or task.assigned_agent not in {"content_factory", "content_creative", "content_editor"}:
        return Outcome("Не нашёл подходящее контентное задание. Для видео используйте продолжение видео.",
                       ok=False, blocks=[links_block(ctx, "work")])
    if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
        return Outcome("Повторить можно только остановленное задание.", ok=False)
    from app.api.content_studio_activity import task_title
    return Confirmation("Повторить остановленное задание?",
                        [task_title(task), "Возможны новые платные обращения к ИИ. Существующие ограничения и проверки сохраняются."],
                        {"task_id": str(task.id), "updated_at": task.updated_at.isoformat()},
                        paid=True, confirm_label="Повторить")


async def confirm_retry_content_task(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    from app.api.content_studio import retry_stopped_content_task
    try:
        task, href = retry_stopped_content_task(
            ctx.db, ctx.product.id, UUID(stored["task_id"]), confirmed=True,
            studio_role=ctx.studio_role, actor=ctx.account, expected_updated_at=stored["updated_at"])
    except (HTTPException, ValueError, KeyError, LookupError) as exc:
        ctx.db.rollback()
        text = str(exc.detail) if isinstance(exc, HTTPException) else "Не удалось повторить задание. Проверьте его состояние."
        return Outcome(text, ok=False, blocks=[links_block(ctx, "work")])
    return _task_result(ctx, task, "Задание поставлено в очередь повторно.", href=href)


async def describe_resume_video(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    from app.content.whiteboard.jobs import (owner_task, records, digest, can_resume, can_top_up,
        call_limit, started_attempts, TOP_UP_TOTAL)
    from app.content.whiteboard.runtime import ReadinessError
    try:
        task = owner_task(ctx.db, ctx.product.id, params.task_id)
        source, state, approval, payload = records(ctx.db, task)
        value = json.loads(state.content)
        extra = params.extra_calls
        if task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            return Outcome("Видео уже ожидает запуска или выполняется. Повторно не запускаю.",
                           blocks=[task_block(ctx, task, "Ход видео", f"/studio/products/{ctx.product.id}/whiteboard/{task.id}")])
        if approval.status != "approved":
            return Outcome("Сначала необходимо утвердить исходное видео.", ok=False)
        if extra and (not can_top_up(task.status, value, payload)
                      or int(value.get("extra_calls", 0)) + extra > TOP_UP_TOTAL):
            return Outcome("Дополнительный лимит для этого задания сейчас недоступен.", ok=False)
        proposed = {**value, "extra_calls": int(value.get("extra_calls", 0)) + extra}
        if not can_resume(task.status, proposed, payload):
            if can_top_up(task.status, value, payload):
                return Outcome("Лимит исчерпан. Укажите, сколько дополнительных обращений разрешаете (до 10 за подтверждение).",
                               ok=False)
            return Outcome("Сохранённое задание сейчас нельзя возобновить. Проверьте его состояние.", ok=False)
        return Confirmation("Продолжить создание видео?",
            [payload.request.topic, "Готовые этапы будут использованы повторно.",
             f"Начато обращений: {started_attempts(value)}. Общий лимит после подтверждения: {call_limit(proposed, payload)}.",
             f"Дополнительных обращений к лимиту: {extra}. Возможны платные вызовы."],
            {"task_id": str(task.id), "state_digest": digest(state.content),
             "request_digest": digest(source.content), "extra_calls": extra},
            paid=True, confirm_label="Продолжить")
    except (ReadinessError, ValueError, LookupError, TypeError, KeyError):
        ctx.db.rollback()
        return Outcome("Не удалось проверить исходное видео.", ok=False, blocks=[links_block(ctx, "whiteboard")])


async def confirm_resume_video(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    from app.content.whiteboard.jobs import owner_task, records, digest, resume_job
    from app.content.whiteboard.runtime import ReadinessError
    try:
        task_id = UUID(stored["task_id"])
        # Same lock order as resume_job, preventing a stale card from racing automatic recovery.
        ctx.db.scalar(select(Product).where(Product.id == ctx.product.id).with_for_update())
        task = owner_task(ctx.db, ctx.product.id, task_id, lock=True)
        source, state, approval, payload = records(ctx.db, task, lock=True)
        if (digest(state.content) != stored["state_digest"]
                or digest(source.content) != stored["request_digest"]):
            ctx.db.rollback()
            return Outcome("Состояние видео изменилось. Проверьте его повторно перед запуском.", ok=False)
        task = resume_job(ctx.db, ctx.product.id, task_id, confirmed=True,
                          decided_by=ctx.account, extra_calls=int(stored["extra_calls"]))
    except (ReadinessError, ValueError, LookupError, TypeError, KeyError):
        ctx.db.rollback()
        return Outcome("Возобновление недоступно. Проверьте состояние видео и лимит.", ok=False)
    return _task_result(ctx, task, "Видео поставлено в очередь на продолжение с сохранённых этапов.",
                        href=f"/studio/products/{ctx.product.id}/whiteboard/{task.id}")


async def describe_redraw_post_image(ctx: Context, params):
    if not _allowed(ctx):
        return _denied()
    from app.content.revisions import source_fingerprint
    row = _post(ctx, params.material_id)
    if row is None:
        return Outcome("Не нашёл выбранный пост этой компании.", ok=False, blocks=[links_block(ctx, "review")])
    try:
        value = json.loads(row.content)
        if (row.status != ArtifactStatus.AWAITING_APPROVAL or row.task_id is None
                or value.get("channel") != "vk" or value.get("image_model", "none") == "none"):
            return Outcome("Перегенерация доступна только для поста ВКонтакте с картинкой до утверждения.", ok=False)
        return Confirmation("Перегенерировать картинку поста?",
            [row.title[:120], "Текст поста не изменится.", f"Модель: {value['image_model']}.",
             "Одна новая генерация изображения и одна проверка критиком — возможны платные вызовы."]
             + ([f"Пожелание: {params.feedback}"] if params.feedback else []),
            {"material_id": str(row.id), "source_version": row.version,
             "source_digest": source_fingerprint(row), "feedback": params.feedback},
            paid=True, confirm_label="Перегенерировать")
    except (ValueError, TypeError, AttributeError, KeyError):
        return Outcome("Не удалось проверить параметры исходного поста.", ok=False)


async def confirm_redraw_post_image(ctx: Context, stored, via):
    if not _allowed(ctx):
        return _denied()
    from app.content.vk_image_regen import queue_vk_image_redraw
    try:
        task = queue_vk_image_redraw(ctx.db, ctx.product.id, UUID(stored["material_id"]),
            source_version=int(stored["source_version"]), source_digest=stored["source_digest"],
            feedback=stored["feedback"])
    except (LookupError, ValueError, TypeError, KeyError):
        ctx.db.rollback()
        return Outcome("Перегенерация недоступна: пост изменился, уже выполняется правка или исчерпан лимит. "
                       "Проверьте текущий пост.", ok=False, blocks=[links_block(ctx, "review")])
    return _task_result(ctx, task, "Поставил новую картинку в очередь. Текст поста остаётся прежним.")


DESCRIBERS = {"retry_content_task": describe_retry_content_task, "resume_video": describe_resume_video,
              "redraw_post_image": describe_redraw_post_image}
CONFIRMERS = {"retry_content_task": confirm_retry_content_task, "resume_video": confirm_resume_video,
              "redraw_post_image": confirm_redraw_post_image}
