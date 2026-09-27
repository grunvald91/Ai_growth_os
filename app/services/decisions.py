from typing import Any
from uuid import UUID

from app.models import Decision
from app.models.domain import DecisionType
from app.services.base import CRUDService


class DecisionService(CRUDService[Decision]):
    model = Decision
    update_fields = frozenset({"goal_id", "experiment_id", "agent_run_id", "decision_type", "rationale", "evidence_references"})

    def create(self, *, product_id: UUID, decision_type: DecisionType | str, rationale: str, evidence_references: list[Any] | None = None, **values: Any) -> Decision:
        decision = Decision(
            product_id=product_id,
            decision_type=self._decision_type(decision_type),
            rationale=rationale,
            evidence_references=evidence_references or [],
            **values,
        )
        self.db.add(decision)
        self.db.flush()
        self.db.refresh(decision)
        return decision

    def update(self, entity_id: UUID, **values: Any) -> Decision:
        if "decision_type" in values:
            values["decision_type"] = self._decision_type(values["decision_type"])
        if values.get("evidence_references") is None:
            values["evidence_references"] = []
        return self._update(entity_id, values)

    @staticmethod
    def _decision_type(value: DecisionType | str) -> DecisionType:
        try:
            return value if isinstance(value, DecisionType) else DecisionType(value)
        except ValueError as exc:
            raise ValueError(f"Invalid decision type: {value}") from exc
