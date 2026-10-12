from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, model_validator

from app.workflows.research_contracts import Contract, ResearchSourceBundle

Mode = Literal["reviews", "weaknesses", "pricing"]


class RunInput(Contract):
    product_id: UUID
    brief: str = Field(min_length=3, max_length=3000)
    mode: Mode = "reviews"
    region: str = Field(default="", max_length=200)
    competitors: list[str] = Field(default_factory=list, max_length=10)
    depth: Literal["quick", "standard", "deep"] = "standard"
    date_from: date | None = None
    date_to: date | None = None
    source_bundle: ResearchSourceBundle | None = None

    @model_validator(mode="after")
    def valid_context(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before date_to")
        if any(not name.strip() or len(name) > 200 for name in self.competitors):
            raise ValueError("Competitor names must contain 1-200 characters")
        return self


class ResearchPlan(Contract):
    mode: Mode
    questions: list[str] = Field(min_length=1, max_length=6)
    source_requests: list[str] = Field(default_factory=list, max_length=6)


class Label(Contract):
    evidence_id: str = Field(min_length=1, max_length=64)
    relevance: Literal["highly_relevant", "relevant", "weakly_relevant", "irrelevant"]
    topic: Literal["price", "quality", "speed", "service", "booking", "staff", "reliability", "UX", "support", "other"]
    sentiment: Literal["positive", "negative", "mixed", "neutral"]
    confidence: float = Field(ge=0, le=1)


class LabelBatch(Contract):
    labels: list[Label] = Field(min_length=1, max_length=8)


class Evidence(Contract):
    id: str
    competitor: str
    source_url: AnyHttpUrl
    source_type: Literal["review", "pricing", "website", "social", "marketplace"]
    platform: str
    published_at: datetime | None
    observed_at: datetime
    supplied_observed_at: str | None = None
    excerpt: str
    rating: float | None = None
    topic: str = "other"
    sentiment: str = "neutral"
    relevance: float = 0
    recency_score: float | None = None
    jeff_confidence: float | None = None
    decision_confidence: float | None = None
    validated_by: Literal["jeff", "strong_llm", "rule"] = "rule"
    dedupe_hash: str
    disposition: Literal["retained", "excluded"] = "excluded"
    exclusion_reason: str | None = None


class InsightDraft(Contract):
    view: Literal["weaknesses", "pricing", "opportunities", "reviews"]
    statement: str = Field(min_length=1, max_length=1500)
    impact: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0, le=1)
    state: Literal["signal", "inference", "conflict"]
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    # Quotes are copied verbatim; numerical price/rating/date facts come from evidence only.
    quotes: list[str] = Field(default_factory=list, max_length=4)
    suggested_test: str = Field(min_length=1, max_length=1000)


class SynthesisDraft(Contract):
    insights: list[InsightDraft] = Field(default_factory=list, max_length=12)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=12)


class Insight(InsightDraft):
    id: str
    evidence_count: int
    source_count: int
    period: dict


class Report(Contract):
    status: Literal["completed", "partial", "needs_sources"]
    insights: list[Insight] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    methodology: dict


class FollowUpInput(Contract):
    brief: str = Field(min_length=3, max_length=3000)


class ExportInput(Contract):
    insight_id: str = Field(min_length=1, max_length=64)
    target: Literal["task", "content_hypothesis", "offer_hypothesis"] = "task"
