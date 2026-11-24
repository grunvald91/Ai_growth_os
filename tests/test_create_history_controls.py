"""Create-screen UX regressions: no duplicate dispatcher and no mismatched arrows."""
from pathlib import Path

from app.api.growth_v4 import ASSETS, create_workspace_v4


def test_create_has_current_result_not_history():
    markup = create_workspace_v4("fixture-company", "", voice_project="")
    assert markup.count('id="dp-feed"') == 1
    assert markup.count('id="dp-input"') == 1
    assert 'id="sg-create-result"' in markup
    assert 'sg-create-history' not in markup
    assert 'id="sg-create-video-grip"' in markup
    for name in ("length", "image", "profile", "review", "science", "budget", "cta",
                 "script", "notes", "references"):
        assert f'id="sg-create-video-{name}"' in markup


def test_create_video_gestures_and_current_response():
    script = (ASSETS / "create-studio.js").read_text(encoding="utf-8")
    dispatcher = (Path(__file__).resolve().parents[1] /
                  "app/api/dispatcher_assets/dispatcher.js").read_text(encoding="utf-8")
    assert "videoSettings.open=delta>0" in script
    assert "grip.setPointerCapture(event.pointerId)" in script
    assert "showHistory" not in script
    assert "sg:dispatcher:response" in script
    assert "Keep only the current assistant response" in dispatcher
    assert "restore only the last pending creation card" not in dispatcher


def test_create_accordion_icons_are_identical_and_beside_titles():
    css = (ASSETS / "growth-live.css").read_text(encoding="utf-8")
    assert ":is(.sg-create-advanced,.sg-create-extra,.sg-create-timing)>summary" in css
    assert "justify-content:flex-start" in css
    assert "border-right:1.6px solid var(--muted)" in css
    assert "border-bottom:1.6px solid var(--muted)" in css
    assert ':is(.sg-create-advanced,.sg-create-extra,.sg-create-timing)[open]>summary::after' in css
    assert 'content:"⌄"' not in css
    assert 'content:"⌃"' not in css
