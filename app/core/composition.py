"""Runtime composition; tests inject a provider without credentials or network."""
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.llm.anthropic import AnthropicProvider
from app.llm.provider import LLMProvider
from app.llm.service import LLMService
from app.privacy import PrivacyGateway
from app.skills import SkillLoader
from app.tools import FakeToolGateway
from app.workflows.daily_growth_review import DailyGrowthReview


def build_review(db: Session, settings: Settings, provider: LLMProvider | None = None,
                 tools: FakeToolGateway | None = None) -> DailyGrowthReview:
    if provider is None:
        if settings.llm_provider != 'anthropic' or not settings.anthropic_api_key:
            raise ValueError('Configure a supported provider and credentials')
        provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.llm_model,
                                     base_url=settings.anthropic_base_url)
    privacy = PrivacyGateway(allowed_context_fields=set())
    return DailyGrowthReview(db=db, llm=LLMService(privacy_gateway=privacy, provider=provider),
        privacy=privacy, skills=SkillLoader(settings.skills_root), tools=tools or FakeToolGateway(),
        products_root=settings.products_root, provider_name=settings.llm_provider,
        model=settings.llm_model, review_skill=settings.review_skill)
