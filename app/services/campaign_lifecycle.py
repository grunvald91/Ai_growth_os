"""Company-scoped, evidence-preserving lifecycle for marketing experiments.

"stopping" and "finishing" are *requests*, not proof that a worker or
external delivery has stopped.  Only explicitly linked preparation tasks and
experiment-scoped outreach are governed here.  Independent calendars retain
their own cancellation controls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.domain import (
    AgentRun, Artifact, ArtifactStatus, Decision, Experiment, Prospect, Publication,
    Task, TaskStatus, ToolCall,
)
from app.services.campaign_readiness import load_resources

REQUEST_STATES = {"stop": "stopping", "finish": "finishing"}
FINAL_STATES = {"stopping": "stopped", "finishing": "completed"}
SEND_BLOCKERS = {"sending", "delivery_unknown"}
PUBLICATION_BLOCKERS = {"pending", "scheduled", "sending", "delivery_unknown"}
ACTIVE_TASKS = {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}


class CampaignLifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class LifecycleBlockers:
    active_tasks: int = 0
    sending_emails: int = 0
    uncertain_emails: int = 0
    pending_publications: int = 0

    @property
    def clear(self) -> bool:
        return not any((self.active_tasks, self.sending_emails,
                        self.uncertain_emails, self.pending_publications))


def _associated_tasks(db: Session, campaign: Experiment, *, nowait: bool = False) -> list[Task]:
    """Resolve verified campaign tasks, including legacy CMO decision links."""
    decision_ids = list(db.scalars(select(Decision.id).where(
        Decision.product_id == campaign.product_id,
        Decision.experiment_id == campaign.id,
    )))
    tokens = [str(campaign.id), *(str(value) for value in decision_ids)]
    briefs = db.scalars(select(Artifact).join(
        Task, Task.id == Artifact.task_id,
    ).where(
        Artifact.product_id == campaign.product_id,
        Task.product_id == campaign.product_id,
        Artifact.kind == "task_brief",
        or_(*(Artifact.content.contains(token) for token in tokens)),
    ).execution_options(populate_existing=True))
    ids: set[UUID] = set()
    for brief in briefs:
        try:
            data = json.loads(brief.content)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        task = db.get(Task, brief.task_id)
        if task is None or task.product_id != campaign.product_id:
            continue
        associated = task_campaign(db, task, data)
        if associated is not None and associated.id == campaign.id:
            ids.add(task.id)
    if not ids:
        return []
    all_ids = set(ids)
    frontier = set(ids)
    for _ in range(16):
        if not frontier:
            break
        children = set(db.scalars(select(Task.id).where(
            Task.product_id == campaign.product_id,
            Task.parent_id.in_(frontier),
        )))
        frontier = children - all_ids
        all_ids.update(frontier)
    return list(db.scalars(select(Task).where(
        Task.product_id == campaign.product_id,
        Task.id.in_(all_ids),
    ).with_for_update(nowait=nowait).execution_options(populate_existing=True)))


def task_campaign(db: Session, task: Task, brief_data: dict, *, lock: bool = False) -> Experiment | None:
    """Verify the product-scoped experiment and, for follow-ups, its decision."""
    if not isinstance(brief_data, dict):
        return None
    raw = brief_data.get("experiment_id")
    decision_raw = brief_data.get("decision_id")
    if not raw and not decision_raw:
        return None
    try:
        explicit_id = UUID(str(raw)) if raw else None
        decision_id = UUID(str(decision_raw)) if decision_raw else None
    except (TypeError, ValueError, AttributeError):
        return None
    if decision_id is not None:
        decision = db.scalar(select(Decision).where(
            Decision.product_id == task.product_id,
            Decision.id == decision_id,
        ))
        if decision is None or decision.experiment_id is None:
            return None
        if explicit_id is not None and explicit_id != decision.experiment_id:
            return None
        experiment_id = decision.experiment_id
    else:
        experiment_id = explicit_id
    query = select(Experiment).where(
        Experiment.product_id == task.product_id,
        Experiment.id == experiment_id,
    )
    if lock:
        query = query.with_for_update()
    campaign = db.scalar(query.execution_options(populate_existing=True))
    if campaign is None:
        return None
    # A decisionless experiment_id is reserved for canonical launch tasks.
    if decision_id is None and task.title not in {
        f"{campaign.experiment_key}: prepare experiment",
        f"{campaign.experiment_key}: prepare outreach cohort",
    }:
        return None
    return campaign


def task_halted(db: Session, task: Task, brief_data: dict, *, lock: bool = False) -> bool:
    campaign = task_campaign(db, task, brief_data, lock=lock)
    return bool(campaign and campaign.status != "running")


def _outreach_blockers(db: Session, campaign: Experiment) -> tuple[int, int]:
    prospect_ids = set(db.scalars(select(Prospect.id).where(
        Prospect.product_id == campaign.product_id,
        Prospect.experiment_id == campaign.id,
    )))
    if not prospect_ids:
        return 0, 0
    calls = db.scalars(select(ToolCall).where(
        ToolCall.product_id == campaign.product_id,
        ToolCall.operation == "send_outreach_message",
        ToolCall.status.in_(SEND_BLOCKERS),
    ))
    sending = uncertain = 0
    for call in calls:
        try:
            pid = UUID(str((call.request_data or {}).get("prospect_id")))
        except (TypeError, ValueError):
            continue
        if pid in prospect_ids:
            if call.status == "sending":
                sending += 1
            else:
                uncertain += 1
    return sending, uncertain


def blockers(db: Session, campaign: Experiment, tasks: list[Task] | None = None) -> LifecycleBlockers:
    tasks = tasks if tasks is not None else _associated_tasks(db, campaign)
    sending, uncertain = _outreach_blockers(db, campaign)
    # Only publications linked to THIS campaign, not all posts for the company.
    # A scheduled send is not silently cancelled; the operator must resolve it
    # on the publishing screen. Never claim that it has been stopped.
    materials = load_resources(db, [campaign])[campaign.id].materials
    material_ids = {a.id for a in materials}
    pending = 0
    if material_ids:
        pending = sum(1 for _ in db.scalars(select(Publication.id).where(
            Publication.product_id == campaign.product_id,
            Publication.artifact_id.in_(material_ids),
            Publication.status.in_(PUBLICATION_BLOCKERS),
        )))
    return LifecycleBlockers(
        active_tasks=sum(task.status in ACTIVE_TASKS for task in tasks),
        sending_emails=sending,
        uncertain_emails=uncertain,
        pending_publications=pending,
    )


def _write_audit(db: Session, campaign: Experiment, actor: str,
                 command: str, note: str) -> None:
    now = datetime.now(timezone.utc)
    db.add(Artifact(
        product_id=campaign.product_id,
        kind="campaign_lifecycle_event",
        title=f"Campaign {campaign.id}: {command}",
        content=json.dumps({
            "experiment_id": str(campaign.id),
            "command": command,
            "actor": actor[:200],
            "note": note[:2000],
            "requested_at": now.isoformat(),
        }, ensure_ascii=False),
        status=ArtifactStatus.DRAFT,
    ))


def _cancel_queued_telegram(db: Session, campaign: Experiment) -> tuple[UUID, ...]:
    """Cancel only unsent Telegram intents belonging to this campaign.

    The Experiment row is locked by command() before this function. The
    Telegram sender takes the same lock before reserving a network send.
    In-flight and ambiguous attempts are NOT marked cancelled here.
    """
    material_ids = {
        artifact.id for artifact in load_resources(db, [campaign])[campaign.id].materials
    }
    if not material_ids:
        return ()
    ids = tuple(db.scalars(select(Publication.id).where(
        Publication.product_id == campaign.product_id,
        Publication.artifact_id.in_(material_ids),
        Publication.channel == "telegram",
        Publication.status.in_(("scheduled", "pending")),
    ).order_by(Publication.id)))
    if ids:
        db.execute(update(Publication).where(
            Publication.product_id == campaign.product_id,
            Publication.id.in_(ids),
            Publication.status.in_(("scheduled", "pending")),
        ).values(status="cancelled"))
    return ids


def _reconcile_cancelled_calendar(db: Session, product_id: UUID,
                                  cancelled: tuple[UUID, ...]) -> None:
    """Best effort calendar projection AFTER the stop transaction commits.

    Never hold the Experiment lock while taking a Product/calendar lock. A
    failed projection cannot undo the durable publication cancellation.
    """
    if not cancelled:
        return
    from app.content.telegram_publishing import update_plan_delivery

    titles = ["Telegram schedule " + str(pub_id) for pub_id in cancelled]
    intents = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "telegram_publish_intent",
        Artifact.title.in_(titles),
    ))
    for intent in list(intents):
        try:
            data = json.loads(intent.content)
            if UUID(str(data.get("publication_id"))) not in cancelled:
                continue
            update_plan_delivery(db, data, "cancelled")
            db.commit()
        except (LookupError, TypeError, ValueError, KeyError):
            db.rollback()


def _reconcile_locked(db: Session, campaign: Experiment, tasks: list[Task]) -> LifecycleBlockers:
    pending = blockers(db, campaign, tasks)
    if campaign.status in FINAL_STATES and pending.clear:
        campaign.status = FINAL_STATES[campaign.status]
        _write_audit(db, campaign, "system:lifecycle", campaign.status, "")
    return pending


def command(db: Session, *, product_id: UUID, experiment_id: UUID,
            action: str, actor: str, note: str = "") -> tuple[str, LifecycleBlockers]:
    if action not in REQUEST_STATES:
        raise CampaignLifecycleError("Неизвестное действие")
    if len(note) > 2000:
        raise CampaignLifecycleError("Комментарий слишком длинный")
    campaign = db.scalar(select(Experiment).where(
        Experiment.product_id == product_id, Experiment.id == experiment_id,
    ).with_for_update().execution_options(populate_existing=True))
    if campaign is None:
        raise LookupError("Кампания не найдена у выбранной организации")
    desired = REQUEST_STATES[action]
    final = FINAL_STATES[desired]
    if campaign.status not in {"running", desired, final}:
        raise CampaignLifecycleError("Для этой кампании действие сейчас недоступно")
    if campaign.status == "running":
        campaign.status = desired
        _write_audit(db, campaign, actor, action, note)
        if action == "finish" and note.strip():
            campaign.conclusion = "Вывод владельца: " + note.strip()
    # SessionLocal disables autoflush. Re-reading the same Experiment through
    # task_campaign(populate_existing=True) must not restore the old status.
    db.flush()
    tasks = _associated_tasks(db, campaign)
    cancelled: tuple[UUID, ...] = ()
    if campaign.status == desired:
        for task in tasks:
            if task.status in {TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL}:
                task.status = TaskStatus.CANCELLED
        db.flush()  # Persist cancellations before any populate_existing resource reads.
        cancelled = _cancel_queued_telegram(db, campaign)
        pending = _reconcile_locked(db, campaign, tasks)
    else:
        pending = blockers(db, campaign, tasks)
    db.commit()
    # Calendar is a denormalized view. Its locks are Product-first, so update
    # only after releasing the Experiment row lock.
    _reconcile_cancelled_calendar(db, product_id, cancelled)
    return campaign.status, pending


# Worker-process cursor: stuck early campaigns cannot starve later tenants.
# Each independent worker cycles through all pending UUIDs; a restart starts
# from the beginning, without storing global cross-tenant state in a company.
_RECONCILE_CURSOR: UUID | None = None


def reconcile_pending(db: Session, *, limit: int = 50) -> int:
    """Reconcile a bounded, round-robin page without forced completion."""
    global _RECONCILE_CURSOR
    limit = max(1, min(limit, 500))
    query = select(Experiment.id).where(
        Experiment.status.in_(FINAL_STATES),
    )
    ids = []
    if _RECONCILE_CURSOR is not None:
        ids = list(db.scalars(query.where(
            Experiment.id > _RECONCILE_CURSOR,
        ).order_by(Experiment.id).limit(limit)))
    if len(ids) < limit:
        remaining = limit - len(ids)
        beginning = query
        if _RECONCILE_CURSOR is not None:
            beginning = beginning.where(Experiment.id <= _RECONCILE_CURSOR)
        ids.extend(db.scalars(beginning.order_by(Experiment.id).limit(remaining)))
    if ids:
        _RECONCILE_CURSOR = ids[-1]
    changed = 0
    for experiment_id in ids:
        try:
            row = db.scalar(select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.status.in_(FINAL_STATES),
            ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if row is None:
                db.rollback()
                continue
            tasks = _associated_tasks(db, row, nowait=True)
            before = row.status
            _reconcile_locked(db, row, tasks)
            if row.status != before:
                changed += 1
            db.commit()
        except SQLAlchemyError:
            # A busy company/Task must not stall all subsequent workers.
            db.rollback()
            import logging
            logging.getLogger(__name__).warning("campaign_reconcile_skipped", exc_info=True)
    return changed


def closing_campaign_for_material(db: Session, product_id: UUID,
                                  artifact_id: UUID, *, lock: bool = True) -> bool:
    """Gate only verified material links, never every campaign for this company.

    Existing campaign row locks serialize a concurrent stop; a fresh link
    cannot commit after stop because link_resource locks the same Experiment.
    """
    query = select(Experiment).where(
        Experiment.product_id == product_id,
        Experiment.status.in_({"running", "stopping", "finishing", "stopped", "completed"}),
    ).order_by(Experiment.id)
    candidates = list(db.scalars(query.execution_options(populate_existing=True)))
    if not candidates:
        return False
    current = load_resources(db, candidates)
    linked_ids = [
        row.id for row in candidates
        if artifact_id in {a.id for a in current[row.id].materials}
    ]
    if not linked_ids:
        return False
    verified = select(Experiment).where(
        Experiment.product_id == product_id,
        Experiment.id.in_(linked_ids),
    ).order_by(Experiment.id)
    if lock:
        verified = verified.with_for_update()
    rows = list(db.scalars(verified.execution_options(populate_existing=True)))
    fresh = load_resources(db, rows)
    return any(
        row.status in {"stopping", "finishing", "stopped", "completed"}
        and artifact_id in {a.id for a in fresh[row.id].materials}
        for row in rows
    )

# Explicit operator-only recovery after verifying the research executor is OFF.
# No timeout/age heuristic is allowed to cancel a possibly live LLM call.
INTERNAL_RESEARCH_ROLES = frozenset({
    "research_pmm", "segment_researcher", "channel_researcher",
    "problem_researcher", "competitor_voc_researcher", "research_lead",
})


def recover_orphaned_research(
    db: Session, *, product_id: UUID, experiment_id: UUID,
    actor: str, confirmed_workers_stopped: bool = False, apply: bool = False,
) -> tuple[int, int]:
    """Manually finalize orphaned internal research after verified worker shutdown.

    Does not modify external publishing, ToolCalls, or independent tasks.
    Dry-run rolls back without changing anything. Caller must verify all
    research worker processes have been stopped before requesting apply.
    """
    if apply and not confirmed_workers_stopped:
        raise CampaignLifecycleError("Подтвердите остановку всех research-воркеров")
    row = db.scalar(select(Experiment).where(
        Experiment.product_id == product_id,
        Experiment.id == experiment_id,
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise LookupError("Кампания не найдена у выбранной организации")
    if row.status not in FINAL_STATES:
        raise CampaignLifecycleError("Восстановление доступно только для stopping/finishing")
    tasks = _associated_tasks(db, row)
    orphan_ids = [task.id for task in tasks if (
        task.status == TaskStatus.RUNNING and task.assigned_agent in INTERNAL_RESEARCH_ROLES
    )]
    runs = list(db.scalars(select(AgentRun).where(
        AgentRun.task_id.in_(orphan_ids), AgentRun.status == "running",
    ).with_for_update())) if orphan_ids else []
    if apply:
        for task in tasks:
            if task.id in orphan_ids:
                task.status = TaskStatus.CANCELLED
        for run in runs:
            run.status = "failed"
            run.error = "operator_verified_orphan_recovery"
            run.finished_at = datetime.now(timezone.utc)
        db.flush()  # Keep internal cancellation visible to fresh resource queries.
        _write_audit(db, row, actor, "operator_recover_research",
                     f"tasks={len(orphan_ids)}, runs={len(runs)}; workers externally verified stopped")
        _reconcile_locked(db, row, tasks)
        db.commit()
    else:
        db.rollback()
    return len(orphan_ids), len(runs)
