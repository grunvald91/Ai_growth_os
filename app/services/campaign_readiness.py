"""Company-scoped campaign resource links and factual owner readiness.

The workspace is an internal Artifact, not campaign content or an approval.
Existing review and Telegram publishing flows remain the sole authorities.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Decision, Experiment, Task

WORKSPACE_KIND = "campaign_workspace"
MATERIAL_KINDS = frozenset({
    "article", "channel_text", "creative_brief", "video_script",
    "video_render", "short_video_brief", "story_brief",
    "static_image_brief", "whiteboard_video",
})
MAX_LINKED_MATERIALS = 100


class CampaignResourceError(ValueError):
    pass


@dataclass(frozen=True)
class CampaignResources:
    materials: tuple[Artifact, ...] = ()
    connections: tuple[Artifact, ...] = ()
    selected_connection: Artifact | None = None
    needs_telegram: bool = False

    @property
    def pending_material(self) -> Artifact | None:
        return next((a for a in self.materials
                     if a.status == ArtifactStatus.AWAITING_APPROVAL), None)


def is_telegram(row: Experiment) -> bool:
    # Only an explicit provider value authorizes this requirement.
    return (row.channel or "").strip().lower() == "telegram"


def workspace_title(experiment_id: UUID) -> str:
    return f"Campaign resources {experiment_id}"


def _decode(row: Artifact | None, experiment_id: UUID) -> dict:
    if row is None:
        return {}
    try:
        data = json.loads(row.content)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get("experiment_id") != str(experiment_id):
        return {}
    return data


def load_resources(db: Session, campaigns: list[Experiment]) -> dict[UUID, CampaignResources]:
    """Load verified material/channel links for one company-scoped page."""
    if not campaigns:
        return {}
    product_id = campaigns[0].product_id
    if any(row.product_id != product_id for row in campaigns):
        raise ValueError("Cannot mix companies in a campaign context")
    names = {workspace_title(row.id): row.id for row in campaigns}
    records = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == WORKSPACE_KIND,
        Artifact.title.in_(names),
    ).execution_options(populate_existing=True)))
    by_id = {names[row.title]: _decode(row, names[row.title])
             for row in records if row.title in names}
    wanted: set[UUID] = set()
    for data in by_id.values():
        links = data.get("material_ids")
        for raw in (links if isinstance(links, list) else [])[:MAX_LINKED_MATERIALS]:
            try:
                wanted.add(UUID(str(raw)))
            except (ValueError, TypeError, AttributeError):
                continue
        try:
            if data.get("telegram_connection_id"):
                wanted.add(UUID(str(data["telegram_connection_id"])))
        except (ValueError, TypeError, AttributeError):
            pass
    targets = {}
    if wanted:
        targets = {a.id: a for a in db.scalars(select(Artifact).where(
            Artifact.product_id == product_id, Artifact.id.in_(wanted),
        ).execution_options(populate_existing=True))}
    # Use the SAME verified campaign/task resolver as claim, stop and recovery.
    # CMO follow-up briefs can reference Decision without a canonical launch title.
    from app.services.campaign_lifecycle import task_campaign

    selected_ids = {row.id for row in campaigns}
    decisions = list(db.scalars(select(Decision.id).where(
        Decision.product_id == product_id,
        Decision.experiment_id.in_(selected_ids),
    )))
    tokens = [*(str(value) for value in selected_ids),
              *(str(value) for value in decisions)]
    task_to_experiment = {}
    if tokens:
        briefs = list(db.scalars(select(Artifact).join(
            Task, Task.id == Artifact.task_id,
        ).where(
            Artifact.product_id == product_id,
            Task.product_id == product_id,
            Artifact.kind == "task_brief",
            or_(*(Artifact.content.contains(token) for token in tokens)),
        ).execution_options(populate_existing=True)))
        for brief in briefs:
            task = db.get(Task, brief.task_id)
            if task is None:
                continue
            try:
                payload = json.loads(brief.content)
            except (ValueError, TypeError):
                continue
            match = task_campaign(db, task, payload)
            if match is not None and match.id in selected_ids:
                task_to_experiment[task.id] = match.id
    generated = {}
    if task_to_experiment:
        for artifact in db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_(task_to_experiment),
            Artifact.kind.in_(MATERIAL_KINDS),
        ).order_by(Artifact.created_at.desc())):
            exp_id = task_to_experiment[artifact.task_id]
            generated.setdefault(exp_id, []).append(artifact)
    telegram_used = any(is_telegram(row) for row in campaigns)
    connections = ()
    if telegram_used:
        connections = tuple(db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.kind == "telegram_channel_connection",
            Artifact.status == ArtifactStatus.APPROVED,
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    result = {}
    for row in campaigns:
        data = by_id.get(row.id, {})
        materials = []
        seen = set()
        links = data.get("material_ids")
        for raw in (links if isinstance(links, list) else [])[:MAX_LINKED_MATERIALS]:
            try:
                key = UUID(str(raw))
            except (ValueError, TypeError, AttributeError):
                continue
            record = targets.get(key)
            if record is not None and record.kind in MATERIAL_KINDS and key not in seen:
                materials.append(record)
                seen.add(key)
        for artifact in generated.get(row.id, []):
            if artifact.id not in seen:
                materials.append(artifact)
                seen.add(artifact.id)
        selection = None
        if is_telegram(row):
            try:
                chosen = UUID(str(data.get("telegram_connection_id")))
            except (ValueError, TypeError, AttributeError):
                chosen = None
            selection = next((a for a in connections if a.id == chosen), None)
        result[row.id] = CampaignResources(
            materials=tuple(materials),
            connections=connections if is_telegram(row) else (),
            selected_connection=selection,
            needs_telegram=is_telegram(row),
        )
    return result


def link_resource(db: Session, product_id: UUID, experiment_id: UUID,
                  target_id: UUID, *, relation: str) -> None:
    """Validate tenant, type and current status before saving a local link.

    Lock the material first, then the experiment, to serialize links with
    publication gates and concurrent edits. No migration is required; this
    does not authorize delivery or publication.
    """
    # Acquire the material-row mutex BEFORE the Experiment lock. The
    # Telegram scheduling and send gates take the material lock first too.
    # A new link therefore cannot slip through their initial workspace scan
    # and the lock order cannot invert against a pending send.
    target = None
    if relation == "material":
        target = db.scalar(select(Artifact).where(
            Artifact.id == target_id, Artifact.product_id == product_id,
        ).with_for_update().execution_options(populate_existing=True))
        if target is None:
            raise LookupError("Ресурс не найден у выбранной компании")
    row = db.scalar(select(Experiment).where(
        Experiment.id == experiment_id, Experiment.product_id == product_id,
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise LookupError("Кампания не найдена")
    if row.status not in {"draft", "running"}:
        raise CampaignResourceError("Менять связи можно только при подготовке кампании")
    if target is None:
        target = db.scalar(select(Artifact).where(
            Artifact.id == target_id, Artifact.product_id == product_id,
        ).execution_options(populate_existing=True))
        if target is None:
            raise LookupError("Ресурс не найден у выбранной компании")
    if relation == "telegram":
        if not is_telegram(row):
            raise CampaignResourceError("Для кампании не выбран канал Telegram")
        if (target.kind != "telegram_channel_connection" or
                target.status != ArtifactStatus.APPROVED):
            raise CampaignResourceError("Требуется проверенный Telegram-канал")
    elif relation == "material":
        if target.kind not in MATERIAL_KINDS:
            raise CampaignResourceError("Выбранная запись не является материалом")
    else:
        raise CampaignResourceError("Неизвестный тип ресурса")

    workspace = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == WORKSPACE_KIND,
        Artifact.title == workspace_title(experiment_id),
    ).with_for_update().execution_options(populate_existing=True))
    data = _decode(workspace, experiment_id)
    if not data:
        data = {"experiment_id": str(experiment_id), "material_ids": []}
    if relation == "telegram":
        data["telegram_connection_id"] = str(target.id)
    else:
        material_ids = data.get("material_ids") or []
        if not isinstance(material_ids, list):
            material_ids = []
        if str(target.id) not in material_ids:
            if len(material_ids) >= MAX_LINKED_MATERIALS:
                raise CampaignResourceError("Достигнут лимит материалов кампании")
            material_ids.append(str(target.id))
        data["material_ids"] = material_ids

    if workspace is None:
        db.add(Artifact(
            product_id=product_id, kind=WORKSPACE_KIND,
            title=workspace_title(experiment_id),
            content=json.dumps(data, ensure_ascii=False),
            status=ArtifactStatus.DRAFT,
        ))
    else:
        workspace.content = json.dumps(data, ensure_ascii=False)
    db.commit()
