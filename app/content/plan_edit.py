"""Tenant-scoped editing of topics within unapproved editorial content packages.

An owner may change editorial wording without re-generating the entire plan.
No dates, evidence, approval state or queued/published content are modified.
"""
import json
from uuid import UUID

from sqlalchemy import select

from app.content.calendar import EditorialPlan, Slot
from app.models.domain import Artifact, Product


def update_draft_package(db, *, product_id: UUID, plan_id: UUID,
                         package_key: str, topic: str, objective: str,
                         audience: str) -> bool:
    """Update all channel adaptations of one draft package atomically.

    Returns False when nothing changed; raises on unknown plan/package or
    non-editable plan. The caller must authenticate the owning company.
    """
    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    row = db.scalar(select(Artifact).where(
        Artifact.id == plan_id, Artifact.product_id == product_id,
        Artifact.kind == "editorial_plan",
    ).with_for_update())
    if row is None:
        raise LookupError("Контент-план не найден")
    try:
        plan = json.loads(row.content)
    except (TypeError, ValueError) as exc:
        raise ValueError("Данные контент-плана повреждены") from exc
    if plan.get("approval_state") != "draft":
        raise ValueError("Изменять темы можно только до утверждения плана")
    values = {"topic": topic.strip(), "objective": objective.strip(),
              "audience": audience.strip()}
    for key, value in values.items():
        bounds = {"topic": (3, 300), "objective": (3, 600), "audience": (3, 300)}
        lo, hi = bounds[key]
        if not lo <= len(value) <= hi:
            raise ValueError(f"Поле «{key}»: требуется от {lo} до {hi} символов")

    slots = [s for s in plan.get("slots", [])
             if s.get("package_key") == package_key]
    if not slots:
        raise LookupError("Контент-пакет не найден")
    if any(s.get("task_id") or s.get("state") not in (None, "planned", "missed") or
           s.get("publication_url") or s.get("telegram_publication_id")
           for s in slots):
        raise ValueError("Пакет уже находится в работе — сначала проверьте созданные материалы")
    if all(all(s.get(k) == v for k, v in values.items()) for s in slots):
        return False

    for s in slots:
        s.update(values)

    # Validate the complete original plan contract; derived calendar timestamps
    # and tenant-only metadata are not part of the model's source schema.
    source = {name: plan[name] for name in EditorialPlan.model_fields if name in plan}
    source['slots'] = [
        {name: slot[name] for name in Slot.model_fields if name in slot}
        for slot in plan['slots']
    ]
    EditorialPlan.model_validate(source)
    row.content = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return True
