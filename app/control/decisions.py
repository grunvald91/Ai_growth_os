from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.domain import Approval, Artifact, Experiment
from app.services.approvals import ApprovalService


ApprovalDecision = Literal["approved", "rejected"]


class ApprovalDecisionError(ValueError):
    pass


def approval_review_token(db: Session, approval: Approval) -> str:
    artifact = db.get(Artifact, approval.artifact_id) if approval.artifact_id else None
    if approval.artifact_id and (artifact is None or artifact.product_id != approval.product_id):
        raise ApprovalDecisionError("Материал согласования недоступен.")
    snapshot = {"product_id": str(approval.product_id), "action": approval.action_type,
                "risk": approval.risk_level, "artifact_id": str(approval.artifact_id),
                "content": artifact.content if artifact else None, "reason": approval.reason}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]


def decide_approval(
    db: Session,
    approval_id: UUID,
    *,
    decision: ApprovalDecision,
    actor: str,
    expected_review_token: str | None = None,
) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise LookupError("Approval not found")
    # Same lock order as outreach preparation: experiment, then approval.
    if approval.action_type == "send_outreach_batch" and approval.artifact_id:
        artifact = db.get(Artifact, approval.artifact_id)
        try:
            experiment_id = UUID(json.loads(artifact.content)["experiment_id"])
        except (AttributeError, ValueError, KeyError, TypeError):
            raise ApprovalDecisionError("Пакет согласования недоступен.") from None
        experiment = db.scalar(select(Experiment).where(Experiment.id == experiment_id,
            Experiment.product_id == approval.product_id).with_for_update())
        if experiment is None:
            raise ApprovalDecisionError("Эксперимент согласования недоступен.")
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update()
                         .execution_options(populate_existing=True))
    if approval.status != "pending":
        if approval.decided_by == "growth_os:superseded":
            raise ApprovalDecisionError("Пакет заменён. Откройте актуальное согласование.")
        return approval
    if expected_review_token is not None or (decision == "approved" and
                                              approval.action_type == "send_outreach_batch"):
        if not expected_review_token or not hmac.compare_digest(
                expected_review_token, approval_review_token(db, approval)):
            raise ApprovalDecisionError("Материал изменился. Откройте актуальное согласование.")
    if decision == "approved" and approval.risk_level.lower() == "red":
        raise ApprovalDecisionError(
            "RED approvals require a higher-assurance flow and cannot be approved with one tap."
        )

    approval = ApprovalService(db).update(
        approval_id,
        status=decision,
        decided_by=actor,
        decided_at=datetime.now(timezone.utc),
    )
    db.commit()
    db.refresh(approval)
    return approval
