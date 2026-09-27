import re

import pytest

from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.privacy import PrivacyGateway


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-abcdefgh12345678",
        "sk-abcdefgh12345678",
        "Bearer abc.def-ghi_123",
        "api_key=topsecretvalue",
        "password: hunter2",
        "Cookie: session=private-value",
        "Authorization: Bearer private.jwt.token",
        "X-API-Key: private-key-value",
    ],
)
@pytest.mark.asyncio
async def test_secrets_never_reach_provider(secret: str) -> None:
    provider = FakeLLMProvider()
    service = LLMService(privacy_gateway=PrivacyGateway(), provider=provider)
    request = LLMRequest(
        system_prompt=f"System secret {secret}",
        messages=[LLMMessage(role="user", content=f"Analyze this: {secret}")],
        metadata={"nested": {"credential": secret}},
    )

    await service.generate(request)

    sent = provider.last_request
    assert sent is not None
    serialized = sent.model_dump_json()
    assert secret not in serialized
    assert "REDACTED" in serialized
    assert secret in request.messages[0].content


@pytest.mark.asyncio
async def test_allowlist_and_pii_hooks_are_applied() -> None:
    provider = FakeLLMProvider()
    redact_email = lambda text: re.sub(r"\S+@\S+", "[REDACTED_EMAIL]", text)
    gateway = PrivacyGateway(allowed_context_fields={"goal"}, pii_redaction_hooks=[redact_email])
    service = LLMService(privacy_gateway=gateway, provider=provider)

    await service.generate(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Contact person@example.com")],
            metadata={"goal": "Email person@example.com", "internal": "drop this"},
        )
    )

    sent = provider.last_request
    assert sent is not None
    assert sent.messages[0].content == "Contact [REDACTED_EMAIL]"
    assert sent.metadata == {"goal": "Email [REDACTED_EMAIL]"}
