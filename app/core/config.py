from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://ai_growth:ai_growth@localhost:5432/ai_growth"
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5-5"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    xai_api_key: str | None = None
    xai_base_url: str = "https://api.claudehub.fun/v1"
    xai_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    xai_timeout_seconds: float = 300.0
    skills_root: Path = Path("skills")
    products_root: Path = Path("products")
    review_skill: str | None = None
    telegram_bot_token: str | None = None
    telegram_owner_chat_id: int | None = None
    telegram_poll_timeout_seconds: int = 25
    team_worker_batch_size: int = 3
    team_worker_idle_poll_seconds: int = 20
    team_worker_busy_poll_seconds: int = 2
    telegram_proxy_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
