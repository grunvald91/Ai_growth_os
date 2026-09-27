import re
from collections.abc import Callable
from typing import Any

from app.llm.schemas import LLMRequest

RedactionHook = Callable[[str], str]


class PrivacyGateway:
    _secret_patterns = (
        (re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"), "Authorization: [REDACTED_BEARER_TOKEN]"),
        (re.compile(r"(?i)x-api-key\s*:\s*\S+"), "X-API-Key: [REDACTED_API_KEY]"),
        (re.compile(r"(?i)cookie\s*:\s*\S+"), "Cookie: [REDACTED_COOKIE]"),
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED_BEARER_TOKEN]"),
        (re.compile(r"(?i)(?:api[_-]?key|password)\s*[:=]\s*\S+"), "[REDACTED_SECRET]"),
        (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{8,}\b"), "[REDACTED_API_KEY]"),
    )

    def __init__(
        self,
        *,
        allowed_context_fields: set[str] | frozenset[str] | None = None,
        pii_redaction_hooks: list[RedactionHook] | None = None,
    ) -> None:
        self._allowed_context_fields = frozenset(allowed_context_fields) if allowed_context_fields is not None else None
        self._pii_redaction_hooks = tuple(pii_redaction_hooks or ())

    def sanitize(self, request: LLMRequest) -> LLMRequest:
        sanitized = request.model_copy(deep=True)
        if sanitized.system_prompt is not None:
            sanitized.system_prompt = self.sanitize_text(sanitized.system_prompt)
        for message in sanitized.messages:
            message.content = self.sanitize_text(message.content)

        metadata = sanitized.metadata
        if self._allowed_context_fields is not None:
            metadata = {key: value for key, value in metadata.items() if key in self._allowed_context_fields}
        sanitized.metadata = self._sanitize_value(metadata)
        return sanitized

    def sanitize_text(self, value: str) -> str:
        sanitized = value
        for pattern, replacement in self._secret_patterns:
            sanitized = pattern.sub(replacement, sanitized)
        for hook in self._pii_redaction_hooks:
            sanitized = hook(sanitized)
        return sanitized

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.sanitize_text(value)
        if isinstance(value, dict):
            return {key: self._sanitize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_value(item) for item in value)
        return value
