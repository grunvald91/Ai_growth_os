"""Typed contracts of the semantic core. Suggestions carry no demand numbers, and none are invented."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Query = Annotated[str, Field(min_length=2, max_length=300)]
Intent = Literal["informational", "commercial", "transactional", "navigational"]
FunnelStage = Literal["awareness", "consideration", "decision"]
Engine = Literal["yandex", "google"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QueryCluster(Contract):
    id: str = Field(pattern=r"^c\d{3,5}$")
    head_query: Query
    intent: Intent
    stage: FunnelStage
    queries: list[Query] = Field(min_length=1)
    engines: list[Engine] = Field(min_length=1)


class SourceStatus(Contract):
    engine: Engine
    requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    queries_found: int = Field(ge=0)


class SemanticCore(Contract):
    seeds: list[Query] = Field(min_length=1)
    collected_at: datetime
    demand_data: Literal["none"] = "none"  # suggestions show what people type, not how often
    sources: list[SourceStatus]
    clusters: list[QueryCluster]


class SeedList(Contract):
    """Seed queries phrased the way customers search, not the way the company describes itself."""
    seeds: list[Query] = Field(min_length=3, max_length=10)


class PlanItem(Contract):
    id: str = Field(pattern=r"^a\d{3,5}$")
    cluster_ids: list[str] = Field(min_length=1)
    primary_query: Query
    working_title: Annotated[str, Field(min_length=5, max_length=300)]
    stage: FunnelStage
    intent: Intent
    reader_problem: Annotated[str, Field(min_length=10, max_length=1500)]
    ai_questions: list[Query] = Field(min_length=1)  # what a person would ask an AI assistant on this topic
    next_step: Annotated[str, Field(min_length=5, max_length=600)]  # where the article leads the reader


class SeoContentPlan(Contract):
    items: list[PlanItem] = Field(min_length=1)
