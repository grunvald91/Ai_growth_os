"""Niche landing pages — YAML config loader.

Reads `_shared.yaml` and per-niche `<slug>.yaml` files in this directory,
deep-merges shared defaults under per-niche values, and exposes `get_niche(slug)`
for the aiohttp handler.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import yaml

_NICHES_DIR = os.path.dirname(__file__)
_SHARED_NAME = "_shared.yaml"
_OFFER_NAME = "_offer.yaml"
_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_shared_cache: dict[str, Any] | None = None
_RELOAD_ON_EACH_REQUEST = os.environ.get("NICHES_RELOAD", "0") == "1"


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `overlay` into `base`. Overlay wins on conflicts."""
    result = dict(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_shared() -> dict[str, Any]:
    global _shared_cache
    if _shared_cache is None or _RELOAD_ON_EACH_REQUEST:
        _shared_cache = _load_yaml(os.path.join(_NICHES_DIR, _SHARED_NAME))
    return _shared_cache


def _money(value: int) -> str:
    """990 -> '990 ₽', 2990 -> '2 990 ₽' (неразрывные пробелы)."""
    return f"{int(value):,}".replace(",", "\u00a0") + "\u00a0₽"


def get_offer(slug: str | None = None) -> dict[str, Any]:
    """Return start-offer conditions from `_offer.yaml`.

    With `slug`, adds the per-niche counters (`seats_left`, `studio_free_left`).
    Always adds ready-made text values (`entry_price_text`, `studio_price_text`).
    """
    raw = _load_yaml(os.path.join(_NICHES_DIR, _OFFER_NAME))
    total = int(raw.get("seats_total", 3))
    offer: dict[str, Any] = {
        "entry_price": int(raw["entry_price"]),
        "studio_price_month": int(raw["studio_price_month"]),
        "seats_total": total,
        "updated": raw.get("updated", ""),
        "entry_price_text": _money(raw["entry_price"]),
        "studio_price_text": _money(raw["studio_price_month"]),
    }
    if slug:
        left = int((raw.get("seats_left") or {}).get(slug, total))
        offer["seats_left"] = max(0, min(total, left))
        offer["studio_free_left"] = int((raw.get("studio_free_left") or {}).get(slug, 0))
    return offer


def _fill_tokens(value: Any, tokens: dict[str, str]) -> Any:
    """Replace [[token]] placeholders in every string of a nested structure."""
    if isinstance(value, str):
        for key, text in tokens.items():
            value = value.replace(f"[[{key}]]", text)
        return value
    if isinstance(value, list):
        return [_fill_tokens(item, tokens) for item in value]
    if isinstance(value, dict):
        return {k: _fill_tokens(v, tokens) for k, v in value.items()}
    return value


def offer_tokens(offer: dict[str, Any]) -> dict[str, str]:
    tokens = {
        "entry_price": offer["entry_price_text"],
        "studio_price": offer["studio_price_text"],
        "seats_total": str(offer["seats_total"]),
    }
    if "seats_left" in offer:
        tokens["seats_left"] = str(offer["seats_left"])
    return tokens


def list_niches() -> list[str]:
    """Return the list of available niche slugs (filenames without extension)."""
    return sorted(
        os.path.splitext(name)[0]
        for name in os.listdir(_NICHES_DIR)
        if name.endswith(".yaml") and not name.startswith("_")
    )


def get_niche(slug: str) -> dict[str, Any] | None:
    """Return merged niche data, or None if the slug is unknown."""
    if not slug or slug != slug.lower() or not slug.replace("-", "").isalnum():
        return None

    with _lock:
        if not _RELOAD_ON_EACH_REQUEST and slug in _cache:
            return _cache[slug]

        path = os.path.join(_NICHES_DIR, f"{slug}.yaml")
        if not os.path.isfile(path):
            return None

        shared = _load_shared()
        niche = _load_yaml(path)
        merged = _deep_merge(shared, niche)
        merged["slug"] = slug

        offer = get_offer(slug)
        merged = _fill_tokens(merged, offer_tokens(offer))
        merged["offer"] = offer

        if not _RELOAD_ON_EACH_REQUEST:
            _cache[slug] = merged
        return merged
