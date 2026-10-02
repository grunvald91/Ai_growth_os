import json

import pytest
from sqlalchemy import select

from app.core.composition import build_team_cycle
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return LLMResponse(
            content=output if isinstance(output, str) else json.dumps(output),
            model="fake",
            usage=LLMUsage(input_tokens=10, output_tokens=20),
            stop_reason="end_turn",
        )


@pytest.fixture
def product(db):
    row = Product(name="ServGo", slug="servgo")
    db.add(row)
    db.commit()
    return row


def add_task(db, product, *, role, task_type, title, priority=0, operation=None, requires_approval=False):
    task = Task(
        product_id=product.id,
        title=title,
        description=f"Do {task_type} safely",
        assigned_agent=role,
        status=TaskStatus.QUEUED,
        priority=priority,
    )
    db.add(task)
    db.flush()
    db.add(Artifact(
        product_id=product.id,
        task_id=task.id,
        kind="task_brief",
        title=title,
        content=json.dumps({
            "decision_id": None,
            "title": title,
            "description": task.description,
            "assigned_agent": role,
            "task_type": task_type,
            "skill_name": None,
            "priority": priority,
            "operation": operation,
            "requires_approval": requires_approval,
        }),
    ))
    db.commit()
    return task


def analytics_output():
    stages = []
    for stage in ["content", "traffic", "lead", "activation", "customer"]:
        stages.append({
            "stage": stage,
            "event": f"{stage}_event",
            "definition": f"Observed {stage} event.",
            "required_fields": ["source", "occurred_at"],
            "evidence_needed": f"Persist actual {stage} observations.",
        })
    return {
        "summary": "Persist a minimal organic funnel.",
        "qualified_lead_definition": "A service-business owner who explicitly asks to evaluate ServGo.",
        "stages": stages,
        "attribution_notes": "Keep source and experiment identifiers; do not infer missing attribution.",
        "missing_evidence": ["No real funnel events exist yet."],
    }


def content_output():
    return {
        "summary": "Draft one grounded article brief.",
        "format": "article",
        "audience_hypothesis": "Owners of appointment-based service businesses.",
        "problem_hypothesis": "Manual booking coordination may create avoidable friction.",
        "angle": "Map the booking flow before choosing automation.",
        "title_options": ["A booking workflow checklist", "Where booking friction hides"],
        "outline": ["Map the current flow", "Identify manual handoffs", "Define a small test"],
        "cta": "Request a ServGo demo to compare the workflow.",
        "verified_facts_to_use": [],
        "hypotheses_to_label": ["The segment may value reduced coordination."],
        "proof_needed": ["Collect real lead and activation evidence."],
    }


@pytest.mark.asyncio
async def test_team_cycle_executes_nonresearch_specialists_and_persists_results(db, product, tmp_path):
    analytics = add_task(db, product, role="analytics", task_type="analytics", title="Analytics", priority=80)
    content = add_task(db, product, role="content_seo", task_type="article_brief", title="Article brief", priority=50)
    provider = SequenceProvider([analytics_output(), content_output()])
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, max_tasks=10, followup_cmo=False)

    assert result.failed_task_ids == result.skipped_task_ids == []
    assert result.completed_task_ids == [analytics.id, content.id]
    assert len(result.artifact_ids) == len(result.run_ids) == len(provider.requests) == 2
    assert {db.get(Artifact, aid).kind for aid in result.artifact_ids} == {
        "measurement_spec", "article_brief"
    }
    assert all(db.get(Task, tid).status == TaskStatus.COMPLETED for tid in result.completed_task_ids)
    assert all(db.get(AgentRun, rid).status == "completed" for rid in result.run_ids)
    assert len(list(db.scalars(select(CostRecord)))) == 2


@pytest.mark.asyncio
async def test_bad_specialist_output_fails_one_task_and_continues(db, product, tmp_path):
    first = add_task(db, product, role="content_seo", task_type="article_brief", title="Bad content", priority=90)
    second = add_task(db, product, role="analytics", task_type="analytics", title="Good analytics", priority=80)
    provider = SequenceProvider(["not-json", "still-not-json", analytics_output()])
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, followup_cmo=False)

    assert result.failed_task_ids == [first.id]
    assert result.completed_task_ids == [second.id]
    assert db.get(Task, first.id).status == TaskStatus.FAILED
    assert db.get(Task, second.id).status == TaskStatus.COMPLETED
    assert len(result.artifact_ids) == 1


@pytest.mark.asyncio
async def test_invalid_structured_output_gets_one_repair_attempt(db, product, tmp_path):
    task = add_task(
        db,
        product,
        role="content_seo",
        task_type="article_brief",
        title="Repair content",
    )
    provider = SequenceProvider([
        {"wrong": "shape"},
        content_output(),
    ])
    cycle = build_team_cycle(
        db,
        Settings(
            _env_file=None,
            products_root=tmp_path,
            llm_model="gemini-flash-3.8",
            llm_fallback_models="",
        ),
        provider,
    )

    result = await cycle.run(product.id, followup_cmo=False)

    assert result.completed_task_ids == [task.id]
    assert result.failed_task_ids == []
    assert len(provider.requests) == 2
    assert provider.requests[0].response_schema is not None
    assert provider.requests[1].response_schema is not None
    assert "Repair the previous JSON" in provider.requests[1].system_prompt
    artifact = db.get(Artifact, result.artifact_ids[0])
    assert artifact.kind == "article_brief"


@pytest.mark.asyncio
async def test_external_action_and_unknown_task_are_skipped_without_llm(db, product, tmp_path):
    external = add_task(db, product, role="distribution", task_type="distribution_plan",
                        title="Publish", operation="publish", requires_approval=True)
    unknown = add_task(db, product, role="research_pmm", task_type="unknown", title="Unknown")
    provider = SequenceProvider([])
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, followup_cmo=False)

    assert set(result.skipped_task_ids) == {external.id, unknown.id}
    assert provider.requests == []
    assert db.get(Task, external.id).status == TaskStatus.QUEUED
    assert db.get(Task, unknown.id).status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_followup_cmo_receives_specialist_artifact(db, product, tmp_path):
    analytics = add_task(db, product, role="analytics", task_type="analytics", title="Analytics")
    cmo = {
        "summary": "Analytics is now available; define the next test.",
        "decisions": [{
            "decision_type": "TEST",
            "rationale": "Use the new measurement artifact as input.",
            "evidence_references": [],
            "next_tasks": [{
                "title": "Draft content brief",
                "description": "Prepare one grounded brief.",
                "assigned_agent": "content_seo",
                "task_type": "article_brief",
                "priority": 50,
                "operation": None,
                "requires_approval": False,
            }],
        }],
    }
    provider = SequenceProvider([analytics_output(), cmo])
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, followup_cmo=True)

    assert result.completed_task_ids == [analytics.id]
    assert result.followup_review is not None
    assert result.followup_review.status == "completed"
    assert result.followup_review.task_ids
    assert len(provider.requests) == 2
    assert "measurement_spec" in provider.requests[1].messages[0].content


@pytest.mark.asyncio
async def test_content_task_requires_matching_format(db, product, tmp_path):
    task = add_task(db, product, role="content_seo", task_type="article_brief", title="Article")
    wrong = content_output()
    wrong["format"] = "video"
    provider = SequenceProvider([wrong])
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, followup_cmo=False)

    assert result.failed_task_ids == [task.id]
    assert result.artifact_ids == []
