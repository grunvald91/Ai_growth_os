"""The fast JSON repair model is isolated from author/critic fallback selection."""
import pytest

from app.core.composition import build_schema_normalizer_llm
from app.core.config import Settings
from app.llm.fallback import FallbackProvider
from app.llm.schema_normalizer import preserve_generated_copy
from app.llm.xai import XAIProvider


def test_format_repair_client_uses_explicit_economy_model_not_author_fallback():
    settings = Settings(
        xai_api_key="dummy-test-key", llm_model="kimi-2.7",
        llm_fallback_models="grok-4.7",
        llm_schema_repair_model="deepseek-v4.1-flash",
        model_router_enabled=False,
    )
    client = build_schema_normalizer_llm(settings)
    assert isinstance(client._provider, XAIProvider)
    assert not isinstance(client._provider, FallbackProvider)
    assert client._provider._model == "deepseek-v4.1-flash"


def test_format_repair_does_not_silently_rewrite_or_drop_existing_article_copy():
    before = {"sections": [{"heading": "Заголовок", "body": "Точный текст",
                            "kind": "fact"}], "unknown": "Не проверено"}
    after = {"sections": [{"heading": "Заголовок", "body": "Точный текст",
                           "kind": "advice"}]}
    preserve_generated_copy(before, after)
    after["sections"][0]["body"] = "Изменённый текст"
    with pytest.raises(ValueError, match="prose"):
        preserve_generated_copy(before, after)
