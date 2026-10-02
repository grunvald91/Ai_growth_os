import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.domain import Experiment, Product, TaskStatus
from app.services.artifacts import ArtifactService
from app.services.decisions import DecisionService
from app.services.experiments import ExperimentService
from app.services.tasks import TaskService


class ExperimentLaunchError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentLaunchResult:
    experiment_id: UUID
    decision_id: UUID
    task_id: UUID
    starts_at: datetime
    ends_at: datetime


_REQUIRED_FIELDS = (
    "experiment_key",
    "hypothesis",
    "audience",
    "channel",
    "action",
    "target_metric",
    "attribution_method",
    "baseline_summary",
    "observation_window_days",
    "time_budget_minutes",
    "success_condition",
    "stop_condition",
    "inconclusive_condition",
    "budget",
    "currency",
)


class ExperimentLaunchService:
    """Start a measured experiment and queue preparation work.

    Launching is an internal state transition only. It never contacts leads,
    publishes content, or executes another external action.
    """

    def __init__(self, db: Session, products_root: Path) -> None:
        self.db = db
        self.products_root = products_root

    def launch(self, experiment_id: UUID, *, actor: str) -> ExperimentLaunchResult:
        experiment = self.db.get(Experiment, experiment_id)
        if experiment is None:
            raise LookupError("Experiment not found")
        if experiment.status != "draft":
            raise ExperimentLaunchError(
                f"Experiment must be draft before launch; current status is {experiment.status}."
            )
        self._validate_contract(experiment)

        product = self.db.get(Product, experiment.product_id)
        if product is None:
            raise LookupError("Product not found")

        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(days=int(experiment.observation_window_days or 0))
        ExperimentService(self.db).update(
            experiment.id,
            status="running",
            starts_at=now,
            ends_at=ends_at,
        )

        key = experiment.experiment_key or str(experiment.id)
        decision = DecisionService(self.db).create(
            product_id=experiment.product_id,
            goal_id=experiment.goal_id,
            experiment_id=experiment.id,
            decision_type="TEST",
            rationale=(
                f"Experiment {key} launched by {actor}. "
                "Launch authorizes internal preparation and measurement only; "
                "external outreach/publication still requires separate approval."
            ),
            evidence_references=[
                {"kind": "experiment", "id": str(experiment.id)},
            ],
        )

        title = f"{key}: prepare outreach cohort"
        tasks = TaskService(self.db)
        task = tasks.find_active_duplicate(
            product_id=experiment.product_id,
            title=title,
            assigned_agent="research_pmm",
        )
        if task is None:
            task = tasks.create(
                product_id=experiment.product_id,
                goal_id=experiment.goal_id,
                title=title,
                description=(
                    "Review the bounded cohort and prepare a decision-ready validation/enrichment plan. "
                    "Do not contact businesses, publish anything, invent missing contact channels, "
                    "or treat stale/public-directory data as current without evidence."
                ),
                assigned_agent="research_pmm",
                status=TaskStatus.QUEUED,
                priority=100,
            )
            ArtifactService(self.db).create(
                product_id=experiment.product_id,
                task_id=task.id,
                kind="task_brief",
                title=title,
                content=json.dumps(
                    {
                        "decision_id": str(decision.id),
                        "experiment_id": str(experiment.id),
                        "experiment_key": key,
                        "title": title,
                        "description": task.description,
                        "assigned_agent": "research_pmm",
                        "task_type": "research",
                        "skill_name": None,
                        "priority": 100,
                        "operation": None,
                        "requires_approval": False,
                        "cohort_snapshot": self._load_cohort(product.slug, key),
                        "hard_constraints": [
                            "No outreach or publication.",
                            "No invented email, WhatsApp, Telegram, website, or decision-maker data.",
                            "Every candidate still requires current validation before counting as reached.",
                            "Sea SPA remains outside EXP-SERVGO-001 because prior contact predates the experiment.",
                        ],
                    },
                    ensure_ascii=False,
                ),
            )

        self.db.commit()
        refreshed = self.db.get(Experiment, experiment.id)
        return ExperimentLaunchResult(
            experiment_id=refreshed.id,
            decision_id=decision.id,
            task_id=task.id,
            starts_at=refreshed.starts_at,
            ends_at=refreshed.ends_at,
        )

    @staticmethod
    def _validate_contract(experiment: Experiment) -> None:
        missing = []
        for field in _REQUIRED_FIELDS:
            value = getattr(experiment, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        if missing:
            raise ExperimentLaunchError(
                "Experiment contract is incomplete: " + ", ".join(missing)
            )
        if int(experiment.observation_window_days or 0) <= 0:
            raise ExperimentLaunchError("Observation window must be positive.")
        if int(experiment.time_budget_minutes or 0) <= 0:
            raise ExperimentLaunchError("Time budget must be positive.")

    def _load_cohort(self, product_slug: str, experiment_key: str) -> dict:
        safe_slug = product_slug if product_slug.replace("-", "").isalnum() else ""
        safe_key = experiment_key if experiment_key.replace("-", "").isalnum() else ""
        if not safe_slug or not safe_key:
            return {"status": "unavailable", "leads": []}
        root = self.products_root.resolve()
        path = (root / safe_slug / "experiments" / safe_key / "cohort.json").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return {"status": "unavailable", "leads": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "invalid", "leads": []}

        leads = []
        for row in payload.get("leads", [])[:40]:
            leads.append(
                {
                    "lead_id": row.get("lead_id"),
                    "business_name": row.get("business_name"),
                    "area": row.get("area"),
                    "category": row.get("category"),
                    "public_phone": row.get("public_phone"),
                    "address": row.get("address"),
                    "source_place_id": row.get("source_place_id"),
                    "source_checked_at": row.get("source_checked_at"),
                    "fit_status": row.get("fit_status"),
                    "outreach_status": row.get("outreach_status"),
                }
            )
        return {
            "status": payload.get("status", "unknown"),
            "lead_count": len(leads),
            "pending_gates": payload.get("pending_gates", []),
            "leads": leads,
        }
