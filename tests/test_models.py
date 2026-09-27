from app.db.models import Base
from app.models import (
    AgentRun,
    Approval,
    Artifact,
    CostRecord,
    Decision,
    Experiment,
    Goal,
    Metric,
    Product,
    Publication,
    Task,
    ToolCall,
)


def test_required_models_are_importable_and_mapped() -> None:
    expected = {"products", "goals", "tasks", "agent_runs", "artifacts", "decisions", "experiments", "metrics", "approvals", "tool_calls", "cost_records", "publications"}
    assert expected == set(Base.metadata.tables)
    assert all(model.__tablename__ in Base.metadata.tables for model in (Product, Goal, Task, AgentRun, Artifact, Decision, Experiment, Metric, Approval, ToolCall, CostRecord, Publication))
