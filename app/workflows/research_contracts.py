"""Typed contracts for the bounded Research Department and external evidence bundles."""
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchSource(Contract):
    url: AnyHttpUrl
    title: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=120)
    competitor: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=4000)
    rating: float | None = Field(default=None, ge=0, le=5)
    observed_at: str | None = Field(default=None, max_length=80)


class ResearchSourceBundle(Contract):
    label: str = Field(min_length=1, max_length=300)
    sources: list[ResearchSource] = Field(min_length=1, max_length=50)


class SegmentHypothesis(Contract):
    segment: str = Field(min_length=1, max_length=300)
    booking_job: str = Field(min_length=1, max_length=1500)
    servgo_relevance: str = Field(min_length=1, max_length=1500)
    evidence_to_support: list[str] = Field(min_length=1, max_length=4)
    evidence_to_kill: list[str] = Field(min_length=1, max_length=4)


class SegmentResearchReport(Contract):
    summary: str = Field(min_length=1, max_length=3000)
    hypotheses: list[SegmentHypothesis] = Field(min_length=1, max_length=4)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)


class ChannelHypothesis(Contract):
    channel: str = Field(min_length=1, max_length=200)
    audience_hypothesis: str = Field(min_length=1, max_length=1000)
    proposed_use: str = Field(min_length=1, max_length=1500)
    evidence_to_support: list[str] = Field(min_length=1, max_length=4)
    evidence_to_kill: list[str] = Field(min_length=1, max_length=4)


class ChannelResearchReport(Contract):
    summary: str = Field(min_length=1, max_length=3000)
    hypotheses: list[ChannelHypothesis] = Field(min_length=1, max_length=4)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)


class ProblemHypothesis(Contract):
    problem: str = Field(min_length=1, max_length=1500)
    affected_role_hypothesis: str = Field(min_length=1, max_length=500)
    verified_servgo_capability: str = Field(min_length=1, max_length=1500)
    fit_hypothesis: str = Field(min_length=1, max_length=1500)
    evidence_to_support: list[str] = Field(min_length=1, max_length=4)
    evidence_to_kill: list[str] = Field(min_length=1, max_length=4)


class ProblemResearchReport(Contract):
    summary: str = Field(min_length=1, max_length=3000)
    hypotheses: list[ProblemHypothesis] = Field(min_length=1, max_length=4)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)


class VOCProblemPattern(Contract):
    competitor: str = Field(min_length=1, max_length=200)
    problem: str = Field(min_length=1, max_length=1500)
    occurrences_in_bundle: int = Field(ge=1, le=50, strict=True)
    source_urls: list[AnyHttpUrl] = Field(min_length=1, max_length=10)
    implication_hypothesis: str = Field(min_length=1, max_length=1500)
    servgo_check: str = Field(min_length=1, max_length=1500)


class CompetitorVOCReport(Contract):
    source_status: Literal["evidence_available", "needs_sources"]
    summary: str = Field(min_length=1, max_length=3000)
    patterns: list[VOCProblemPattern] = Field(default_factory=list, max_length=10)
    source_requests: list[str] = Field(default_factory=list, max_length=10)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def source_state_matches_content(self):
        if self.source_status == "needs_sources" and self.patterns:
            raise ValueError("VOC patterns require supplied review sources")
        if self.source_status == "evidence_available" and not self.patterns:
            raise ValueError("Evidence-available VOC report requires at least one pattern")
        return self


class ResearchOpportunity(Contract):
    segment_hypothesis: str = Field(min_length=1, max_length=500)
    problem_hypothesis: str = Field(min_length=1, max_length=1500)
    channel_hypothesis: str = Field(min_length=1, max_length=1000)
    competitor_signal: str = Field(min_length=1, max_length=1500)
    servgo_relevance: str = Field(min_length=1, max_length=1500)
    proposed_test: str = Field(min_length=1, max_length=2000)
    evidence_to_support: list[str] = Field(min_length=1, max_length=5)
    evidence_to_kill: list[str] = Field(min_length=1, max_length=5)


class ResearchSynthesisDraft(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    department_status: Literal["complete", "partial"]
    competitor_voc_status: Literal["evidence_available", "needs_sources"]
    top_opportunities: list[ResearchOpportunity] = Field(min_length=1, max_length=4)
    recommended_test: str = Field(min_length=1, max_length=2500)
    product_questions: list[str] = Field(default_factory=list, max_length=10)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=15)


class ResearchDepartmentReport(ResearchSynthesisDraft):
    parent_task_id: UUID
    worker_task_ids: list[UUID] = Field(min_length=4, max_length=4)
    worker_artifact_ids: list[UUID] = Field(min_length=3, max_length=4)
    lead_run_id: UUID
