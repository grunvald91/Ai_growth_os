from typing import Any
from uuid import UUID

from app.models import Approval
from app.services.base import CRUDService


class ApprovalService(CRUDService[Approval]):
    model = Approval
    update_fields = frozenset({"action_type", "risk_level", "status", "requested_by", "decided_by", "reason", "decided_at", "artifact_id"})
    valid_statuses = frozenset({"pending", "approved", "rejected"})

    def create(self, *, product_id: UUID, action_type: str, risk_level: str, **values: Any) -> Approval:
        self._validate_status(values.get("status"))
        approval = Approval(product_id=product_id, action_type=action_type, risk_level=risk_level, **values)
        self.db.add(approval)
        self.db.flush()
        self.db.refresh(approval)
        return approval

    def update(self, entity_id: UUID, **values: Any) -> Approval:
        self._validate_status(values.get("status"))
        approval = self.get(entity_id)
        if approval is None:
            raise ValueError("Approval not found")
        if "artifact_id" in values and values["artifact_id"] != approval.artifact_id:
            raise ValueError("Approval artifact binding is immutable; create a new approval")
        return self._update(entity_id, values)

    @classmethod
    def _validate_status(cls, status: str | None) -> None:
        if status is not None and status not in cls.valid_statuses:
            raise ValueError(f"Invalid approval status: {status}")
