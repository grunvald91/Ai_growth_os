"""Typed outputs for bounded specialist team execution."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflows.contracts import ReviewResult

Text = str
ShortText = str


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchCandidate(Contract):
    segment_hypothesis: str = Field(min_length=1, max_length=300)
    channel: str = Field(min_length=1, max_length=200)
    booking_job: str = Field(min_length=1, max_length=2000)
    proposed_test: str = Field(min_length=1, max_length=2000)
    evidence_to_support: list[str] = Field(min_length=1, max_length=8)
    evidence_to_kill: list[str] = Field(min_length=1, max_length=8)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=12)


class ResearchReport(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    candidates: list[ResearchCandidate] = Field(min_length=1, max_length=8)
    recommended_test: str = Field(min_length=1, max_length=3000)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)


class FunnelStageSpec(Contract):
    stage: Literal["content", "traffic", "lead", "activation", "customer"]
    event: str = Field(min_length=1, max_length=300)
    definition: str = Field(min_length=1, max_length=2000)
    required_fields: list[str] = Field(min_length=1, max_length=12)
    evidence_needed: str = Field(min_length=1, max_length=2000)


class AnalyticsPlan(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    qualified_lead_definition: str = Field(min_length=1, max_length=2000)
    stages: list[FunnelStageSpec] = Field(min_length=5, max_length=5)
    attribution_notes: str = Field(min_length=1, max_length=3000)
    missing_evidence: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def exact_funnel(self):
        expected = {"content", "traffic", "lead", "activation", "customer"}
        if {stage.stage for stage in self.stages} != expected:
            raise ValueError("Analytics plan must define each funnel stage exactly once")
        return self


class ContentBrief(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    format: Literal["article", "video", "content_plan"]
    audience_hypothesis: str = Field(min_length=1, max_length=1000)
    problem_hypothesis: str = Field(min_length=1, max_length=2000)
    angle: str = Field(min_length=1, max_length=2000)
    title_options: list[str] = Field(min_length=2, max_length=5)
    outline: list[str] = Field(min_length=3, max_length=15)
    cta: str = Field(min_length=1, max_length=1000)
    verified_facts_to_use: list[str] = Field(default_factory=list, max_length=20)
    hypotheses_to_label: list[str] = Field(default_factory=list, max_length=20)
    proof_needed: list[str] = Field(default_factory=list, max_length=20)


class DistributionCandidate(Contract):
    channel: str = Field(min_length=1, max_length=200)
    audience_hypothesis: str = Field(min_length=1, max_length=1000)
    proposed_action: str = Field(min_length=1, max_length=2000)
    downstream_goal: Literal["lead", "activation", "customer"]
    evidence_to_support: str = Field(min_length=1, max_length=2000)
    evidence_to_kill: str = Field(min_length=1, max_length=2000)


class DistributionPlan(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    candidates: list[DistributionCandidate] = Field(min_length=1, max_length=10)
    recommended_first_test: str = Field(min_length=1, max_length=3000)
    approval_required_actions: list[str] = Field(default_factory=list, max_length=20)


class TeamTaskResult(Contract):
    task_id: UUID
    status: Literal["completed", "failed", "skipped"]
    run_id: UUID | None = None
    artifact_id: UUID | None = None
    error: str | None = None


class TeamCycleResult(Contract):
    product_id: UUID
    task_results: list[TeamTaskResult] = Field(default_factory=list)
    completed_task_ids: list[UUID] = Field(default_factory=list)
    failed_task_ids: list[UUID] = Field(default_factory=list)
    skipped_task_ids: list[UUID] = Field(default_factory=list)
    artifact_ids: list[UUID] = Field(default_factory=list)
    run_ids: list[UUID] = Field(default_factory=list)
    followup_review: ReviewResult | None = None
