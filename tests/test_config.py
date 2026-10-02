from app.core.config import Settings


def test_llm_settings_support_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.llm_model == "custom-model"
    assert settings.anthropic_api_key == "test-placeholder"
    assert settings.anthropic_base_url == "https://example.invalid"



def test_llm_fallback_models_support_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "deepseek-v4.1-flash,gemini-flash-3.8")
    settings = Settings(_env_file=None)
    assert settings.llm_fallback_models == "deepseek-v4.1-flash,gemini-flash-3.8"


def test_default_llm_profile_is_cost_bounded() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "xai"
    assert settings.llm_model == "deepseek-v4.1-flash"
    assert settings.llm_fallback_models == "gemini-flash-3.8"
    assert settings.xai_reasoning_effort == "low"
