from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


ControlEventKind = Literal["decision_required", "important_result", "experiment_finished"]


class ControlAction(BaseModel):
    key: Literal["approve", "reject", "open"]
    label: str


class ControlInboxItem(BaseModel):
    id: UUID
    product_id: UUID
    kind: ControlEventKind
    title: str
    summary: str
    risk_level: str | None = None
    action_type: str | None = None
    created_at: datetime
    actions: list[ControlAction] = Field(default_factory=list)


class ControlExperiment(BaseModel):
    id: UUID
    experiment_key: str | None
    name: str
    status: str
    target_metric: str | None
    created_at: datetime


class ProductControlStatus(BaseModel):
    product_id: UUID
    product_name: str
    pending_approvals: int
    decisions_waiting: int
    active_experiments: int
    draft_experiments: int
    experiments: list[ControlExperiment] = Field(default_factory=list)
    inbox_preview: list[ControlInboxItem] = Field(default_factory=list)


class ControlStatus(BaseModel):
    products: list[ProductControlStatus] = Field(default_factory=list)
    decisions_waiting: int = 0
    active_experiments: int = 0
