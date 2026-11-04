"""Create-screen UX regressions: no duplicate dispatcher and no mismatched arrows."""
from pathlib import Path

from app.api.growth_v4 import ASSETS, create_workspace_v4


def test_create_history_handles_control_the_existing_dispatcher_feed():
    markup = create_workspace_v4("fixture-company", "", voice_project="")
    assert markup.count('id="dp-feed"') == 1
    assert markup.count('id="dp-input"') == 1
    assert 'id="sg-create-history"' in markup
    assert 'id="sg-create-history-close"' in markup
    assert 'id="sg-create-history-open"' in markup
    assert markup.index('id="sg-create-history-close"') < markup.index('id="dp-feed"')
    assert markup.index('id="sg-create-history-open"') < markup.index('id="dp-form"')
    assert 'aria-label="Свернуть историю сообщений"' in markup
    assert 'aria-label="Развернуть историю сообщений"' in markup
    assert markup.count('aria-controls="dp-feed"') == 2
    assert markup.count('class="sg-create-history-grip"') == 2
    assert 'role="log" aria-live="polite"' in markup


def test_create_history_gestures_preserve_feed_and_voice_input():
    script = (ASSETS / "create-studio.js").read_text(encoding="utf-8")
    css = (ASSETS / "growth-live.css").read_text(encoding="utf-8")
    dispatcher = (Path(__file__).resolve().parents[1] /
                  "app/api/dispatcher_assets/dispatcher.js").read_text(encoding="utf-8")
    assert "attachGrip(historyClose,false,1)" in script
    assert "attachGrip(historyOpen,true,-1)" in script
    assert "swipeDirection*movement>24" in script
    assert "root.classList.toggle('sg-history-collapsed',!open)" in script
    assert "history.setAttribute('aria-hidden',String(!open))" in script
    assert "aria-expanded" in script
    assert "root.classList.toggle('has-feed', hasMessages)" in dispatcher
    assert ".sg-create-dispatch.has-feed:not(.sg-history-collapsed) .sg-create-history" in css
    assert ".sg-create-dispatch.has-feed.sg-history-collapsed .sg-create-history-open" in css
    assert "touch-action:none" in css
    assert ".sg-create-dispatch .command-dock" in css


def test_create_accordion_icons_are_identical_and_beside_titles():
    css = (ASSETS / "growth-live.css").read_text(encoding="utf-8")
    assert ":is(.sg-create-advanced,.sg-create-extra,.sg-create-timing)>summary" in css
    assert "justify-content:flex-start" in css
    assert "border-right:1.6px solid var(--muted)" in css
    assert "border-bottom:1.6px solid var(--muted)" in css
    assert ':is(.sg-create-advanced,.sg-create-extra,.sg-create-timing)[open]>summary::after' in css
    assert 'content:"⌄"' not in css
    assert 'content:"⌃"' not in css
