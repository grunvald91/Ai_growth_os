"""Typed, bounded strategic proposals. Model output never grants approval."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.agents import AgentRole
from app.models.domain import DecisionType
from app.tools import ToolOperation

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

class EvidenceReference(Contract):
    kind: Literal['goal', 'metric', 'experiment', 'decision', 'task', 'artifact', 'approval']
    id: UUID

class NextTask(Contract):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    assigned_agent: AgentRole
    task_type: Literal['research', 'article_brief', 'video_brief', 'content_plan', 'distribution_plan', 'analytics', 'action_request']
    skill_name: str | None = Field(default=None, max_length=200, pattern=r'^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$')
    priority: int = Field(default=0, ge=0, le=100, strict=True)
    operation: ToolOperation | None = None
    requires_approval: bool = False

    @model_validator(mode='after')
    def safe_delegation(self):
        from app.policies import PolicyEvaluator
        if self.assigned_agent == AgentRole.CMO:
            raise ValueError('Delegate to a specialist')
        if self.operation in {ToolOperation.LAUNCH_AD, ToolOperation.INCREASE_BUDGET}:
            raise ValueError('Paid operations excluded')
        if self.operation and PolicyEvaluator().evaluate(self.operation).requires_approval:
            self.requires_approval = True
        if self.task_type == 'action_request' and self.operation is None:
            raise ValueError('Action requests require an explicit operation')
        return self

class StrategicDecision(Contract):
    decision_type: DecisionType
    rationale: str = Field(min_length=1, max_length=4000)
    goal_id: UUID | None = None
    experiment_id: UUID | None = None
    evidence_references: list[EvidenceReference] = Field(default_factory=list, max_length=30)
    next_tasks: list[NextTask] = Field(default_factory=list, max_length=5)

class CMOReview(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    decisions: list[StrategicDecision] = Field(min_length=1, max_length=5)

class ReviewResult(Contract):
    product_id: UUID
    task_id: UUID
    run_id: UUID
    status: Literal['completed', 'failed']
    decision_ids: list[UUID] = Field(default_factory=list)
    task_ids: list[UUID] = Field(default_factory=list)
    artifact_ids: list[UUID] = Field(default_factory=list)
    approval_ids: list[UUID] = Field(default_factory=list)
    error: str | None = None
