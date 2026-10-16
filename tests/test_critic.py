"""Tests for subordinate critics: strict scope, bounded calls and author veto."""
import json

import pytest
from pydantic import BaseModel, Field

from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.privacy import PrivacyGateway


class Draft(BaseModel):
    title: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return LLMResponse(content=json.dumps(output), model="fake",
                           usage=LLMUsage(input_tokens=10, output_tokens=20),
                           stop_reason="end_turn")


def task():
    return LLMRequest(
        system_prompt="Only prepare onboarding steps relevant to the user's goal.",
        messages=[LLMMessage(role="user", content="Plan customer onboarding.")],
        response_schema=Draft.model_json_schema(), model="fake", max_tokens=600,
        metadata={"team_role": "content_strategist", "task_type": "content_plan"},
    )


def finding(severity="major"):
    return {"findings": [{
        "id": "m1", "severity": severity, "problem": "No onboarding step described",
        "evidence_in_draft": "Output has only an acquisition step",
        "proposed_fix": "Add the onboarding action as a step",
        "link_to_original_goal": "The request is specifically about onboarding",
    }]}


async def run(provider, *, enabled=True):
    return await LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                            critic_enabled=enabled).generate(task(), validate_revision=lambda data: Draft.model_validate(data))


@pytest.mark.asyncio
async def test_critic_can_recommend_one_goal_aligned_correction():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Traffic"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "The user explicitly requested onboarding"}],
         "updated_output": {"title": "Plan", "steps": ["Traffic", "Onboarding"]}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Traffic", "Onboarding"]
    assert result.quality_review["status"] == "revised"
    assert result.quality_review["extra_calls"] == 2
    assert result.usage.input_tokens == 30
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_no_material_findings_do_not_trigger_rework():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}, {"findings": []}])
    result = await run(provider)
    assert result.quality_review["status"] == "no_material_issues"
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_minor_findings_are_not_mandatory():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}, finding("minor")])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Onboarding"]
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_author_rejects_critic_and_preserves_original():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "reject",
                        "rationale": "Already sufficiently covered"}],
         "updated_output": {"title": "Unwanted", "steps": ["Unrelated"]}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["title"] == "Plan"
    assert result.quality_review["status"] == "all_rejected"


@pytest.mark.asyncio
async def test_invalid_revision_falls_back_to_valid_original():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "Material improvement"}],
         "updated_output": {"title": "Oops"}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Onboarding"]
    assert result.quality_review["status"] == "revision_unavailable"
    assert result.quality_review["findings"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_critic_outage_does_not_break_original_task():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, TimeoutError("not available"),
    ])
    result = await run(provider)
    assert result.quality_review["status"] == "review_unavailable"
    assert json.loads(result.content)["title"] == "Plan"


@pytest.mark.asyncio
async def test_disabled_default_does_not_add_calls():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}])
    result = await run(provider, enabled=False)
    assert len(provider.requests) == 1
    assert result.quality_review is None


@pytest.mark.asyncio
async def test_invalid_initial_draft_is_not_reviewed():
    provider = SequenceProvider([{"title": "Plan"}])
    result = await run(provider)
    assert len(provider.requests) == 1
    assert result.quality_review["status"] == "skipped_invalid_original"
    assert result.quality_review["extra_calls"] == 0


@pytest.mark.asyncio
async def test_review_revision_schema_reuses_original_contract():
    from jsonschema import Draft202012Validator
    provider = SequenceProvider([
        {"title": "Draft", "steps": ["Traffic"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "Onboarding is essential"}],
         "updated_output": {"title": "Draft", "steps": ["Onboarding"]}},
    ])
    await run(provider)
    sent = provider.requests[2].response_schema
    assert sent["properties"]["updated_output"] == task().response_schema
    Draft202012Validator.check_schema(sent)
    Draft202012Validator(sent).validate({
        "decisions": [{"finding_id": "m1", "decision": "accept",
                       "rationale": "Onboarding is essential"}],
        "updated_output": {"title": "Draft", "steps": ["Onboarding"]},
    })


@pytest.mark.asyncio
async def test_large_context_skips_review_without_dropping_constraints():
    provider = SequenceProvider([
        {"title": "Draft", "steps": ["Onboarding"]},
        {"findings": []},
    ])
    request = task()
    request.messages[0].content = json.dumps({
        "task": {"title": "Onboarding steps", "description": "Explain onboarding"},
        "task_brief": {"target": "New customers", "constraints": ["No paid ads", "Telegram only"]},
        "product": {"name": "Demo company"},
        "stable_product_context": "Verified information about the company",
        "available_records": {"goal": [], "metric": []},
        "context_limits": {"max_items": 8},
        "irrelevant_history": "example " * 9000,
    })
    result = await LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                              critic_enabled=True).generate(request)
    assert len(provider.requests) == 1
    assert result.quality_review["status"] == "skipped_incomplete_context"


@pytest.mark.asyncio
async def test_more_than_three_material_findings_all_get_author_decisions():
    # A fixed cap must never hide additional material failures.
    original = {"title": "Plan", "steps": ["Traffic"]}
    problems = [
        {"id": f"m{i}", "severity": "critical" if i % 2 else "major",
         "problem": f"Missing essential onboarding requirement number {i}",
         "evidence_in_draft": "Draft contains only Traffic",
         "proposed_fix": f"Add an actionable onboarding requirement {i}",
         "link_to_original_goal": "All eight are explicitly requested onboarding needs"}
        for i in range(1, 9)
    ]
    revised = {"title": "Plan", "steps": ["Traffic", *[f"Onboarding {i}" for i in range(1, 9)]]}
    responses = [
        original, {"findings": problems},
        {"decisions": [
            {"finding_id": f"m{i}", "decision": "accept",
             "rationale": "Essential to the approved onboarding goal"}
            for i in range(1, 9)
        ], "updated_output": revised},
    ]
    provider = SequenceProvider(responses)
    result = await run(provider)
    assert result.quality_review["status"] == "revised"
    assert len(result.quality_review["findings"]) == 8
    assert len(result.quality_review["decisions"]) == 8
    assert json.loads(result.content) == revised
    review_schema = provider.requests[1].response_schema
    decisions_schema = provider.requests[2].response_schema
    assert review_schema["properties"]["findings"].get("maxItems") is None
    assert decisions_schema["properties"]["decisions"].get("maxItems") is None
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_multiple_findings_mixed_accept_reject_keep_author_authority():
    problems = [
        {"id": f"issue{i}", "severity": "major",
         "problem": "Missing material requirement for the approved task",
         "evidence_in_draft": "Only the first step appears",
         "proposed_fix": "Add the required step only if actually absent",
         "link_to_original_goal": "An approved customer onboarding objective"}
        for i in range(6)
    ]
    decisions = [
        {"finding_id": f"issue{i}", "decision": "accept" if i % 2 == 0 else "reject",
         "rationale": "Checked against the original objective and existing draft"}
        for i in range(6)
    ]
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]},
        {"findings": problems},
        {"decisions": decisions,
         "updated_output": {"title": "Plan", "steps": ["Onboarding", "Followup"]}},
    ])
    result = await run(provider)
    assert result.quality_review["status"] == "revised"
    assert [d["decision"] for d in result.quality_review["decisions"]] == [
        "accept", "reject", "accept", "reject", "accept", "reject"
    ]
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_missing_one_author_decision_rejects_partial_revision():
    problems = [
        {"id": f"issue{i}", "severity": "major",
         "problem": "The planned onboarding stage is incomplete",
         "evidence_in_draft": "No confirmation stage appears",
         "proposed_fix": "Include a confirmed next-step stage",
         "link_to_original_goal": "Original goal requires confirmed onboarding"}
        for i in range(6)
    ]
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Before"]},
        {"findings": problems},
        {"decisions": [
            {"finding_id": f"issue{i}", "decision": "accept",
             "rationale": "Essential for the onboarding objective"}
            for i in range(5)
        ], "updated_output": {"title": "Plan", "steps": ["After"]}},
    ])
    result = await run(provider)
    assert result.quality_review["status"] == "revision_unavailable"
    assert json.loads(result.content)["steps"] == ["Before"]


@pytest.mark.asyncio
async def test_original_schema_with_nested_refs_can_be_validated_after_revision():
    from jsonschema import Draft202012Validator
    class Step(BaseModel):
        label: str = Field(min_length=1)

    class NestedDraft(BaseModel):
        title: str
        steps: list[Step]

    request = task()
    request.response_schema = NestedDraft.model_json_schema()
    provider = SequenceProvider([
        {"title": "Plan", "steps": [{"label": "Research"}]},
        finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "Necessary for the original onboarding goal"}],
         "updated_output": {"title": "Plan", "steps": [{"label": "Onboarding"}]}},
    ])
    result = await LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                              critic_enabled=True).generate(
                                  request, validate_revision=lambda data: NestedDraft.model_validate(data)
                              )
    assert result.quality_review["status"] == "revised"
    schema = provider.requests[2].response_schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({
        "decisions": [{"finding_id": "m1", "decision": "accept",
                       "rationale": "Necessary for onboarding"}],
        "updated_output": json.loads(result.content),
    })


@pytest.mark.asyncio
async def test_compaction_keeps_quick_post_company_restrictions():
    # Regression: a ~12k payload once collapsed to ~195 chars and lost
    # a company's explicit "no discounts" policy.
    request = task()
    request.metadata["task_type"] = "quick_post"
    original_payload = {
        "company_name": "Test cafe", "topic": "Customer loyalty",
        "audience": "Local guests", "brief": "Useful post",
        "channel": "telegram", "channel_guidance": "Friendly",
        "verified_facts": {}, "company_context":
            "Brand constraints: NEVER offer discounts or promotions.",
        "prior_notes": "x" * 12500,
    }
    request.messages[0].content = json.dumps(original_payload)
    provider = SequenceProvider([
        {"title": "No discounts", "steps": ["Respect policies"]}, {"findings": []},
    ])
    result = await LLMService(
        privacy_gateway=PrivacyGateway(), provider=provider,
        critic_enabled=True).generate(request)
    assert result.quality_review["status"] == "no_material_issues"
    reviewer_task = json.loads(provider.requests[1].messages[0].content)["task"]
    safe_payload = json.loads(reviewer_task["original_task"][0]["content"])
    assert safe_payload["company_context"] == original_payload["company_context"]
    assert "discounts" in safe_payload["company_context"]
    assert safe_payload["prior_notes"] == original_payload["prior_notes"]


@pytest.mark.asyncio
async def test_oversized_mandatory_company_policy_skips_critic_entirely():
    request = task()
    request.metadata["task_type"] = "creative_brief"
    request.messages[0].content = json.dumps({
        "company": "Test", "topic": "Launch", "audience": "Visitors",
        "editorial_brief": "A launch post", "format": "story",
        "platform": "telegram", "verified_product_facts": {},
        "company_profile": "Deny discounts. " + "X" * 36000,
    })
    provider = SequenceProvider([{"title": "Plan", "steps": ["Step"]}])
    result = await LLMService(
        privacy_gateway=PrivacyGateway(), provider=provider,
        critic_enabled=True).generate(request)
    assert len(provider.requests) == 1
    assert result.quality_review["status"] == "skipped_incomplete_context"


@pytest.mark.asyncio
async def test_each_completed_call_is_observed_before_critic_cancellation():
    import asyncio
    class CancelInReview:
        def __init__(self):
            self.calls = 0
        async def generate(self, request):
            self.calls += 1
            if self.calls == 2:
                raise asyncio.CancelledError()
            return LLMResponse(
                content=json.dumps({"title":"Plan","steps":["Step"]}),
                model="fake", usage=LLMUsage(input_tokens=24, output_tokens=34),
            )
    provider = CancelInReview()
    observed = []
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                     critic_enabled=True)
    with pytest.raises(asyncio.CancelledError):
        await llm.generate(task(), on_observed=lambda stage, value:
                           observed.append((stage, value.usage.input_tokens,
                                            value.usage.output_tokens)))
    assert observed == [("author", 24, 34)]


@pytest.mark.asyncio
async def test_validated_major_finding_persisted_before_revision_is_cancelled():
    """The critic's finding must be durably observable before cancelled author work."""
    import asyncio

    class CancelledRevision:
        def __init__(self):
            self.requests = []
        async def generate(self, request):
            self.requests.append(request)
            if request.metadata.get("task_type") == "apply_quality_feedback":
                raise asyncio.CancelledError()
            payload = (finding() if request.metadata.get("task_type") == "quality_review"
                       else {"title": "Plan", "steps": ["Traffic"]})
            return LLMResponse(
                content=json.dumps(payload), model="fake",
                usage=LLMUsage(input_tokens=24, output_tokens=34),
                stop_reason="end_turn",
            )

    provider = CancelledRevision()
    observed = []
    reviews = []
    with pytest.raises(asyncio.CancelledError):
        await LLMService(
            privacy_gateway=PrivacyGateway(), provider=provider,
            critic_enabled=True,
        ).generate(
            task(),
            validate_revision=lambda data: Draft.model_validate(data),
            on_observed=lambda stage, reply: observed.append(
                (stage, reply.usage.input_tokens, reply.usage.output_tokens)),
            on_review_progress=lambda reply: reviews.append(reply.quality_review),
        )
    assert len(provider.requests) == 3
    assert [stage for stage, _, _ in observed] == ["author", "critic"]
    assert [item["status"] for item in reviews] == [
        "revision_pending", "revision_cancelled"
    ]
    assert all(item["findings"][0]["id"] == "m1" for item in reviews)
    assert all(item["findings"][0]["severity"] == "major" for item in reviews)
    assert reviews[1]["usage_unknown_for_failed_calls"] is True


@pytest.mark.asyncio
async def test_invalid_critic_report_is_never_logged_as_validated():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Traffic"]},
        {"findings": [{"id": "m1", "severity": "major", "problem": "Incomplete"}]},
    ])
    audit = []
    result = await LLMService(
        privacy_gateway=PrivacyGateway(), provider=provider,
        critic_enabled=True,
    ).generate(
        task(), validate_revision=lambda data: Draft.model_validate(data),
        on_review_progress=lambda response: audit.append(response.quality_review),
    )
    assert result.quality_review["status"] == "review_unavailable"
    assert audit == []


@pytest.mark.asyncio
async def test_failed_intermediate_audit_does_not_silently_start_revision():
    class Provider:
        def __init__(self):
            self.requests = []
        async def generate(self, request):
            self.requests.append(request)
            payload = finding() if len(self.requests) == 2 else {
                "title": "Plan", "steps": ["Traffic"],
            }
            return LLMResponse(content=json.dumps(payload), model="fake",
                               usage=LLMUsage(input_tokens=1, output_tokens=1),
                               stop_reason="end_turn")
    provider = Provider()

    def fail_audit(_response):
        raise OSError("database unavailable")

    from app.llm.critic import ReviewAuditPersistenceError
    with pytest.raises(ReviewAuditPersistenceError):
        await LLMService(
            privacy_gateway=PrivacyGateway(), provider=provider,
            critic_enabled=True,
        ).generate(
            task(), validate_revision=lambda data: Draft.model_validate(data),
            on_review_progress=fail_audit,
        )
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_critic_context_limit_is_configurable():
    request = task()
    request.messages[0].content = json.dumps({"brief": "x" * 30000})

    default = await LLMService(
        privacy_gateway=PrivacyGateway(),
        provider=SequenceProvider([{"title": "T", "steps": ["s"]}]),
        critic_enabled=True, critic_limits={}).generate(request)
    assert default.quality_review["status"] == "skipped_incomplete_context"

    provider = SequenceProvider([{"title": "T", "steps": ["s"]}, {"findings": []}])
    service = LLMService(
        privacy_gateway=PrivacyGateway(), provider=provider, critic_enabled=True,
        critic_limits={"max_context_chars": 120000, "review_timeout": 180, "revision_timeout": 240})
    raised = await service.generate(request)
    assert raised.quality_review["status"] == "no_material_issues"
    assert provider.requests[1].metadata["provider_timeout_seconds"] == 180
    assert service.extra_timeout_seconds == 425
