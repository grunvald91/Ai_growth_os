from typing import Any
from uuid import UUID

from app.models import Experiment
from app.services.base import CRUDService


class ExperimentService(CRUDService[Experiment]):
    model = Experiment
    update_fields = frozenset({"name", "hypothesis", "audience", "channel", "target_metric", "success_condition", "budget", "currency", "status", "conclusion", "starts_at", "ends_at", "goal_id"})

    def create(self, *, product_id: UUID, name: str, hypothesis: str, **values: Any) -> Experiment:
        experiment = Experiment(product_id=product_id, name=name, hypothesis=hypothesis, **values)
        self.db.add(experiment)
        self.db.flush()
        self.db.refresh(experiment)
        return experiment

    def update(self, entity_id: UUID, **values: Any) -> Experiment:
        return self._update(entity_id, values)
