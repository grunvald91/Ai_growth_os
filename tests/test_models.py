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
    DispatcherWorkflow,
    DispatcherWorkflowStep,
)


def test_required_models_are_importable_and_mapped() -> None:
    expected = {"brand_assets", "brand_kit_versions", "brand_kit_active", "brand_backgrounds", "brand_compositions", "brand_renders", "products", "goals", "tasks", "agent_runs", "artifacts", "decisions", "experiments", "prospects", "metrics", "approvals", "tool_calls", "cost_records", "publications", "comment_channels", "business_comments", "dispatcher_conversations", "dispatcher_messages", "dispatcher_workflows", "dispatcher_workflow_steps", "studio_business_links", "studio_sso_tickets", "insight_settings", "voice_usage", "voice_cost_alerts", "seo_factory_activations", "ai_chat_conversations", "ai_chat_messages", "ai_chat_attachments", "ai_chat_requests"}
    assert expected == set(Base.metadata.tables)
    assert all(model.__tablename__ in Base.metadata.tables for model in (Product, Goal, Task, AgentRun, Artifact, Decision, Experiment, Metric, Approval, ToolCall, CostRecord, Publication, DispatcherWorkflow, DispatcherWorkflowStep))
