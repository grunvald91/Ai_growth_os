from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import TaskStatus, ArtifactStatus, DecisionType
from app.workflows.article_contracts import ArticleProductionInput, ArticleProductionResult, Channel
from app.workflows.team_contracts import TeamCycleResult
from app.workflows.experiment_materialization import ExperimentMaterializationResult


class ArticleCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    task_id: UUID | None = None
    decision_id: UUID | None = None
    language: Literal['ru', 'en'] = 'ru'
    experiment_id: UUID | None = None
    channel: Channel = 'own_site'
    topic: str = Field(min_length=1, max_length=300)
    audience: str | None = Field(default=None, min_length=1, max_length=300)
    problem: str | None = Field(default=None, min_length=1, max_length=4000)
    request_publication: bool = False

    def workflow_input(self, product_id: UUID) -> ArticleProductionInput:
        return ArticleProductionInput(product_id=product_id, **self.model_dump())


class ArticleProductionRead(ArticleProductionResult):
    main_artifact_id: UUID | None = None
    derivative_artifact_ids: list[UUID] = Field(default_factory=list)


class TeamCycleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_tasks: int = Field(default=10, ge=1, le=20, strict=True)
    followup_cmo: bool = True


class TeamCycleRead(TeamCycleResult):
    pass


class ExperimentFromPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_artifact_id: UUID
    decision_id: UUID | None = None


class ExperimentMaterializationRead(ExperimentMaterializationResult):
    pass


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = Field(default=None, max_length=10000)


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    target_metric: str | None = Field(default=None, max_length=100)
    target_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    starts_on: date | None = None
    ends_on: date | None = None


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class ProductRead(Record, ProductCreate):
    pass


class GoalRead(Record, GoalCreate):
    product_id: UUID
    status: str


class TaskRead(Record):
    product_id: UUID
    parent_id: UUID | None
    title: str
    description: str | None
    assigned_agent: str | None
    status: TaskStatus
    priority: int


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    experiment_key: str = Field(min_length=5, max_length=100, pattern=r"^EXP-[A-Z0-9-]+$")
    goal_id: UUID | None = None
    name: str = Field(min_length=1, max_length=300)
    hypothesis: str = Field(min_length=1, max_length=4000)
    audience: str = Field(min_length=1, max_length=2000)
    channel: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=4000)
    target_metric: str = Field(min_length=1, max_length=100)
    secondary_signals: list[str] = Field(default_factory=list, max_length=20)
    attribution_method: str = Field(min_length=1, max_length=4000)
    baseline_summary: str = Field(min_length=1, max_length=4000)
    observation_window_days: int = Field(ge=1, le=180, strict=True)
    time_budget_minutes: int = Field(ge=1, le=100000, strict=True)
    success_condition: str = Field(min_length=1, max_length=4000)
    stop_condition: str = Field(min_length=1, max_length=4000)
    inconclusive_condition: str = Field(min_length=1, max_length=4000)
    budget: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3)
    status: Literal["draft"] = "draft"


class ExperimentRead(Record):
    product_id: UUID
    goal_id: UUID | None
    experiment_key: str | None
    name: str
    hypothesis: str
    audience: str | None
    channel: str | None
    action: str | None
    target_metric: str | None
    secondary_signals: list
    attribution_method: str | None
    baseline_summary: str | None
    observation_window_days: int | None
    time_budget_minutes: int | None
    success_condition: str | None
    stop_condition: str | None
    inconclusive_condition: str | None
    budget: Decimal | None
    currency: str | None
    status: str
    conclusion: str | None
    starts_at: datetime | None
    ends_at: datetime | None


class ArtifactRead(Record):
    product_id: UUID
    task_id: UUID | None
    agent_run_id: UUID | None
    kind: str
    title: str
    content: str
    status: ArtifactStatus


class DecisionRead(Record):
    product_id: UUID
    agent_run_id: UUID | None
    decision_type: DecisionType
    rationale: str
    evidence_references: list[dict]


class ApprovalRead(Record):
    product_id: UUID
    artifact_id: UUID | None
    action_type: str
    risk_level: str
    status: str
    reason: str | None
