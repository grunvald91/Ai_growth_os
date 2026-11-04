"""Deployment handoff uses GitHub Secrets without logging the credential."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "deploy" / "ops" / "sync_yandex_env.py"
SPEC = importlib.util.spec_from_file_location("sync_yandex_env", SOURCE)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def test_sync_preserves_existing_env_and_uses_reference_only(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("XAI_API_KEY=other-secret\nAPI_YANDEX=old-key\nPOSTGRES_PASSWORD=keep\n")
    config = tmp_path / "deploy.json"
    config.write_text(json.dumps({
        "base_image": "test", "overlay_env": {"EXISTING_SETTING": "true"}}))
    monkeypatch.setattr(sync, "ENV", env)
    monkeypatch.setattr(sync, "CONFIG", config)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yandex-test-api-key"))
    sync.main()
    saved = env.read_text()
    assert saved.count("API_YANDEX=") == 1
    assert "API_YANDEX=yandex-test-api-key" in saved
    assert "XAI_API_KEY=other-secret" in saved
    overlay = json.loads(config.read_text())["overlay_env"]
    assert overlay["API_YANDEX"] == "${API_YANDEX:-}"
    assert overlay["EXISTING_SETTING"] == "true"
    assert "yandex-test-api-key" not in config.read_text()
    assert "yandex-test-api-key" not in capsys.readouterr().out


@pytest.mark.parametrize("invalid", ["", "key-with-newline\nunsafe", "x" * 4100])
def test_sync_fails_closed_for_bad_credentials(tmp_path, monkeypatch, invalid):
    env = tmp_path / ".env"
    env.write_text("SAFE=value\n")
    config = tmp_path / "deploy.json"
    config.write_text(json.dumps({"overlay_env": {}}))
    monkeypatch.setattr(sync, "ENV", env)
    monkeypatch.setattr(sync, "CONFIG", config)
    monkeypatch.setattr(sys, "stdin", io.StringIO(invalid))
    with pytest.raises(SystemExit):
        sync.main()
    assert env.read_text() == "SAFE=value\n"
