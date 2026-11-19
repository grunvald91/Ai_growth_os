"""ServGo AI Chat default model access policy, not a ClaudeHub price list.

An unreviewed alias is never implicitly included. Paid models stay disabled
until separate, tenant-aware entitlement checks are implemented.
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
_PROVIDER_FAMILIES = {
    "anthropic": ("claude-",),
    "google": ("gemini-",),
    "openai": ("gpt-", "chatgpt-", "dall-e-", "o1", "o3", "o4"),
    "deepseek": ("deepseek-",),
    "xai": ("grok-",),
}


@dataclass(frozen=True)
class ModelAccess:
    status: str  # included | paid | review
    enabled: bool
    label: str


def _review() -> ModelAccess:
    return ModelAccess("review", False, "Требует проверки тарифа")


def model_access(model_id: str) -> ModelAccess:
    if not isinstance(model_id, str) or not model_id or len(model_id) > 150:
        return _review()
    parts = model_id.lower().split("/")
    if len(parts) > 2 or any(not part for part in parts):
        return _review()
    name = parts[-1]
    if len(parts) == 2:
        allowed_family = _PROVIDER_FAMILIES.get(parts[0])
        if allowed_family is None or not name.startswith(allowed_family):
            return _review()
    if name in _INCLUDED:
        return ModelAccess("included", True, "Входит в доступ")

    # Only sufficiently recognizable high-cost families are labeled paid.
    # New cheap-sounding model names must first be reviewed by the operator.
    if name.startswith("claude-"):
        if any(fragment in name for fragment in ("sonnet", "opus", "fable", "mythos")):
            return ModelAccess("paid", False, "Отдельная оплата")
        return _review()
    if name.startswith("gemini-"):
        if any(fragment in name for fragment in ("-pro", "-ultra", "-thinking")):
            return ModelAccess("paid", False, "Отдельная оплата")
        return _review()
    if name.startswith(("gpt-", "chatgpt-")):
        if any(marker in name for marker in ("mini", "nano")):
            return _review()
        return ModelAccess("paid", False, "Отдельная оплата")
    if name.startswith(("grok-", "dall-e-", "o1", "o3", "o4")):
        return ModelAccess("paid", False, "Отдельная оплата")
    return _review()
