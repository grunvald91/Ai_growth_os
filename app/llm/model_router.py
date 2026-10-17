"""Deterministic opt-in model selection for the existing LLMProvider interface.

Model availability, prices and live search must be operator-verified, never inferred
from a model name. This module does not make an LLM call to select another LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx

from app.llm.costing import estimate_cost_usd
from app.llm.provider import LLMProvider
from app.llm.schemas import LLMRequest, LLMResponse

Tier = Literal["economy", "balanced", "premium"]
_TIERS = {"economy": 0, "balanced": 1, "premium": 2}
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

_PREMIUM_ROLES = {"cmo", "research_lead"}
_BALANCED_ROLES = {
    "company_intake", "research_pmm", "content_seo", "content_strategist",
    "seo_editor", "conversion_editor", "human_editor", "marketing_editor",
    "scriptwriter", "creative_director", "video_director", "visual_designer",
    "cro", "channel_researcher", "problem_researcher", "viral_content_researcher",
    "youtube_shorts_director", "instagram_reels_director", "sales_agent", "comment_sales",
    "competitive_intelligence", "intelligence", "research_synthesis",
}
_ECONOMY_ROLES = {
    "distribution", "analytics", "article_writer", "video_producer",
    "story_director", "community_manager", "segment_researcher",
    "competitor_voc_researcher", "viral_video_researcher",
    "repurposing_editor", "platform_writer", "comment_agent",
}


class RoutingError(ValueError):
    """Routing rejected the request before sending tokens to the provider."""


class WebSearchUnavailable(RoutingError):
    """No verified end-to-end web-search adapter is available."""


class BudgetExceeded(RoutingError):
    """No available model meets the configured estimated cost ceiling."""


DEFAULT_PRIORITY = 100


@dataclass(frozen=True)
class ModelProfile:
    model: str
    tier: Tier
    enabled: bool = True
    input_usd_per_million: Decimal | None = None
    output_usd_per_million: Decimal | None = None
    web_search_verified: bool = False
    # Owner-set order inside a tier: 1 is tried first. Equal priorities fall back to
    # known price, then model name.
    priority: int = DEFAULT_PRIORITY

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip() or len(self.model) > 120:
            raise RoutingError("Invalid model identifier")
        if self.tier not in _TIERS:
            raise RoutingError("Unknown model tier")
        for price in (self.input_usd_per_million, self.output_usd_per_million):
            if price is not None and (not price.is_finite() or price < 0):
                raise RoutingError("Model prices must be finite and nonnegative")
        if type(self.priority) is not int or not 1 <= self.priority <= 999:
            raise RoutingError("Model priority must be an integer from 1 to 999")


def _parse_profiles(raw: str) -> tuple[ModelProfile, ...]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RoutingError("MODEL_ROUTER_PROFILES is not valid JSON") from exc
    if not isinstance(data, list) or not 1 <= len(data) <= 40:
        raise RoutingError("MODEL_ROUTER_PROFILES must be a list of 1 to 40 models")
    profiles: list[ModelProfile] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or set(item) - {
            "model", "tier", "enabled", "input_usd_per_million",
            "output_usd_per_million", "web_search_verified", "priority",
        }:
            raise RoutingError("Invalid model profile fields")
        try:
            if type(item.get("enabled", True)) is not bool or type(item.get("web_search_verified", False)) is not bool:
                raise RoutingError("Model capability flags must be booleans")
            model, tier = item["model"], item["tier"]
            if not isinstance(model, str) or not isinstance(tier, str):
                raise RoutingError("Model and tier must be strings")
            prices = []
            for key in ("input_usd_per_million", "output_usd_per_million"):
                value = item.get(key)
                prices.append(None if value is None else Decimal(str(value)))
            profile = ModelProfile(
                model=model, tier=tier, enabled=item.get("enabled", True),
                input_usd_per_million=prices[0], output_usd_per_million=prices[1],
                web_search_verified=item.get("web_search_verified", False),
                priority=item.get("priority", DEFAULT_PRIORITY),
            )
        except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
            raise RoutingError("Invalid model profile") from exc
        if profile.model in seen:
            raise RoutingError("Duplicate model profile")
        seen.add(profile.model)
        profiles.append(profile)
    return tuple(profiles)


def _parse_overrides(raw: str) -> dict[str, tuple[str, ...]]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RoutingError("MODEL_ROUTER_OVERRIDES is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RoutingError("Model overrides must be an object")
    out: dict[str, tuple[str, ...]] = {}
    for role, value in data.items():
        if not isinstance(role, str) or not role or not isinstance(value, list) or not 1 <= len(value) <= 3:
            raise RoutingError("Overrides must map role to 1-3 model identifiers")
        if any(not isinstance(model, str) or not model for model in value) or len(set(value)) != len(value):
            raise RoutingError("Invalid override model identifiers")
        out[role] = tuple(value)
    return out


def _money_limit(raw: str | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise RoutingError("Invalid per-call cost limit") from exc
    if not value.is_finite() or value <= 0:
        raise RoutingError("Cost limit must be positive and finite")
    return value


@dataclass(frozen=True)
class RoutingDecision:
    role: str
    phase: str
    required_tier: Tier
    candidates: tuple[str, ...]
    estimated_max_cost_usd: Decimal | None


class ModelRouter:
    """Deterministic policy mapping internal roles to ordered model candidates."""

    def __init__(
        self, *, profiles_json: str, overrides_json: str = "{}",
        max_call_usd: str | None = None, provider_supports_web_search: bool = False,
    ) -> None:
        self.profiles = _parse_profiles(profiles_json)
        self._catalog = {p.model: p for p in self.profiles}
        self.overrides = _parse_overrides(overrides_json)
        self.max_call_usd = _money_limit(max_call_usd)
        # Current XAIProvider cannot complete a search tool call.
        self.provider_supports_web_search = provider_supports_web_search
        for role, models in self.overrides.items():
            target_role, separator, target_phase = role.partition(":")
            if separator and target_phase != "critic":
                raise RoutingError("Only role:critic overrides are supported")
            required = self._tier_for(target_role, target_phase if separator else "main")
            for model in models:
                profile = self._catalog.get(model)
                if not profile or not profile.enabled or _TIERS[profile.tier] < _TIERS[required]:
                    raise RoutingError(f"Unsafe/unavailable override for role {role}")

    @staticmethod
    def _tier_for(role: str, phase: str) -> Tier:
        if phase == "critic":
            return "premium" if role in _PREMIUM_ROLES else "balanced"
        if role in _PREMIUM_ROLES:
            return "premium"
        if role in _ECONOMY_ROLES:
            return "economy"
        return "balanced"  # Unknown work does not silently degrade to economy.

    @staticmethod
    def _input_token_ceiling(request: LLMRequest) -> int:
        """Conservative UTF-8 byte estimate, not a provider token-count guarantee."""
        parts = [request.system_prompt or ""]
        parts.extend(msg.content for msg in request.messages)
        if request.response_schema is not None:
            parts.append(json.dumps(request.response_schema, ensure_ascii=False))
        return sum(len(part.encode("utf-8")) for part in parts) + 512 + 32 * len(request.messages)

    def _max_cost(self, profile: ModelProfile, request: LLMRequest) -> Decimal | None:
        if profile.input_usd_per_million is None or profile.output_usd_per_million is None:
            return None
        return (
            Decimal(self._input_token_ceiling(request)) * profile.input_usd_per_million
            + Decimal(request.max_tokens) * profile.output_usd_per_million
        ) / Decimal(1_000_000)

    def select(self, request: LLMRequest) -> RoutingDecision:
        metadata = request.metadata
        role = str(metadata.get("team_role") or metadata.get("task_type") or "general")
        phase = metadata.get("routing_phase", "main")
        if phase not in {"main", "critic"}:
            raise RoutingError("Unknown routing phase")
        needs_search = metadata.get("requires_web_search", False)
        if type(needs_search) is not bool:
            raise RoutingError("requires_web_search must be a boolean")
        if needs_search and not self.provider_supports_web_search:
            raise WebSearchUnavailable("Live web search is not implemented in the active provider adapter")
        required = self._tier_for(role, phase)
        complexity = metadata.get("routing_complexity", "normal")
        if complexity not in {"low", "normal", "high"}:
            raise RoutingError("routing_complexity must be low, normal or high")
        # Task difficulty can raise but never lower a role's minimum quality floor.
        if complexity == "high":
            required = "premium"
        elif complexity == "normal" and required == "economy":
            # Routine writer work stays economical unless explicitly marked high.
            pass
        preferred = self.overrides.get(f"{role}:{phase}") or (
            self.overrides.get(role) if phase == "main" else None
        )
        eligible = [
            p for p in self.profiles
            if p.enabled and _TIERS[p.tier] >= _TIERS[required]
            and (not needs_search or p.web_search_verified)
        ]
        if preferred is not None:
            ordered = [self._catalog[name] for name in preferred]
            if any(_TIERS[p.tier] < _TIERS[required] for p in ordered):
                raise RoutingError("Override is below the task's quality floor")
            if needs_search and not all(p.web_search_verified for p in ordered):
                raise WebSearchUnavailable("Override does not have validated web search")
        else:
            ordered = sorted(
                eligible,
                key=lambda p: (
                    _TIERS[p.tier],
                    p.priority,
                    self._max_cost(p, request)
                    if self._max_cost(p, request) is not None else Decimal("Infinity"),
                    p.model,
                ),
            )
        priced: list[tuple[ModelProfile, Decimal | None]] = []
        for profile in ordered:
            estimate = self._max_cost(profile, request)
            if self.max_call_usd is not None and (estimate is None or estimate > self.max_call_usd):
                continue
            priced.append((profile, estimate))
            if len(priced) == 3:
                break
        if not priced:
            if needs_search:
                raise WebSearchUnavailable("No enabled proven web search model matches the policy")
            if self.max_call_usd is not None:
                raise BudgetExceeded("No priced model fits the estimated per-call budget")
            raise RoutingError(f"No enabled model meets the {required} quality tier")
        return RoutingDecision(
            role=role, phase=phase, required_tier=required,
            candidates=tuple(p.model for p, _ in priced),
            estimated_max_cost_usd=priced[0][1],
        )


class ModelRoutingProvider:
    """Drop-in LLMProvider decorator; fallback only on transient transport failures."""

    def __init__(self, *, provider: LLMProvider, router: ModelRouter) -> None:
        self.provider = provider
        self.router = router

    async def generate(self, request: LLMRequest) -> LLMResponse:
        decision = self.router.select(request)
        last_error: Exception | None = None
        for model in decision.candidates:
            try:
                forwarded = request.model_copy(update={
                    "model": model,
                    "metadata": {
                        key: value for key, value in request.metadata.items()
                        if key not in {"routing_phase", "routing_complexity"}
                    },
                })
                response = await self.provider.generate(forwarded)
                profile = self.router._catalog[model]
                # A gateway may reply with a different canonical model ID. Never
                # claim the configured tariff applies to an unknown upstream alias.
                if (response.usage is not None and response.model == model
                        and profile.input_usd_per_million is not None
                        and profile.output_usd_per_million is not None):
                    estimate = estimate_cost_usd(
                        response.usage.input_tokens, response.usage.output_tokens,
                        input_usd_per_million=profile.input_usd_per_million,
                        output_usd_per_million=profile.output_usd_per_million,
                    )
                    response = response.model_copy(update={"estimated_cost_usd": estimate})
                return response
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _TRANSIENT_STATUS:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
