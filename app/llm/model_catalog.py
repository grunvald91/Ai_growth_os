"""Read-only ClaudeHub/OpenAI-compatible model discovery.

GET /models does not generate tokens. No automatic paid capability probes are made.
A listed model ID proves catalog visibility, NOT Russian site reachability,
live web-search support, or successful inference.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx


class CatalogUnavailable(RuntimeError):
    pass


async def list_gateway_models(
    *, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, ...]:
    if not api_key or not api_key.strip():
        raise CatalogUnavailable("XAI_API_KEY is not configured")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise CatalogUnavailable("Model catalog URL must be a trusted HTTPS endpoint")
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(12.0),
            follow_redirects=False, trust_env=False,
        ) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            if len(response.content) > 512_000:
                raise CatalogUnavailable("Model catalog response is oversized")
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CatalogUnavailable("Provider model listing unavailable; no paid probe was attempted") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CatalogUnavailable("Provider returned an unexpected model catalog format")
    models = []
    for entry in payload["data"][:1000]:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            model = entry["id"]
            if model and len(model) <= 120:
                models.append(model)
    return tuple(sorted(set(models)))


def catalog_comparison(remote_ids: tuple[str, ...], configured_profiles) -> list[dict]:
    """Return sanitized operator-view data with explicit evidence boundaries."""
    available = set(remote_ids)
    return [
        {
            "model": profile.model,
            "enabled": profile.enabled,
            "listed_by_gateway": profile.model in available,
            "web_search": "unverified",  # GET /models cannot establish this.
            "prices": "configured_estimate" if (
                profile.input_usd_per_million is not None
                and profile.output_usd_per_million is not None
            ) else "unknown",
        }
        for profile in configured_profiles
    ]


_NOT_TEXT = ("image", "imagen", "dall", "flux", "sora", "veo", "video", "tts", "speech", "voice",
             "whisper", "audio", "embed", "rerank", "moderation", "vision-only")
_PREMIUM = frozenset({"opus", "fable", "mythos", "pro", "sol", "o3", "ultra"})
_ECONOMY = frozenset({"haiku", "flash", "mini", "lite", "nano", "small", "instant", "deepseek"})


def _tokens(model: str) -> set[str]:
    # Whole words only: "gemini" must not count as "mini".
    return {part for part in re.split(r"[^a-z0-9.]+", model.lower()) if part}


def is_text_model(model: str) -> bool:
    """Name-based guess used only to sort the picker; every catalog model stays selectable."""
    name = model.lower()
    return not any(word in name for word in _NOT_TEXT)


def suggest_tier(model: str) -> str:
    """Starting class for a newly added model; the owner changes it on the models tab."""
    words = _tokens(model)
    if words & _PREMIUM:
        return "premium"
    if words & _ECONOMY or model.lower().startswith("kimi-2"):
        return "economy"
    return "balanced"


def gateway_models(remote_ids: tuple[str, ...], configured_profiles) -> list[dict]:
    """Every model the gateway lists, with whether it is already in the team's catalog."""
    configured = {profile.model: profile for profile in configured_profiles}
    return [
        {"model": model, "text": is_text_model(model), "suggested_tier": suggest_tier(model),
         "configured": model in configured, "enabled": bool(model in configured and configured[model].enabled)}
        for model in remote_ids
    ]


async def _cli() -> int:
    from app.core.config import get_settings
    from app.llm.model_router import ModelRouter

    parser = argparse.ArgumentParser(description="Safe GET /models check; no paid LLM requests")
    parser.add_argument("--remote", action="store_true", help="GET /models using configured API key")
    args = parser.parse_args()
    settings = get_settings()
    router = ModelRouter(profiles_json=settings.model_router_profiles,
                         overrides_json=settings.model_router_overrides,
                         max_call_usd=settings.model_router_max_call_usd)
    result = {"gateway": settings.xai_base_url, "paid_probes": 0,
              "profiles": catalog_comparison((), router.profiles)}
    if args.remote:
        ids = await list_gateway_models(base_url=settings.xai_base_url,
                                        api_key=settings.xai_api_key or "")
        result["profiles"] = catalog_comparison(ids, router.profiles)
        result["remote_model_count"] = len(ids)
        result["not_configured_model_ids"] = list(ids)[:100]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
