from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.domain import Approval
from app.services.approvals import ApprovalService


ApprovalDecision = Literal["approved", "rejected"]


class ApprovalDecisionError(ValueError):
    pass


def decide_approval(
    db: Session,
    approval_id: UUID,
    *,
    decision: ApprovalDecision,
    actor: str,
) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise LookupError("Approval not found")
    if approval.status != "pending":
        return approval
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
