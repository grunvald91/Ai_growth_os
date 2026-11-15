"""Rollback-safe UI SDK pilot contracts: asset, opt-in, and unchanged dispatcher API."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import dispatcher


ROOT = Path(__file__).resolve().parents[1]


def test_pilot_bundle_not_required_by_normal_studio(monkeypatch, tmp_path):
    """When the experimental bundle isn't built, the main dispatcher remains intact."""
    monkeypatch.setattr(dispatcher, "ASSETS", tmp_path)
    with pytest.raises(HTTPException) as error:
        dispatcher.dispatcher_ui_sdk_pilot_js()
    assert error.value.status_code == 404


def test_pilot_bundle_is_served_from_same_origin(monkeypatch, tmp_path):
    bundle = tmp_path / "ui-sdk-pilot.bundle.js"
    bundle.write_text("export const available = true;", encoding="utf-8")
    monkeypatch.setattr(dispatcher, "ASSETS", tmp_path)
    result = dispatcher.dispatcher_ui_sdk_pilot_js()
    assert result.status_code == 200
    assert result.headers["cache-control"] == "private, no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.media_type == "application/javascript"
    assert result.body == bundle.read_bytes()


def test_pilot_is_explicit_and_only_changes_existing_form():
    js = (ROOT / "app/api/dispatcher_assets/dispatcher.js").read_text(encoding="utf-8")
    pilot = (ROOT / "studio_ui_sdk/pilot.js").read_text(encoding="utf-8")
    assert "get('ui_sdk') === '1'" in js
    assert "/studio/assets/ui-sdk-pilot.bundle.js?v=1" in js
    assert "sg:dispatcher:editor-ready" in js
    assert "sg:dispatcher:editor-ready" in pilot
    assert "data-ui-sdk-advanced" in js
    assert "import \"@ui-sdk/web/collapsible\"" in pilot
    assert "ui-collapsible-trigger" in pilot
    assert "ui-collapsible-panel" in pilot
    assert "default-open" in pilot
    assert "panel.hidden = !expanded" in pilot
    assert "pressedchange" not in pilot
    # The pilot must not invent its own path for commands, changes, or confirmations.
    assert "fetch(" not in pilot
    assert "/dispatch/confirm" not in pilot
    assert "post('/edit'" in js
    assert "post('/confirm'" in js


def test_ui_sdk_dependencies_are_version_pinned():
    import json

    package = json.loads((ROOT / "studio_ui_sdk/package.json").read_text(encoding="utf-8"))
    for name in ("@ui-sdk/core", "@ui-sdk/web"):
        value = package["dependencies"][name]
        assert value and value[0].isdigit() and "*" not in value
