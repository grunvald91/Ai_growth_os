"""Manual campaign workflow, deliberately separate from ExperimentLaunchService.

Changes are only tracking states in the local DB. They DO NOT launch ads,
contact leads, create jobs, post content, or spend money. Existing automated
experiment states (notably "running") are never modified by this workflow.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Experiment
from app.services.artifacts import ArtifactService

EVENT_KIND = "campaign_lifecycle_event"
LABELS = {
    "draft": "Черновик",
    "manual_review": "На согласовании",
    "manual_approved": "Согласована вручную",
    "manual_running": "В работе (учёт вручную)",
    "manual_paused": "Пауза (учёт вручную)",
    "manual_completed": "Завершена (учёт вручную)",
    # Existing experiment launch flows use their own statuses.
    "running": "Измеряемый эксперимент выполняется",
    "completed": "Измеряемый эксперимент завершён",
    "failed": "Ошибка эксперимента",
}
ACTIONS = {
    "draft": {"review": ("manual_review", "Передать на согласование")},
    "manual_review": {
        "approve": ("manual_approved", "Отметить согласованной"),
        "revise": ("draft", "Вернуть к редактированию"),
    },
    "manual_approved": {
        "start_tracking": ("manual_running", "Отметить в работе"),
        "revise": ("draft", "Вернуть к редактированию"),
    },
    "manual_running": {
        "pause": ("manual_paused", "Поставить на паузу"),
        "complete": ("manual_completed", "Отметить завершённой"),
    },
    "manual_paused": {
        "resume": ("manual_running", "Возобновить работу"),
        "complete": ("manual_completed", "Отметить завершённой"),
    },
    "manual_completed": {
        "reopen": ("manual_paused", "Открыть повторно"),
    },
}


class CampaignTransitionError(ValueError):
    pass


def allowed_actions(status: str) -> dict[str, tuple[str, str]]:
    return ACTIONS.get(status, {})


def change_campaign_status(
    db: Session, *, product_id: UUID, campaign_id: UUID,
    action: str, actor: str | None = None,
) -> str:
    """Atomic status CAS + append-only Artifact event in a single DB transaction."""
    # Product scope is mandatory; a foreign campaign is indistinguishable from missing.
    campaign = db.scalar(select(Experiment).where(
        Experiment.id == campaign_id, Experiment.product_id == product_id,
    ))
    if campaign is None:
        raise LookupError("Кампания не найдена")

    previous = campaign.status
    transition = allowed_actions(previous).get(action)
    if transition is None:
        raise CampaignTransitionError("Это действие недоступно для текущего состояния кампании")
    target, _label = transition
    # A concurrent approval/pause must not be silently overwritten.
    result = db.execute(
        update(Experiment).where(
            Experiment.id == campaign_id,
            Experiment.product_id == product_id,
            Experiment.status == previous,
        ).values(status=target).execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        db.rollback()
        raise CampaignTransitionError("Состояние кампании уже изменилось. Обновите страницу.")

    ArtifactService(db).create(
        product_id=product_id,
        kind=EVENT_KIND,
        title=f"campaign:{campaign_id}",
        content=json.dumps({
            "campaign_id": str(campaign_id),
            "from": previous,
            "to": target,
            "action": action,
            "actor": actor or None,
            "at": datetime.now(timezone.utc).isoformat(),
            "external_action": False,
        }, ensure_ascii=False),
    )
    db.commit()
    return target


def campaign_event_history(
    db: Session, *, product_id: UUID, campaign_ids: list[UUID],
    max_events: int = 5,
) -> dict[UUID, list[dict]]:
    """Latest recorded manual changes, scoped to the selected business."""
    if not campaign_ids:
        return {}
    ids = {str(key): key for key in campaign_ids}
    artifacts = db.scalars(
        select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.kind == EVENT_KIND,
            Artifact.title.in_([f"campaign:{key}" for key in ids]),
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1000)
    )
    history: dict[UUID, list[dict]] = {key: [] for key in campaign_ids}
    for item in artifacts:
        key = item.title.removeprefix("campaign:")
        if key not in ids:
            continue
        target = ids[key]
        try:
            event = json.loads(item.content)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("campaign_id") != key:
            continue
        history[target].append(event)
    # UUID IDs are not chronological and some databases store created_at
    # only to second precision. Use the UTC event time from each transition.
    for target in history:
        history[target].sort(key=lambda item: str(item.get("at") or ""), reverse=True)
        history[target] = history[target][:max_events]
    return history
