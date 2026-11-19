"""Initial conservative access rules for ServGo AI Chat. Not gateway price quotes.

No customer can use models marked paid or unreviewed until separate billing and
per-account entitlement checks have been implemented.
"""
from __future__ import annotations

from dataclasses import dataclass

_INCLUDED = frozenset({
    "claude-haiku-4-5", "claude-haiku-5-5",
    "gemini-2.5-flash", "gemini-3.1-flash", "gemini-3.1-flash-image",
    "deepseek-chat", "deepseek-v4.1-flash",
    "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5-mini", "gpt-5-nano", "gpt-image-1-mini",
})
_PAID_FAMILIES = ("claude-", "gpt-", "chatgpt-", "gemini-", "grok-", "gpt-image-", "dall-e-", "o1", "o3", "o4")


@dataclass(frozen=True)
class ModelAccess:
    status: str  # included | paid | review
    enabled: bool
    label: str


def model_access(model_id: str) -> ModelAccess:
    if not isinstance(model_id, str) or not model_id or len(model_id) > 150 or model_id.count("/") > 1:
        return ModelAccess("review", False, "Требует проверки тарифа")
    # Aliases may carry exactly one provider prefix.
    name = model_id.split("/")[-1].lower()
    if name in _INCLUDED:
        return ModelAccess("included", True, "Входит в доступ")
    if name.startswith(_PAID_FAMILIES):
        return ModelAccess("paid", False, "Отдельная оплата")
    # No guesses about new Kimi, DeepSeek Reasoner, or unnamed gateway models.
    return ModelAccess("review", False, "Требует проверки тарифа")
