"""Read-only ClaudeHub/OpenAI-compatible model discovery.

GET /models does not generate tokens. No automatic paid capability probes are made.
A listed model ID proves catalog visibility, NOT Russian site reachability,
live web-search support, or successful inference.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
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


TEXT_DOCUMENT_EXTENSIONS = ("txt", "md", "json", "csv", "pdf", "docx", "xlsx", "xls")
VISION_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_FILES_PER_MESSAGE = 5


@dataclass(frozen=True)
class ConfirmedModelCapabilities:
    """Server-side verified capabilities supplementing ClaudeHub's /models catalog.

    Never infer image generation or direct image/file input from a model name alone.
    """
    id: str
    name: str
    provider: str
    supports_text: bool = True
    supports_streaming: bool = True
    supports_image_generation: bool = False
    supports_vision: bool = False
    supports_files: bool = True
    context_window_chars: int = 120_000
    max_output_tokens: int = 4096
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_files_per_message: int = DEFAULT_MAX_FILES_PER_MESSAGE
    image_sizes: tuple[str, ...] = ()
    verified: bool = True


# Explicit server-side registry of confirmed model capabilities on ClaudeHub.
CONFIRMED_MODEL_CAPABILITIES: dict[str, ConfirmedModelCapabilities] = {
    "claude-sonnet-5-5": ConfirmedModelCapabilities(
        id="claude-sonnet-5-5",
        name="Claude Sonnet 5.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-sonnet-4-5": ConfirmedModelCapabilities(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-sonnet-4-6": ConfirmedModelCapabilities(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-opus-5-5": ConfirmedModelCapabilities(
        id="claude-opus-5-5",
        name="Claude Opus 5.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-opus-4-5": ConfirmedModelCapabilities(
        id="claude-opus-4-5",
        name="Claude Opus 4.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-opus-4-6": ConfirmedModelCapabilities(
        id="claude-opus-4-6",
        name="Claude Opus 4.6",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=180_000,
        max_output_tokens=8192,
    ),
    "claude-haiku-5-5": ConfirmedModelCapabilities(
        id="claude-haiku-5-5",
        name="Claude Haiku 5.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "claude-haiku-4-5": ConfirmedModelCapabilities(
        id="claude-haiku-4-5",
        name="Claude Haiku 4.5",
        provider="Anthropic Claude",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "kimi-2.7": ConfirmedModelCapabilities(
        id="kimi-2.7",
        name="Kimi 2.7",
        provider="Moonshot Kimi",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "kimi-k3": ConfirmedModelCapabilities(
        id="kimi-k3",
        name="Kimi K3",
        provider="Moonshot Kimi",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "deepseek-v4.1-flash": ConfirmedModelCapabilities(
        id="deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash",
        provider="DeepSeek",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=120_000,
        max_output_tokens=4096,
    ),
    "deepseek-chat": ConfirmedModelCapabilities(
        id="deepseek-chat",
        name="DeepSeek Chat",
        provider="DeepSeek",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=120_000,
        max_output_tokens=4096,
    ),
    "deepseek-reasoner": ConfirmedModelCapabilities(
        id="deepseek-reasoner",
        name="DeepSeek Reasoner",
        provider="DeepSeek",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=120_000,
        max_output_tokens=4096,
    ),
    "grok-4.7": ConfirmedModelCapabilities(
        id="grok-4.7",
        name="Grok 4.7",
        provider="xAI Grok",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "grok-4": ConfirmedModelCapabilities(
        id="grok-4",
        name="Grok 4",
        provider="xAI Grok",
        supports_text=True,
        supports_streaming=True,
        supports_vision=False,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=4096,
    ),
    "gemini-3.1-flash": ConfirmedModelCapabilities(
        id="gemini-3.1-flash",
        name="Gemini 3.1 Flash",
        provider="Google Gemini",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=240_000,
        max_output_tokens=8192,
    ),
    "gemini-2.5-flash": ConfirmedModelCapabilities(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="Google Gemini",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=240_000,
        max_output_tokens=8192,
    ),
    "gemini-2.5-pro": ConfirmedModelCapabilities(
        id="gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        provider="Google Gemini",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=240_000,
        max_output_tokens=8192,
    ),
    "gemini-3.1-flash-image": ConfirmedModelCapabilities(
        id="gemini-3.1-flash-image",
        name="Gemini 3.1 Flash Image",
        provider="Google Gemini",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1792"),
    ),
    "gpt-image-1": ConfirmedModelCapabilities(
        id="gpt-image-1",
        name="ChatGPT Image 1",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1536", "1536x1024"),
    ),
    "gpt-image-1.5": ConfirmedModelCapabilities(
        id="gpt-image-1.5",
        name="ChatGPT Image 1.5",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1536", "1536x1024"),
    ),
    "gpt-image-1-mini": ConfirmedModelCapabilities(
        id="gpt-image-1-mini",
        name="ChatGPT Image 1 Mini",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1536", "1536x1024"),
    ),
    "gpt-image-2.5-sunburst": ConfirmedModelCapabilities(
        id="gpt-image-2.5-sunburst",
        name="ChatGPT Image 2.5",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1280", "1792x1008", "1008x1792"),
    ),
    "chatgpt-image-latest": ConfirmedModelCapabilities(
        id="chatgpt-image-latest",
        name="ChatGPT Image Latest",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1536", "1536x1024"),
    ),
    "dall-e-3": ConfirmedModelCapabilities(
        id="dall-e-3",
        name="DALL-E 3",
        provider="OpenAI ChatGPT",
        supports_text=False,
        supports_streaming=False,
        supports_image_generation=True,
        supports_vision=False,
        supports_files=False,
        context_window_chars=16_000,
        max_output_tokens=1024,
        image_sizes=("1024x1024", "1024x1792", "1792x1024"),
    ),
    "gpt-4o": ConfirmedModelCapabilities(
        id="gpt-4o",
        name="GPT-4o",
        provider="OpenAI ChatGPT",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=160_000,
        max_output_tokens=8192,
    ),
    "gpt-4.1": ConfirmedModelCapabilities(
        id="gpt-4.1",
        name="GPT-4.1",
        provider="OpenAI ChatGPT",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=240_000,
        max_output_tokens=8192,
    ),
    "gpt-5": ConfirmedModelCapabilities(
        id="gpt-5",
        name="GPT-5",
        provider="OpenAI ChatGPT",
        supports_text=True,
        supports_streaming=True,
        supports_vision=True,
        supports_files=True,
        context_window_chars=240_000,
        max_output_tokens=8192,
    ),
}


def _family_label(model_id: str) -> str:
    lower = model_id.lower()
    if lower.startswith("claude"):
        return "Anthropic Claude"
    if lower.startswith("kimi"):
        return "Moonshot Kimi"
    if lower.startswith("deepseek"):
        return "DeepSeek"
    if lower.startswith("grok"):
        return "xAI Grok"
    if lower.startswith("gemini"):
        return "Google Gemini"
    if lower.startswith(("gpt-", "chatgpt-", "dall-e", "o1", "o3", "o4")):
        return "OpenAI ChatGPT"
    if lower.startswith("qwen"):
        return "Qwen"
    return "ClaudeHub"


def _display_name(model_id: str) -> str:
    if model_id in CONFIRMED_MODEL_CAPABILITIES:
        return CONFIRMED_MODEL_CAPABILITIES[model_id].name
    return model_id


def resolve_model_capabilities(
    model_id: str,
    *,
    overrides: dict[str, ConfirmedModelCapabilities] | None = None,
) -> ConfirmedModelCapabilities:
    """Return confirmed capabilities if known, or conservative unverified text-only defaults."""
    if overrides and model_id in overrides:
        return overrides[model_id]
    if model_id in CONFIRMED_MODEL_CAPABILITIES:
        return CONFIRMED_MODEL_CAPABILITIES[model_id]
    # Unverified model from /models: NEVER grant image_generation or vision by name!
    text_ok = is_text_model(model_id)
    return ConfirmedModelCapabilities(
        id=model_id,
        name=_display_name(model_id),
        provider=_family_label(model_id),
        supports_text=text_ok,
        supports_streaming=text_ok,
        supports_image_generation=False,
        supports_vision=False,
        supports_files=text_ok,
        context_window_chars=64_000,
        max_output_tokens=4096,
        verified=False,
    )


def serialize_model_entry(
    cap: ConfirmedModelCapabilities,
    *,
    available: bool,
) -> dict:
    from app.ai_chat.model_access import model_access

    access = model_access(cap.id)
    supported_exts: list[str] = []
    if cap.supports_text and cap.supports_files:
        supported_exts.extend(TEXT_DOCUMENT_EXTENSIONS)
    if cap.supports_vision:
        supported_exts.extend(VISION_IMAGE_EXTENSIONS)
    category = "image" if cap.supports_image_generation and not cap.supports_text else "text"
    return {
        "id": cap.id,
        "name": cap.name,
        "provider": cap.provider,
        "category": category,
        "supports_text": cap.supports_text,
        "supports_streaming": cap.supports_streaming,
        "supports_image_generation": cap.supports_image_generation,
        "supports_vision": cap.supports_vision,
        "supports_files": bool(supported_exts),
        "supported_file_extensions": supported_exts,
        "image_sizes": list(cap.image_sizes),
        "context_window_chars": cap.context_window_chars,
        "max_output_tokens": cap.max_output_tokens,
        "max_file_bytes": cap.max_file_bytes,
        "max_files_per_message": cap.max_files_per_message,
        "available": available,
        "access_enabled": access.enabled,
        "access_status": access.status,
        "access_label": access.label,
        "verified": cap.verified,
    }


def _cache_path(products_root: Path) -> Path:
    return Path(products_root) / "_ai_chat" / "model_catalog_cache.json"


def _save_catalog_cache(products_root: Path, model_ids: tuple[str, ...]) -> None:
    try:
        path = _cache_path(products_root)
        if path.parent.is_symlink() or path.is_symlink():
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "models": list(model_ids),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        if len(payload) > 512_000:
            return
        fd, tmp = tempfile.mkstemp(prefix=".catalog-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    except OSError:
        return


def _load_catalog_cache(products_root: Path) -> tuple[tuple[str, ...], str | None]:
    try:
        path = _cache_path(products_root)
        if not path.exists() or path.is_symlink():
            return (), None
        raw = path.read_bytes()
        if len(raw) > 512_000:
            return (), None
        data = json.loads(raw)
        ids = data.get("models")
        updated_at = data.get("updated_at")
        if not isinstance(ids, list):
            return (), None
        cleaned = tuple(str(x) for x in ids if isinstance(x, str) and 0 < len(x) <= 120)
        return cleaned, updated_at if isinstance(updated_at, str) else None
    except (OSError, ValueError, TypeError):
        return (), None


async def ai_chat_catalog(
    settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Build the AI Chat model catalog from ClaudeHub + confirmed capabilities registry."""
    from app.llm.router_store import RouterStoreError, read_state

    stale = False
    warning: str | None = None
    updated_at: str | None = None
    live_ids: tuple[str, ...] = ()

    try:
        live_ids = await list_gateway_models(
            base_url=settings.xai_base_url,
            api_key=settings.xai_api_key or "",
            transport=transport,
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        _save_catalog_cache(settings.products_root, live_ids)
    except CatalogUnavailable:
        stale = True
        cached_ids, updated_at = _load_catalog_cache(settings.products_root)
        if cached_ids:
            live_ids = cached_ids
            warning = "ClaudeHub временно недоступен: показан сохранённый каталог моделей без актуальной проверки."
        else:
            # Fallback to configured router profiles so the page still explains what is configured.
            try:
                profiles = read_state(settings).config.profiles
                configured_ids = [
                    str(item["model"]) for item in profiles
                    if isinstance(item, dict) and isinstance(item.get("model"), str)
                ]
            except (RouterStoreError, ValueError, OSError):
                configured_ids = [settings.llm_model] if settings.llm_model else []
            live_ids = tuple(dict.fromkeys([*configured_ids, "gemini-3.1-flash-image", "gpt-image-1"]))
            warning = "Не удалось получить актуальный каталог моделей ClaudeHub; показан локально настроенный список без подтверждения доступности."

    # Also ensure confirmed router-configured models are discoverable, preserving order.
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for mid in live_ids:
        if mid not in seen:
            seen.add(mid)
            ordered_ids.append(mid)

    # When OPENAI_API_KEY is configured separately and ClaudeHub /models didn't list ChatGPT image models,
    # also expose the platform's verified ChatGPT image models.
    if getattr(settings, "openai_api_key", None) and not any(
        mid.startswith(("gpt-image-", "chatgpt-image-", "dall-e-")) for mid in seen
    ):
        for fallback_img in ("gpt-image-1", "gpt-image-2.5-sunburst"):
            if fallback_img not in seen:
                seen.add(fallback_img)
                ordered_ids.append(fallback_img)

    models: list[dict] = []
    for mid in ordered_ids:
        cap = resolve_model_capabilities(mid)
        # Exclude pure audio/embedding/video models that can neither chat nor generate images.
        if not cap.supports_text and not cap.supports_image_generation:
            continue
        models.append(serialize_model_entry(cap, available=not stale))

    default_text = next(
        (m["id"] for m in models if m["id"] == settings.llm_model and m["supports_text"] and m["access_enabled"] and m["available"]),
        next((m["id"] for m in models if m["supports_text"] and m["access_enabled"] and m["available"]), None),
    )
    default_image = next(
        (m["id"] for m in models if m["supports_image_generation"] and m["access_enabled"] and m["available"]),
        None,
    )
    has_separate_image_category = any(m["supports_image_generation"] for m in models) and any(
        m["supports_text"] for m in models
    )
    return {
        "models": models,
        "default_text_model": default_text,
        "default_image_model": default_image,
        "has_image_models": default_image is not None,
        "split_categories": has_separate_image_category,
        "stale": stale,
        "warning": warning,
        "updated_at": updated_at,
    }


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
