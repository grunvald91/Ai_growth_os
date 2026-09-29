"""Deterministically materialize a draft Experiment from an approved-internal content plan.

This workflow performs no external action. It preserves traceability from the plan artifact
and optional CMO decision to the Experiment row and a manifest artifact.
"""
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Decision, Experiment, Product
from app.services.artifacts import ArtifactService
from app.services.experiments import ExperimentService
from app.workflows.team_contracts import ContentBrief


class ExperimentFromPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    plan_artifact_id: UUID
    decision_id: UUID | None = None


class ExperimentMaterializationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    experiment_id: UUID
    manifest_artifact_id: UUID
    status: str


class ExperimentMaterialization:
    def __init__(self, db: Session):
        self.db = db

    def run(self, request: ExperimentFromPlanInput) -> ExperimentMaterializationResult:
        product = self.db.get(Product, request.product_id)
        if product is None:
            raise LookupError("Product not found")

        plan_artifact = self.db.get(Artifact, request.plan_artifact_id)
        if (
            plan_artifact is None
            or plan_artifact.product_id != product.id
            or plan_artifact.kind != "content_plan"
        ):
            raise LookupError("Content plan artifact not found for product")

        decision = None
        if request.decision_id is not None:
            decision = self.db.get(Decision, request.decision_id)
            if decision is None or decision.product_id != product.id:
                raise LookupError("Decision not found for product")

        plan = ContentBrief.model_validate_json(plan_artifact.content)
        if plan.format != "content_plan":
            raise ValueError("Artifact is not a content-plan contract")

        # Idempotency: one experiment per source plan artifact.
        manifests = list(
            self.db.scalars(
                select(Artifact)
                .where(
                    Artifact.product_id == product.id,
                    Artifact.kind == "experiment_manifest",
                )
                .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            )
        )
        for manifest in manifests:
            try:
                payload = json.loads(manifest.content)
            except (TypeError, json.JSONDecodeError):
                continue
            if payload.get("plan_artifact_id") != str(plan_artifact.id):
                continue
            experiment_id = payload.get("experiment_id")
            if not experiment_id:
                continue
            experiment = self.db.get(Experiment, UUID(experiment_id))
            if experiment is not None and experiment.product_id == product.id:
                return ExperimentMaterializationResult(
                    product_id=product.id,
                    experiment_id=experiment.id,
                    manifest_artifact_id=manifest.id,
                    status=experiment.status,
                )

        name = plan.title_options[0][:300]
        hypothesis = (
            "Audience hypothesis: " + plan.audience_hypothesis
            + "\nProblem hypothesis: " + plan.problem_hypothesis
            + "\nTest angle: " + plan.angle
        )
        success_condition = (
            "Use persisted evidence only. Future CTA: " + plan.cta
            + "\nProof needed: " + "; ".join(plan.proof_needed)
            + "\nNo KEEP, STOP, or SCALE conclusion is implied by creating this draft experiment."
        )

        experiment = ExperimentService(self.db).create(
            product_id=product.id,
            goal_id=decision.goal_id if decision is not None else None,
            name=name,
            hypothesis=hypothesis,
            audience=plan.audience_hypothesis,
            channel="own_site",
            target_metric="qualified_leads",
            success_condition=success_condition,
            status="draft",
        )

        manifest_payload = {
            "schema_version": "experiment-manifest-v1",
            "experiment_id": str(experiment.id),
            "plan_artifact_id": str(plan_artifact.id),
            "decision_id": str(decision.id) if decision is not None else None,
            "channel": "own_site",
            "target_metric": "qualified_leads",
            "status": "draft",
            "external_action": False,
        }
        manifest = ArtifactService(self.db).create(
            product_id=product.id,
            kind="experiment_manifest",
            title=f"{experiment.name} — manifest",
            content=json.dumps(manifest_payload),
        )
        self.db.commit()
        return ExperimentMaterializationResult(
            product_id=product.id,
            experiment_id=experiment.id,
            manifest_artifact_id=manifest.id,
            status=experiment.status,
        )
