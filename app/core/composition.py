"""Runtime composition; tests inject a provider without credentials or network."""
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.llm.anthropic import AnthropicProvider
from app.llm.fallback import FallbackProvider
from app.llm.model_router import ModelRouter, ModelRoutingProvider
from app.llm.router_store import effective_router_settings
from app.llm.provider import LLMProvider
from app.llm.service import LLMService
from app.llm.xai import XAIProvider
from app.privacy import PrivacyGateway
from app.skills import SkillLoader
from app.tools import FakeToolGateway
from app.workflows.article_production import ArticleProduction
from app.workflows.daily_growth_review import DailyGrowthReview
from app.workflows.team_cycle import TeamCycle

SAFE_LLM_METADATA_FIELDS = {"reasoning_effort", "team_role", "task_type", "conversation_id", "provider_timeout_seconds", "routing_phase", "routing_complexity", "requires_web_search"}


def build_intake(db: Session, settings: Settings, provider: LLMProvider | None = None):
    from app.intake.service import CompanyIntakeService
    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    return CompanyIntakeService(
        db, products_root=settings.products_root,
        llm=LLMService(privacy_gateway=privacy, provider=provider or build_llm_provider(settings)),
        model=settings.llm_model, provider_name=settings.llm_provider,
    )


def build_llm_provider(settings: Settings) -> LLMProvider:
    settings = effective_router_settings(settings)
    if settings.llm_provider == "anthropic":
        if settings.model_router_enabled:
            raise ValueError("Model Router currently supports only the configured xai-compatible gateway")
        if not settings.anthropic_api_key:
            raise ValueError("Configure ANTHROPIC_API_KEY")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            base_url=settings.anthropic_base_url,
        )
    if settings.llm_provider == "xai":
        if not settings.xai_api_key:
            raise ValueError("Configure XAI_API_KEY")

        def make_provider(model: str) -> XAIProvider:
            return XAIProvider(
                api_key=settings.xai_api_key,
                model=model,
                base_url=settings.xai_base_url,
                reasoning_effort=settings.xai_reasoning_effort,
                timeout_seconds=settings.xai_timeout_seconds,
            )

        primary = make_provider(settings.llm_model)
        if settings.model_router_enabled:
            router = ModelRouter(
                profiles_json=settings.model_router_profiles,
                overrides_json=settings.model_router_overrides,
                max_call_usd=settings.model_router_max_call_usd,
                provider_supports_web_search=False,  # No search-tool adapter in XAIProvider yet.
            )
            return ModelRoutingProvider(provider=primary, router=router)
        fallback_models = []
        for raw in settings.llm_fallback_models.split(","):
            model = raw.strip()
            if model and model != settings.llm_model and model not in fallback_models:
                fallback_models.append(model)
        if not fallback_models:
            return primary
        chain: list[tuple[str, LLMProvider]] = [(settings.llm_model, primary)]
        chain.extend((model, make_provider(model)) for model in fallback_models[:3])
        return FallbackProvider(providers=chain)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def build_article_production(
    db: Session,
    settings: Settings,
    provider: LLMProvider | None = None,
) -> ArticleProduction:
    provider = provider or build_llm_provider(settings)
    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    return ArticleProduction(
        db=db,
        llm=LLMService(privacy_gateway=privacy, provider=provider),
        privacy=privacy,
        products_root=settings.products_root,
        provider_name=settings.llm_provider,
        model=settings.llm_model,
    )


def build_review(
    db: Session,
    settings: Settings,
    provider: LLMProvider | None = None,
    tools: FakeToolGateway | None = None,
) -> DailyGrowthReview:
    provider = provider or build_llm_provider(settings)
    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    return DailyGrowthReview(
        db=db,
        llm=LLMService(privacy_gateway=privacy, provider=provider),
        privacy=privacy,
        skills=SkillLoader(settings.skills_root),
        tools=tools or FakeToolGateway(),
        products_root=settings.products_root,
        provider_name=settings.llm_provider,
        model=settings.llm_model,
        review_skill=settings.review_skill,
    )


def build_team_cycle(
    db: Session,
    settings: Settings,
    provider: LLMProvider | None = None,
) -> TeamCycle:
    provider = provider or build_llm_provider(settings)
    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    return TeamCycle(
        db=db,
        llm=LLMService(privacy_gateway=privacy, provider=provider),
        privacy=privacy,
        products_root=settings.products_root,
        provider_name=settings.llm_provider,
        model=settings.llm_model,
        followup_review=build_review(db, settings, provider),
    )
