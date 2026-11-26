"""Persisted, model-specific 24-hour image recovery policy."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .runtime import DEFAULT_IMAGE_MODEL, GPT_IMAGE_MODEL, CLAUDEHUB_IMAGE_MODELS, TRANSIENT_STATUSES

IMAGE_RECOVERY_RESERVE = 105  # Initial failure plus 104 scheduled attempts; explicitly approved.
NETWORK_FAILURES = frozenset({'ConnectError', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout',
                              'PoolTimeout', 'RemoteProtocolError', 'ReadError', 'TimeoutError'})


@dataclass(frozen=True)
class RecoveryDecision:
    at: datetime
    preflight_only: bool = False


def image_recovery_enabled(payload):
    return bool(getattr(payload, 'image_recovery', False) and payload.image_provider == 'claudehub'
                and payload.models.image in CLAUDEHUB_IMAGE_MODELS)


def effective_image_model(value, payload):
    primary = payload.models.image
    backup = GPT_IMAGE_MODEL if primary == DEFAULT_IMAGE_MODEL else DEFAULT_IMAGE_MODEL
    if image_recovery_enabled(payload) and value.get('active_image_model') == backup:
        return backup
    return primary


def next_check(since, now):
    hour = since + timedelta(hours=1)
    deadline = since + timedelta(hours=24)
    if now < hour:
        return min(now + timedelta(minutes=5), hour)
    if now < deadline:
        return min(now + timedelta(minutes=15), deadline)
    return now + timedelta(minutes=15)


def schedule_image_recovery(value, payload, code, *, now=None):
    """Only a failed actual primary attempt (or model preflight) can activate backup.
    Polls are persistent; no extra provider call is made by this function.
    """
    now = now or datetime.now(timezone.utc)
    stage = value.get('failed_stage')
    diagnostic = value.get('diagnostic') or {}
    preflight = stage == 'preflight' and code == 'claudehub_image_unavailable'
    image_failure = stage in {'image', 'image_revision'} and (
        code == 'provider_server_error' or diagnostic.get('http_status') in TRANSIENT_STATUSES
        or diagnostic.get('exception_type') in NETWORK_FAILURES)
    if not (preflight or image_failure):
        return None
    if image_failure:
        value['image_recovery_failures'] = min(IMAGE_RECOVERY_RESERVE,
                                               int(value.get('image_recovery_failures', 0)) + 1)
    since = datetime.fromisoformat(value.setdefault('image_outage_since', now.isoformat()))
    deadline = since + timedelta(hours=24)
    if effective_image_model(value, payload) != payload.models.image:
        return RecoveryDecision(now + timedelta(minutes=15), preflight)
    if now >= deadline:
        value['active_image_model'] = GPT_IMAGE_MODEL if payload.models.image == DEFAULT_IMAGE_MODEL else DEFAULT_IMAGE_MODEL
        value['image_fallback_at'] = now.isoformat()
        return RecoveryDecision(now, preflight)
    return RecoveryDecision(next_check(since, now), preflight)
