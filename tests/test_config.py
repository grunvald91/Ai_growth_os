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
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "claude-opus-5-5,gpt-6-sol")
    settings = Settings(_env_file=None)
    assert settings.llm_fallback_models == "claude-opus-5-5,gpt-6-sol"
