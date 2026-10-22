"""Ask ServGo, right now, whether this person still has access to this business.

Fails closed: any error, timeout or unexpected answer means "no access". A positive answer is
remembered for a few seconds only, so a page load that fetches several files asks ServGo once.
"""
from __future__ import annotations

import logging
import time

import httpx

_logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 3.0
CACHE_SECONDS = 15
_CACHE: dict[tuple[int, int], tuple[float, str]] = {}
_transport: httpx.BaseTransport | None = None  # Tests replace it; production uses the network.


def http_client(timeout: float) -> httpx.Client:
    """A client for calls to ServGo; tests swap the transport, production uses the network."""
    return httpx.Client(timeout=timeout, transport=_transport)


def _fetch(settings, user_id: int, business_id: int) -> str | None:
    key = settings.servgo_internal_key or ""
    if len(key) < 32:
        return None
    url = settings.servgo_api_url.rstrip("/") + "/internal/studio/access"
    with httpx.Client(timeout=TIMEOUT_SECONDS, transport=_transport) as client:
        response = client.get(url, params={"user_id": user_id, "business_id": business_id},
                              headers={"X-Studio-Key": key})
    if response.status_code != 200:
        _logger.warning("studio_sso_access_denied_or_failed status=%s", response.status_code)
        return None
    data = response.json()
    if data.get("allowed") is True and data.get("role") in {"owner", "co_owner", "administrator"}:
        return data["role"]
    return None


def servgo_role(settings, user_id: int, business_id: int, *, now: float | None = None) -> str | None:
    current = now if now is not None else time.monotonic()
    cached = _CACHE.get((user_id, business_id))
    if cached and cached[0] > current:
        return cached[1]
    try:
        role = _fetch(settings, user_id, business_id)
    except (httpx.HTTPError, ValueError) as exc:
        _logger.warning("studio_sso_access_check_failed %s", type(exc).__name__)
        role = None
    if role is None:
        _CACHE.pop((user_id, business_id), None)
        return None
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[(user_id, business_id)] = (current + CACHE_SECONDS, role)
    return role


def forget(user_id: int | None = None, business_id: int | None = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop((user_id, business_id), None)


def servgo_business(settings, user_id: int, business_id: int) -> dict | None:
    """What the business shows publicly (name, address, hours, services with prices), or None.

    ServGo answers only to an owner of that business. Used once, when a company is created in the studio.
    """
    key = settings.servgo_internal_key or ""
    if len(key) < 32:
        return None
    url = settings.servgo_api_url.rstrip("/") + "/internal/studio/business"
    try:
        with httpx.Client(timeout=10.0, transport=_transport) as client:
            response = client.get(url, params={"user_id": user_id, "business_id": business_id},
                                  headers={"X-Studio-Key": key})
        if response.status_code != 200:
            _logger.warning("studio_sso_business_failed status=%s", response.status_code)
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        _logger.warning("studio_sso_business_failed %s", type(exc).__name__)
        return None
    return data if isinstance(data, dict) and isinstance(data.get("name"), str) else None
