from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.content.whiteboard.image_recovery import (
    effective_image_model, schedule_image_recovery, IMAGE_RECOVERY_RESERVE,
)
from app.content.whiteboard.runtime import DEFAULT_IMAGE_MODEL, GPT_IMAGE_MODEL


def payload(primary=DEFAULT_IMAGE_MODEL, enabled=True):
    return SimpleNamespace(image_recovery=enabled, image_provider='claudehub',
                           models=SimpleNamespace(image=primary))


def test_image_recovery_full_day_and_final_failure():
    p = payload()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = {'failed_stage': 'image', 'diagnostic': {'http_status': 502}}
    now = start
    for index in range(104):
        assert effective_image_model(state, p) == DEFAULT_IMAGE_MODEL
        result = schedule_image_recovery(state, p, 'provider_server_error', now=now)
        assert result.at - now == timedelta(minutes=5 if index < 12 else 15)
        now = result.at
    assert now == start + timedelta(hours=24)
    assert effective_image_model(state, p) == DEFAULT_IMAGE_MODEL
    result = schedule_image_recovery(state, p, 'provider_server_error', now=now)
    assert result.at == now
    assert effective_image_model(state, p) == GPT_IMAGE_MODEL
    assert state['image_recovery_failures'] == IMAGE_RECOVERY_RESERVE


def test_preflight_does_not_spend_reserve_and_backup_waits():
    p = payload(GPT_IMAGE_MODEL)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = {'failed_stage': 'preflight'}
    assert schedule_image_recovery(state, p, 'claudehub_image_unavailable', now=start).preflight_only
    assert schedule_image_recovery(state, p, 'claudehub_image_unavailable', now=start+timedelta(hours=24)).at == start+timedelta(hours=24)
    assert effective_image_model(state, p) == DEFAULT_IMAGE_MODEL
    assert 'image_recovery_failures' not in state
    assert schedule_image_recovery(state, p, 'claudehub_unavailable', now=start+timedelta(hours=24)).at == start+timedelta(hours=24, minutes=15)


def test_non_image_failure_and_legacy_model():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = payload()
    state = {'failed_stage': 'speech', 'diagnostic': {'http_status': 502}}
    assert schedule_image_recovery(state, p, 'provider_server_error', now=now) is None
    assert 'image_outage_since' not in state
    assert effective_image_model({'active_image_model': GPT_IMAGE_MODEL}, payload(enabled=False)) == DEFAULT_IMAGE_MODEL
