"""SEO factual and intent review is additional to the normal one-pass critic."""
from app.llm.critic import _critic_prompt, _author_prompt
from app.llm.schemas import LLMMessage, LLMRequest


def test_seo_critic_flags_wrong_audience_facts_and_unreadable_steps():
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="draft")],
        metadata={"task_type": "article_production", "team_role": "marketing_editor",
                  "seo_article": True},
    )
    prompt = _critic_prompt(request)
    assert "seo_brief.primary_query" in prompt
    assert "verified_product_facts" in prompt
    assert "нумерованный" in prompt
    assert "фактические ошибки" in prompt
    assert "нечитаемость" in prompt
    revision = _author_prompt(request)
    assert "разбей перечисления" in revision


def test_non_seo_critic_unchanged():
    request = LLMRequest(messages=[LLMMessage(role="user", content="draft")],
                         metadata={"task_type": "article_production", "team_role": "marketing_editor"})
    assert "seo_brief.primary_query" not in _critic_prompt(request)

def test_servgo_seo_critic_checks_manual_schedule_not_online_only():
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="draft")],
        metadata={"task_type": "article_production", "team_role": "marketing_editor",
                  "seo_article": True, "company_slug": "servgo"},
    )
    assert "онлайн-запись как обязательная" in _critic_prompt(request)
    other = request.model_copy(update={"metadata": {**request.metadata, "company_slug": "other"}})
    assert "онлайн-запись как обязательная" not in _critic_prompt(other)


def test_servgo_company_profile_contains_proven_owner_calendar_facts():
    import json
    from pathlib import Path
    from app.company_profile import CompanyProfile, render_company_profile
    from app.seo.planner import SEED_PROMPT, PLAN_PROMPT
    from app.workflows.article_production import SERVGO_EDITORIAL_RULES

    file = Path(__file__).resolve().parents[1] / "products/servgo/company-profile.json"
    profile = CompanyProfile.model_validate(json.loads(file.read_text(encoding="utf-8")))
    facts = [fact.statement for fact in profile.proof.approved_facts]
    assert any("визуальное расписание" in fact for fact in facts)
    assert any("запись вручную" in fact for fact in facts)
    assert "В админ-панели ServGo" in render_company_profile(profile)
    assert "owner-managed appointment journals" in SEED_PROMPT
    assert "appointment journal" in PLAN_PROMPT
    assert "внутренний электронный журнал" in SERVGO_EDITORIAL_RULES
