from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    app_env: str = "development"
    release_sha: str = ""
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
    openrouter_api_key: str | None = None
    # Optional: company registry lookup by ИНН/ОГРН during company intake.
    dadata_api_key: str | None = None
    whiteboard_storyboard_model: str = ""
    # Company data collection: if an attempt gives no usable result, models of OTHER providers are
    # tried in this order, up to the attempt limit (the first attempt is the normal model).
    # Company intake tries these in turn (one per provider) until one gives a usable result;
    # models chosen for "company_intake" on the team page go first.
    intake_models: str = "deepseek-v4.1-flash,kimi-2.7,grok-4.7,claude-sonnet-5-5"
    intake_max_attempts: int = Field(default=3, ge=1, le=5)
    # Tried in this order when the storyboard model gets no answer from the provider.
    whiteboard_storyboard_fallback_models: str = "kimi-k3,grok-4.7,claude-sonnet-5-5"
    whiteboard_image_model: str = ""
    whiteboard_speech_model: str = ""
    whiteboard_regions_model: str = ""
    whiteboard_voice: str = "Kore"
    whiteboard_proxy_url: str | None = None
    # Automatic continuations after a transient failure (0 disables), inside the approved call limit.
    whiteboard_auto_resume_attempts: int = Field(default=5, ge=0, le=5)
    # Jeff is an optional OpenAI-compatible decision endpoint, never a search adapter.
    jeff_api_key: str | None = None
    jeff_base_url: str | None = None
    jeff_model: str = "jeff"
    intelligence_confidence_threshold: float = Field(default=.8, ge=0, le=1)
    intelligence_campaign_enabled: bool = True
    # Disabled until explicitly enabled for a measured rollout; review adds LLM calls.
    agent_critic_enabled: bool = False
    # Critic bounds. The reviewer sees the complete task, so oversized contexts
    # are skipped rather than compacted; raise these together with the model.
    agent_critic_max_context_chars: int = Field(default=24000, ge=1000, le=2_000_000)
    agent_critic_review_timeout_seconds: int = Field(default=45, ge=5, le=900)
    agent_critic_revision_timeout_seconds: int = Field(default=75, ge=5, le=900)
    # Opt-in, operator-verified LLM routing; unverified models stay disabled.
    model_router_enabled: bool = False
    model_router_admin_token: str | None = None
    model_router_profiles: str = (
        '[{"model":"kimi-2.7","tier":"economy"},'
        '{"model":"deepseek-v4.1-flash","tier":"economy"},'
        '{"model":"grok-4.7","tier":"balanced"},'
        '{"model":"kimi-k3","tier":"balanced","enabled":false},'
        '{"model":"claude-sonnet-5-5","tier":"balanced","enabled":false},'
        '{"model":"claude-opus-5-5","tier":"premium","enabled":false}]'
    )
    model_router_overrides: str = "{}"
    model_router_max_call_usd: str | None = None
    skills_root: Path = Path("skills")
    products_root: Path = Path("products")
    # Companies (slugs, comma-separated) whose Studio home is the dispatcher. The previous home stays at /classic.
    dispatcher_enabled_slugs: str = "*"  # "*" = every company; or a comma-separated list of slugs
    review_skill: str | None = None
    studio_admin_username: str | None = None
    studio_admin_password: str | None = None
    studio_public_origin: str | None = None
    studio_htpasswd_file: Path | None = None
    studio_users_file: Path | None = None
    # Optional; without it a private key file is created next to the accounts file.
    studio_session_secret: str | None = None
    # Sign-in from the ServGo admin. Both secrets must match the ServGo server (>= 32 characters);
    # without them the one-time-link sign-in stays switched off.
    servgo_sso_secret: str | None = None
    servgo_internal_key: str | None = None
    servgo_api_url: str = "https://servgo.ru/api"
    # Comma-separated Studio logins allowed to open /studio/tune (agent prompts and settings).
    studio_tune_usernames: str = ""
    telegram_bot_token: str | None = None
    telegram_owner_chat_id: int | None = None
    telegram_poll_timeout_seconds: int = 25
    team_worker_batch_size: int = 1
    team_worker_idle_poll_seconds: int = 20
    team_worker_busy_poll_seconds: int = 2
    telegram_proxy_url: str | None = None
    content_telegram_autopost_enabled: bool = False
    content_telegram_max_lateness_minutes: int = 30
    comment_platform_proxy_url: str | None = None
    viral_research_mode: Literal["public_multisource", "youtube_public", "disabled"] = "public_multisource"
    viral_research_max_queries: int = 4
    viral_research_max_candidates: int = 16
    viral_research_timeout_seconds: float = 20.0

    # Voice module (dictation: speech recognition + correction by meaning). Keys stay on the server.
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    voice_stt_model: str = "whisper-large-v3-turbo"
    # Fast Claude-class model through the same gateway as every other model call.
    voice_cleanup_model: str = "claude-haiku-5-5"
    voice_cleanup_timeout_seconds: float = Field(default=20.0, ge=3, le=120)
    # Access key for bots and pages outside the Studio. Without it the key endpoint stays closed.
    voice_api_key: str | None = None
    # Recording length chosen by the owner (23.10.2026), in seconds.
    voice_max_audio_seconds: int = Field(default=300, ge=10, le=3600)
    # Silence pre-check: recordings shorter than this, or WAV quieter than this (share of full scale), are empty.
    voice_min_audio_ms: int = Field(default=300, ge=0, le=5000)
    voice_silence_rms: float = Field(default=0.002, ge=0, le=0.5)
    # Spend estimate (operator-entered rates, verify against the provider consoles).
    voice_stt_usd_per_hour: str = "0.04"
    voice_llm_input_usd_per_million: str = "1.0"
    voice_llm_output_usd_per_million: str = "5.0"
    # Telegram notice to the owner when today's spend is this many times the usual day (no request limits).
    voice_cost_alert_factor: float = Field(default=3.0, ge=1.0, le=100)
    voice_cost_alert_min_usd: str = "0.5"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("studio_users_file", "whiteboard_proxy_url", mode="before")
    @classmethod
    def optional_account_file(cls, value):
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
