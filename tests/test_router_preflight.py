"""Offline preflight never sends requests or prints administrator secrets."""
from app.core.config import Settings
from app.llm.router_preflight import offline_router_report
from app.llm.router_store import RouterUpdate, read_state, save_state


def test_offline_preflight_reconciles_every_role_and_stage(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path,
                        model_router_admin_token="secret-for-tests-only-" + "x" * 40)
    report = offline_router_report(settings)
    assert report["role_count"] == report["route_count"] and report["route_count"] >= 39
    assert report["network_calls"] == report["paid_calls"] == 0
    assert not any("Unclassified" in issue or "Dashboard role mismatch" in issue
                   for issue in report["issues"])
    assert "model_router_admin_token" not in str(report)
    assert report["enabled"] is False


def test_preflight_recognizes_premium_model_without_provider_ping(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path)
    config = read_state(settings).config
    profiles = []
    for profile in config.profiles:
        item = dict(profile)
        if item["model"] == "claude-opus-5-5":
            item["enabled"] = True
        profiles.append(item)
    config.profiles = profiles
    config.enabled = True
    save_state(settings, RouterUpdate(expected_revision=0, config=config))
    report = offline_router_report(settings)
    assert report["status"] == "ready"
    assert report["main_ready"] == report["route_count"]
    assert report["critics_ready"] == report["route_count"]
    assert report["network_calls"] == 0


def test_preflight_fails_closed_on_corrupted_state(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path)
    path = tmp_path / ".model-router-config.json"
    path.write_text("{definitely broken")
    report = offline_router_report(settings)
    assert report["status"] == "invalid_configuration"
    assert report["network_calls"] == 0
