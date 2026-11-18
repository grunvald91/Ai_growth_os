"""Small durable dispatcher workflow engine.

Only local queued text-post drafts are mutating steps in this first release.
Every write requires an explicit, per-step click and is committed together
with the durable step receipt. External/paid/publishing steps are rejected.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dispatcher import executors
from app.dispatcher.blocks import clean_blocks
from app.dispatcher.decision import PlannedStep
from app.dispatcher.models import (
    DispatcherConversation, DispatcherWorkflow, DispatcherWorkflowStep,
)
from app.dispatcher.outcome import Context
from app.dispatcher.policy import Cost, Verdict, verdict
from app.dispatcher.registry import CAPABILITIES, PostParams
from app.dispatcher.service import READ_ONLY_PLAN_CAPABILITIES
from app.tools import ToolOperation

_logger = logging.getLogger(__name__)
WRITE_CAPABILITIES = frozenset({"create_post"})
_ALLOWED = READ_ONLY_PLAN_CAPABILITIES | WRITE_CAPABILITIES
_RESULT_TYPES = frozenset({"text", "links", "table", "resource_list", "task", "material"})
_MAX_STEPS = 3


def can_write(ctx: Context) -> bool:
    # The same conservative SSO write boundary as editorial-plan changes.
    return ctx.studio_role in (None, "owner", "co_owner")


def preflight(steps: list[PlannedStep], ctx: Context) -> list[tuple[str, dict]]:
    """Reject an entire unsafe chain *before* any step or paid work begins."""
    if not 2 <= len(steps) <= _MAX_STEPS:
        raise ValueError("В цепочке требуется от двух до трёх шагов.")
    parsed: list[tuple[str, dict]] = []
    fingerprints: set[tuple[str, str]] = set()
    from json import dumps
    for candidate in steps:
        cap = CAPABILITIES.get(candidate.capability)
        if cap is None or cap.id not in _ALLOWED:
            raise ValueError("В цепочке есть действие, пока не поддерживаемое оркестратором.")
        if cap.id in WRITE_CAPABILITIES:
            if not can_write(ctx):
                raise PermissionError("Запускать создание материалов может только владелец компании.")
            if (cap.operation != ToolOperation.GENERATE_DRAFT
                    or cap.cost != Cost.CHEAP or not cap.always_confirm
                    or cap.id not in executors.DESCRIBERS):
                raise ValueError("Недопустимая политика подтверждения действия.")
        elif (cap.id not in READ_ONLY_PLAN_CAPABILITIES
              or cap.operation != ToolOperation.ANALYTICS
              or cap.cost != Cost.NONE or cap.always_confirm):
            raise ValueError("Действие не разрешено для автоматического просмотра.")
        try:
            params = cap.params.model_validate(candidate.params).model_dump(exclude_none=True)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise ValueError("Не хватает или неверны параметры шага: " + fields[:160]) from None
        fingerprint = (cap.id, dumps(params, sort_keys=True, ensure_ascii=True))
        if fingerprint in fingerprints:
            raise ValueError("Повторяющийся шаг в одном поручении запрещён.")
        fingerprints.add(fingerprint)
        parsed.append((cap.id, params))
    if not any(cap in WRITE_CAPABILITIES for cap, _ in parsed):
        raise ValueError("Для этого поручения уже предусмотрен режим просмотров без изменений.")
    return parsed


def _scoped(db: Session, ctx: Context, workflow_id: UUID, *, lock: bool = False) -> DispatcherWorkflow:
    query = select(DispatcherWorkflow).where(
        DispatcherWorkflow.id == workflow_id,
        DispatcherWorkflow.product_id == ctx.product.id,
        DispatcherWorkflow.account == ctx.account,
    )
    if lock:
        query = query.with_for_update()
    workflow = db.scalar(query)
    if workflow is None:
        raise LookupError("Поручение не найдено")
    return workflow


def _steps(db: Session, workflow_id: UUID) -> list[DispatcherWorkflowStep]:
    return list(db.scalars(select(DispatcherWorkflowStep).where(
        DispatcherWorkflowStep.workflow_id == workflow_id,
    ).order_by(DispatcherWorkflowStep.position)))


def _status(steps: list[DispatcherWorkflowStep]) -> str:
    if any(s.status == "failed" for s in steps):
        return "failed"
    if any(s.status == "declined" for s in steps):
        return "cancelled"
    if any(s.status == "waiting_approval" for s in steps):
        return "waiting_approval"
    if all(s.status in {"completed", "no_data"} for s in steps):
        return "completed"
    return "running"


def _preview(confirmation) -> dict:
    return {
        "title": confirmation.title[:100],
        "details": [str(s)[:300] for s in confirmation.details[:8]],
        "confirm_label": confirmation.confirm_label[:60],
        "paid": bool(confirmation.paid),
    }


def _completed_step(blocks: list[dict]) -> list[dict]:
    return [b for b in clean_blocks(blocks) if b["type"] in _RESULT_TYPES][:8]


async def resume(ctx: Context, workflow_id: UUID) -> dict:
    """Resume only safe reads; never auto-approve or replay a write."""
    db = ctx.db
    for _ in range(_MAX_STEPS + 1):
        workflow = _scoped(db, ctx, workflow_id, lock=True)
        steps = _steps(db, workflow.id)
        if workflow.status in {"failed", "cancelled", "completed"}:
            return snapshot(ctx, workflow.id)
        active = next((s for s in steps if s.status not in
                       {"completed", "no_data", "skipped"}), None)
        if active is None:
            workflow.status = "completed"
            workflow.revision += 1
            db.commit()
            return snapshot(ctx, workflow.id)
        if active.status == "waiting_approval":
            workflow.status = "waiting_approval"
            db.commit()
            return snapshot(ctx, workflow.id)
        if active.status != "pending":
            raise RuntimeError("Unexpected workflow step state")
        cap = CAPABILITIES[active.capability]
        if active.capability in WRITE_CAPABILITIES:
            if not can_write(ctx):
                workflow.status = "failed"
                active.status = "failed"
                active.result_text = "Текущая роль не позволяет запускать этот шаг."
            else:
                try:
                    description = await executors.DESCRIBERS[active.capability](
                        ctx, cap.params.model_validate(active.params))
                    if not hasattr(description, "params"):
                        raise ValueError("Нет безопасного описания действия")
                    active.preview = _preview(description)
                    active.status = "waiting_approval"
                    active.result_text = "Требуется отдельное подтверждение этого шага."
                    workflow.status = "waiting_approval"
                except Exception:
                    _logger.exception("workflow_preview_failed", extra={"capability": active.capability})
                    active.status = "failed"
                    workflow.status = "failed"
                    active.result_text = "Не удалось подготовить подтверждение."
            workflow.revision += 1
            if workflow.status == "failed":
                for later in steps:
                    if later.position > active.position and later.status == "pending":
                        later.status = "skipped"
            db.commit()
            return snapshot(ctx, workflow.id)
        if active.capability not in READ_ONLY_PLAN_CAPABILITIES:
            raise RuntimeError("Workflow capability not permitted")
        try:
            # Reads use the same authorized, company-scoped executors as the
            # single-action dispatcher. No model-supplied text is persisted.
            outcome = await executors.EXECUTORS[active.capability](
                ctx, cap.params.model_validate(active.params))
            active.status = ("failed" if not outcome.ok else
                             "no_data" if outcome.no_data else "completed")
            active.result_text = outcome.text[:600]
            active.result_blocks = _completed_step(outcome.blocks)
        except Exception:
            db.rollback()
            workflow = _scoped(db, ctx, workflow_id, lock=True)
            steps = _steps(db, workflow.id)
            active = next(s for s in steps if s.position == active.position)
            active.status = "failed"
            active.result_text = "Не удалось прочитать данные. Шаг можно повторить вручную."
            _logger.exception("workflow_read_failed")
        workflow.status = _status(steps)
        if active.status == "failed":
            for later in steps:
                if later.position > active.position and later.status == "pending":
                    later.status = "skipped"
        workflow.revision += 1
        db.commit()
    return snapshot(ctx, workflow_id)


async def create(ctx: Context, conversation: DispatcherConversation,
                 steps: list[PlannedStep], client_key: str | None) -> dict:
    """Store the whole validated plan; a retry returns its existing receipt."""
    parsed = preflight(steps, ctx)
    db = ctx.db
    if client_key:
        existing = db.scalar(select(DispatcherWorkflow).where(
            DispatcherWorkflow.conversation_id == conversation.id,
            DispatcherWorkflow.client_key == client_key))
        if existing is not None:
            return snapshot(ctx, existing.id)
    workflow = DispatcherWorkflow(conversation_id=conversation.id,
                                  product_id=ctx.product.id, account=ctx.account,
                                  client_key=client_key, status="running", revision=0)
    try:
        db.add(workflow)
        db.flush()
        for position, (capability, params) in enumerate(parsed):
            db.add(DispatcherWorkflowStep(
                workflow_id=workflow.id, position=position, capability=capability,
                params=params, status="pending", preview={}, result_text="",
                result_blocks=[],
            ))
        db.commit()
    except IntegrityError:
        db.rollback()
        if client_key:
            previous = db.scalar(select(DispatcherWorkflow).where(
                DispatcherWorkflow.conversation_id == conversation.id,
                DispatcherWorkflow.client_key == client_key))
            if previous is not None:
                return snapshot(ctx, previous.id)
        raise
    return await resume(ctx, workflow.id)


def snapshot(ctx: Context, workflow_id: UUID) -> dict:
    """The only client-visible workflow shape; never return internal params."""
    workflow = _scoped(ctx.db, ctx, workflow_id)
    steps = _steps(ctx.db, workflow.id)
    running = next((s for s in steps if s.status == "waiting_approval"), None)
    payload_steps = [{
        "title": CAPABILITIES[step.capability].title,
        "status": step.status,
        "detail": step.result_text[:600] if step.result_text else
                  ("Ожидает предыдущих шагов." if step.status == "pending"
                   else "Не выполнялось." if step.status == "skipped" else ""),
        "blocks": _completed_step(step.result_blocks or []),
    } for step in steps]
    approval = ({**running.preview, "position": running.position}
                if running is not None else None)
    return {
        "type": "workflow",
        "workflow_id": str(workflow.id),
        "title": "Поручение из нескольких шагов",
        "status": workflow.status,
        "revision": workflow.revision,
        "approval": approval,
        "can_retry_read": (workflow.status == "failed"
                           and any(step.status == "failed" and
                                   step.capability in READ_ONLY_PLAN_CAPABILITIES
                                   for step in steps)),
        "steps": payload_steps,
    }


def recent(ctx: Context, *, limit: int = 8) -> list[dict]:
    """Unfinished plans also survive a new chat topic or lost HTTP response."""
    rows = list(ctx.db.scalars(select(DispatcherWorkflow).where(
        DispatcherWorkflow.product_id == ctx.product.id,
        DispatcherWorkflow.account == ctx.account,
        DispatcherWorkflow.status.in_(("running", "waiting_approval", "failed")),
    ).order_by(DispatcherWorkflow.created_at.desc(), DispatcherWorkflow.id.desc()).limit(limit)))
    return [snapshot(ctx, row.id) for row in rows]


async def decide(ctx: Context, workflow_id: UUID, *, position: int, revision: int,
                 accept: bool) -> dict:
    """Postgres row lock + one transaction for queued task and durable receipt."""
    db = ctx.db
    workflow = _scoped(db, ctx, workflow_id, lock=True)
    if revision != workflow.revision:
        raise PermissionError("Поручение изменилось. Обновите карточку.")
    steps = _steps(db, workflow.id)
    current = next((s for s in steps if s.status == "waiting_approval"), None)
    if current is None or current.position != position or workflow.status != "waiting_approval":
        raise PermissionError("Это подтверждение уже недоступно.")
    if not can_write(ctx):
        raise PermissionError("Изменять материалы может только владелец компании.")
    if not accept:
        current.status = "declined"
        current.result_text = "Владелец отклонил выполнение шага."
        current.decided_at = datetime.now(timezone.utc)
        for later in steps:
            if later.position > current.position and later.status == "pending":
                later.status = "skipped"
        workflow.status = "cancelled"
        workflow.revision += 1
        db.commit()
        return snapshot(ctx, workflow.id)
    if current.capability != "create_post":
        raise PermissionError("Для этого действия нет безопасного исполнителя.")
    from app.content.service import queue_content
    params = PostParams.model_validate(current.params)
    # This local operation is transactional (commit=False); it never publishes.
    # A crash before commit rolls both the Task and the step receipt back.
    try:
        task = queue_content(
            db, ctx.product.id, params.topic,
            format="post", channel=params.channel, audience=params.audience,
            editorial_brief=params.editorial_brief, commit=False,
            dedupe_key=f"DISPATCHER-WORKFLOW:{workflow.id}:{current.position}",
        )
        current.status = "completed"
        current.task_id = task.id
        current.decided_at = datetime.now(timezone.utc)
        current.result_text = "Задание на пост поставлено в очередь. Публикация не запускается."
        current.result_blocks = _completed_step([{
            "type": "task", "task_id": str(task.id), "title": "Пост: " + params.topic,
            "status": task.status.value,
            "href": f"/studio/products/{ctx.product.id}/tasks/{task.id}",
        }])
        workflow.status = "running"
        workflow.revision += 1
        db.commit()
    except Exception:
        db.rollback()
        _logger.exception("workflow_create_post_failed")
        # No write or receipt was committed. Do not auto-retry this step:
        # the user must decide what to do after inspecting the failure.
        workflow = _scoped(db, ctx, workflow_id, lock=True)
        steps = _steps(db, workflow.id)
        current = next(s for s in steps if s.position == position)
        if current.status == "waiting_approval":
            current.status = "failed"
            current.result_text = "Не удалось поставить пост в очередь. Проверьте задания перед повтором."
            for later in steps:
                if later.position > current.position and later.status == "pending":
                    later.status = "skipped"
            workflow.status = "failed"
            workflow.revision += 1
            db.commit()
        return snapshot(ctx, workflow.id)
    return await resume(ctx, workflow.id)


async def retry_read(ctx: Context, workflow_id: UUID, revision: int) -> dict:
    """A failed read can be rerun explicitly. A failed write never is."""
    db = ctx.db
    workflow = _scoped(db, ctx, workflow_id, lock=True)
    if workflow.revision != revision or workflow.status != "failed":
        raise PermissionError("Состояние изменилось. Обновите карточку.")
    steps = _steps(db, workflow.id)
    failed = next((s for s in steps if s.status == "failed"), None)
    if failed is None or failed.capability not in READ_ONLY_PLAN_CAPABILITIES:
        raise PermissionError("Автоматический повтор изменяющего действия запрещён.")
    failed.status = "pending"
    failed.result_text = ""
    failed.result_blocks = []
    for later in steps:
        if later.position > failed.position and later.status == "skipped":
            later.status = "pending"
    workflow.status = "running"
    workflow.revision += 1
    db.commit()
    return await resume(ctx, workflow.id)
