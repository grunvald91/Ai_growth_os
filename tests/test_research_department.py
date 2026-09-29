import json

import pytest
from sqlalchemy import select

from app.core.composition import build_team_cycle
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.workflows.research_contracts import ResearchDepartmentReport, ResearchSourceBundle


class RoleProvider:
    def __init__(self, *, voc_available=False):
        self.requests = []
        self.voc_available = voc_available

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        role = request.metadata.get("team_role")
        if role == "segment_researcher":
            data = {
                "summary": "Test a small set of service-business segments.",
                "hypotheses": [{
                    "segment": "Small massage studios",
                    "booking_job": "Coordinate service, optional specialist, slot and confirmation.",
                    "servgo_relevance": "ServGo has a verified booking flow covering those steps.",
                    "evidence_to_support": ["Owners explicitly report this coordination problem."],
                    "evidence_to_kill": ["Target owners say this workflow is already trivial."],
                }],
                "evidence_gaps": ["No segment demand observations yet."],
            }
        elif role == "channel_researcher":
            data = {
                "summary": "Start with channels that can be tested without spend.",
                "hypotheses": [{
                    "channel": "Own-site SEO",
                    "audience_hypothesis": "Some owners may search for ways to structure booking workflows.",
                    "proposed_use": "Publish one grounded diagnostic article with a qualified-lead CTA.",
                    "evidence_to_support": ["Relevant organic visits produce qualified inquiries."],
                    "evidence_to_kill": ["The page gets no qualified business interest."],
                }],
                "evidence_gaps": ["No channel traffic evidence yet."],
            }
        elif role == "problem_researcher":
            data = {
                "summary": "Map only verified product capabilities to problem hypotheses.",
                "hypotheses": [{
                    "problem": "Booking status may remain unclear while payment proof awaits manual verification.",
                    "affected_role_hypothesis": "Owner or administrator",
                    "verified_servgo_capability": "Customer can attach proof and owner manually verifies before confirmation.",
                    "fit_hypothesis": "A pending-confirmation flow may matter to businesses using prepayment.",
                    "evidence_to_support": ["Owners report confusion around payment-proof status."],
                    "evidence_to_kill": ["Owners do not use or care about prepayment verification."],
                }],
                "evidence_gaps": ["No observed problem frequency."],
            }
        elif role == "competitor_voc_researcher":
            assert self.voc_available
            data = {
                "source_status": "evidence_available",
                "summary": "Supplied excerpts contain a repeated integration complaint.",
                "patterns": [{
                    "competitor": "ExampleBook",
                    "problem": "Limited integrations create manual work.",
                    "occurrences_in_bundle": 2,
                    "source_urls": [
                        "https://reviews.example/a",
                        "https://reviews.example/b",
                    ],
                    "implication_hypothesis": "Integration gaps may matter for some growing businesses.",
                    "servgo_check": "Do not claim superiority; verify which integrations ServGo actually supports.",
                }],
                "source_requests": [],
                "evidence_gaps": ["Small supplied review sample."],
            }
        elif role == "research_lead":
            data = {
                "summary": "Test one service-business segment and one organic path before scaling.",
                "department_status": "complete" if self.voc_available else "partial",
                "competitor_voc_status": "evidence_available" if self.voc_available else "needs_sources",
                "top_opportunities": [{
                    "segment_hypothesis": "Small massage studios",
                    "problem_hypothesis": "Manual confirmation around prepayment may create friction.",
                    "channel_hypothesis": "Own-site SEO diagnostic article.",
                    "competitor_signal": (
                        "Supplied reviews mention integration-related manual work."
                        if self.voc_available else
                        "No competitor review evidence supplied yet."
                    ),
                    "servgo_relevance": "ServGo has a verified customer-proof plus manual-owner-confirmation flow.",
                    "proposed_test": "Run one grounded segment/problem/channel test.",
                    "evidence_to_support": ["Qualified business inquiries reference the problem."],
                    "evidence_to_kill": ["Target owners reject the problem premise."],
                }],
                "recommended_test": "Run one small falsifiable segment/problem/channel test.",
                "product_questions": ["Which integrations are verified in ServGo?"],
                "evidence_gaps": [] if self.voc_available else ["Competitor review sources are missing."],
            }
        else:
            raise AssertionError(f"Unexpected role {role}")
        return LLMResponse(
            content=json.dumps(data),
            model="fake",
            usage=LLMUsage(input_tokens=10, output_tokens=20),
            stop_reason="end_turn",
        )


def add_research_task(db, product):
    task = Task(
        product_id=product.id,
        title="Research organic segment and channel hypotheses",
        description="Research before testing.",
        assigned_agent="research_pmm",
        status=TaskStatus.QUEUED,
        priority=90,
    )
    db.add(task)
    db.flush()
    db.add(Artifact(
        product_id=product.id,
        task_id=task.id,
        kind="task_brief",
        title=task.title,
        content=json.dumps({
            "decision_id": None,
            "title": task.title,
            "description": task.description,
            "assigned_agent": "research_pmm",
            "task_type": "research",
            "skill_name": None,
            "priority": 90,
            "operation": None,
            "requires_approval": False,
        }),
    ))
    db.commit()
    return task


@pytest.fixture
def product(db):
    row = Product(name="ServGo", slug="servgo")
    db.add(row)
    db.commit()
    return row


@pytest.mark.asyncio
async def test_research_department_fans_out_and_synthesizes_partial_without_sources(db, product, tmp_path):
    parent = add_research_task(db, product)
    provider = RoleProvider(voc_available=False)
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, max_tasks=1, followup_cmo=False)

    assert result.completed_task_ids == [parent.id]
    assert len(provider.requests) == 4
    children = list(db.scalars(select(Task).where(Task.parent_id == parent.id)))
    assert len(children) == 4
    assert {child.assigned_agent for child in children} == {
        "segment_researcher", "channel_researcher", "problem_researcher", "competitor_voc_researcher"
    }
    assert all(child.status == TaskStatus.COMPLETED for child in children)
    research = db.get(Artifact, result.artifact_ids[0])
    report = ResearchDepartmentReport.model_validate_json(research.content)
    assert report.department_status == "partial"
    assert report.competitor_voc_status == "needs_sources"
    assert len(report.worker_task_ids) == len(report.worker_artifact_ids) == 4
    voc = db.scalar(select(Artifact).where(Artifact.kind == "competitor_voc_research"))
    assert json.loads(voc.content)["patterns"] == []


@pytest.mark.asyncio
async def test_research_department_uses_supplied_review_sources(db, product, tmp_path):
    parent = add_research_task(db, product)
    bundle = ResearchSourceBundle.model_validate({
        "label": "Competitor review sample",
        "sources": [
            {
                "url": "https://reviews.example/a",
                "title": "Review A",
                "platform": "ExampleReviews",
                "competitor": "ExampleBook",
                "excerpt": "Integration limitations create manual work.",
                "rating": 3,
            },
            {
                "url": "https://reviews.example/b",
                "title": "Review B",
                "platform": "ExampleReviews",
                "competitor": "ExampleBook",
                "excerpt": "Extra manual work remains because integrations are limited.",
                "rating": 3,
            },
        ],
    })
    db.add(Artifact(
        product_id=product.id,
        kind="research_source_bundle",
        title=bundle.label,
        content=bundle.model_dump_json(),
    ))
    db.commit()
    provider = RoleProvider(voc_available=True)
    cycle = build_team_cycle(db, Settings(products_root=tmp_path), provider)

    result = await cycle.run(product.id, max_tasks=1, followup_cmo=False)

    assert result.completed_task_ids == [parent.id]
    assert len(provider.requests) == 5
    research = db.get(Artifact, result.artifact_ids[0])
    report = ResearchDepartmentReport.model_validate_json(research.content)
    assert report.department_status == "complete"
    assert report.competitor_voc_status == "evidence_available"
    voc_request = next(
        r for r in provider.requests
        if r.metadata.get("team_role") == "competitor_voc_researcher"
    )
    assert "https://reviews.example/a" in voc_request.messages[0].content
