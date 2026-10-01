import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactStatus(str, enum.Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class DecisionType(str, enum.Enum):
    KEEP = "KEEP"
    STOP = "STOP"
    SCALE = "SCALE"
    TEST = "TEST"


class Product(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    context_path: Mapped[str | None] = mapped_column(String(500))

    goals: Mapped[list["Goal"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Goal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "goals"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_metric: Mapped[str | None] = mapped_column(String(100))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)

    product: Mapped[Product] = relationship(back_populates="goals")


class Task(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    goal_id: Mapped[UUID | None] = mapped_column(ForeignKey("goals.id"), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_agent: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        default=TaskStatus.QUEUED,
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    input_references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output_references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Artifact(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "artifacts"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        Enum(
            ArtifactStatus,
            name="artifact_status",
            native_enum=False,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        default=ArtifactStatus.DRAFT,
        nullable=False,
    )


class Experiment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("product_id", "experiment_key", name="uq_experiments_product_key"),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    goal_id: Mapped[UUID | None] = mapped_column(ForeignKey("goals.id"), index=True)
    experiment_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str | None] = mapped_column(Text)
    target_metric: Mapped[str | None] = mapped_column(String(100))
    secondary_signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    attribution_method: Mapped[str | None] = mapped_column(Text)
    baseline_summary: Mapped[str | None] = mapped_column(Text)
    observation_window_days: Mapped[int | None] = mapped_column(Integer)
    time_budget_minutes: Mapped[int | None] = mapped_column(Integer)
    success_condition: Mapped[str | None] = mapped_column(Text)
    stop_condition: Mapped[str | None] = mapped_column(Text)
    inconclusive_condition: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "decisions"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    goal_id: Mapped[UUID | None] = mapped_column(ForeignKey("goals.id"), index=True)
    experiment_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiments.id"), index=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    decision_type: Mapped[DecisionType] = mapped_column(
        Enum(
            DecisionType,
            name="decision_type",
            native_enum=False,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class Metric(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "metrics"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    goal_id: Mapped[UUID | None] = mapped_column(ForeignKey("goals.id"), index=True)
    experiment_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiments.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Approval(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "approvals"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCall(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    approval_id: Mapped[UUID | None] = mapped_column(ForeignKey("approvals.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(String(200), nullable=False)
    request_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    response_data: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(50), default="requested", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CostRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cost_records"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_call_id: Mapped[UUID | None] = mapped_column(ForeignKey("tool_calls.id"), index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    incurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Publication(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "publications"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True, nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(ForeignKey("approvals.id"), index=True)
    tool_call_id: Mapped[UUID | None] = mapped_column(ForeignKey("tool_calls.id"), index=True)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
