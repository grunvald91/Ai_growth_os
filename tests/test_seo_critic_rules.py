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
