from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://ai_growth:ai_growth@localhost:5432/ai_growth"
    llm_provider: str = "xai"
    llm_model: str = "kimi-2.7"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    xai_api_key: str | None = None
    xai_base_url: str = "https://api.claudehub.fun/v1"
    xai_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "low"
    xai_timeout_seconds: float = 300.0
    llm_fallback_models: str = "grok-4.7"
    # Jeff is an optional OpenAI-compatible decision endpoint, never a search adapter.
    jeff_api_key: str | None = None
    jeff_base_url: str | None = None
    jeff_model: str = "jeff"
    intelligence_confidence_threshold: float = Field(default=.8, ge=0, le=1)
    intelligence_campaign_enabled: bool = True
    skills_root: Path = Path("skills")
    products_root: Path = Path("products")
    review_skill: str | None = None
    telegram_bot_token: str | None = None
    telegram_owner_chat_id: int | None = None
    telegram_poll_timeout_seconds: int = 25
    team_worker_batch_size: int = 1
    team_worker_idle_poll_seconds: int = 20
    team_worker_busy_poll_seconds: int = 2
    telegram_proxy_url: str | None = None
    comment_platform_proxy_url: str | None = None
    social_telegram_bot_token: str | None = None
    social_telegram_bot_username: str | None = None
    social_google_client_id: str | None = None
    social_google_client_secret: str | None = None
    social_instagram_client_id: str | None = None
    social_instagram_client_secret: str | None = None
    social_threads_client_id: str | None = None
    social_threads_client_secret: str | None = None
    social_vk_client_id: str | None = None
    social_oauth_redirect_base: str | None = None
    viral_research_mode: Literal["public_multisource", "youtube_public", "disabled"] = "public_multisource"
    viral_research_max_queries: int = 4
    viral_research_max_candidates: int = 16
    viral_research_timeout_seconds: float = 20.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
