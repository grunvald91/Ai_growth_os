from typing import Any
from uuid import UUID

from app.models import Experiment
from app.policies.experiment_preflight import LAUNCH_STATUSES, PREFLIGHT_FIELDS, require_launch_ready
from app.services.base import CRUDService


class ExperimentService(CRUDService[Experiment]):
    model = Experiment
    update_fields = frozenset({"experiment_key", "name", "hypothesis", "audience", "channel", "action", "target_metric", "secondary_signals", "attribution_method", "baseline_summary", "observation_window_days", "time_budget_minutes", "success_condition", "stop_condition", "inconclusive_condition", "budget", "currency", "status", "conclusion", "starts_at", "ends_at", "goal_id"})

    def create(self, *, product_id: UUID, name: str, hypothesis: str, **values: Any) -> Experiment:
        if values.get("status") in LAUNCH_STATUSES:
            require_launch_ready(values)
        experiment = Experiment(product_id=product_id, name=name, hypothesis=hypothesis, **values)
        self.db.add(experiment)
        self.db.flush()
        self.db.refresh(experiment)
        return experiment

    def update(self, entity_id: UUID, **values: Any) -> Experiment:
        experiment = self.get(entity_id)
        if experiment is not None:
            status = values.get("status", experiment.status)
            if status in LAUNCH_STATUSES:
                # Validate the merged state so a launch cannot bypass preflight.
                merged = {name: values.get(name, getattr(experiment, name)) for name in PREFLIGHT_FIELDS}
                require_launch_ready(merged)
        return self._update(entity_id, values)
