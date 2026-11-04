"""Popup interaction contracts for new Growth UI and classic Studio."""
from pathlib import Path

from app.api import content_studio_design
from app.api.content_studio_work_ui import JS as NOTIFICATIONS_JS
from app.api.growth_v4 import ASSETS, create_workspace_v4


def test_notifications_close_on_outside_pointer_and_escape_without_preventing_actions():
    assert "document.addEventListener('pointerdown',event=>" in NOTIFICATIONS_JS
    assert "!widget.contains(event.target)" in NOTIFICATIONS_JS
    assert "widget.open=false" in NOTIFICATIONS_JS
    assert "if(event.key==='Escape' && widget.open)" in NOTIFICATIONS_JS
    assert "widget.querySelector('summary')?.focus()" in NOTIFICATIONS_JS
    assert "event.preventDefault()" not in NOTIFICATIONS_JS


def test_v4_mobile_more_and_search_dismiss_on_outside_pointer():
    js = (ASSETS / "growth-live.js").read_text(encoding="utf-8")
    assert "const floatingMenus='.mobile-nav>details'" in js
    assert "document.querySelectorAll(floatingMenus+'[open]')" in js
    assert "!menu.contains(e.target)" in js
    assert "if(e.target===searchBox)closeSearch()" in js
    assert "const menus=[...document.querySelectorAll(floatingMenus+'[open]')]" in js
    # Inline <details> content stays independently expandable.
    assert "document.querySelectorAll('details[open]')" not in js


def test_classic_mobile_more_dismisses_outside_instead_of_backdrop_only():
    js = content_studio_design.STUDIO_JS
    assert "document.querySelectorAll('[data-m-more][open]')" in js
    assert "!menu.contains(event.target)" in js
    assert "menu.open=false" in js
    assert "if(event.key!=='Escape')return" in js


def test_create_inline_panels_and_conversation_close_outside_without_focus_theft():
    js = (ASSETS / "create-studio.js").read_text(encoding="utf-8")
    markup = create_workspace_v4("fixture-company", "")
    assert "panel.hidden=true" in js
    assert "topics.hidden=true" in js
    assert "!panel.contains(event.target)" in js
    assert "!topics.contains(event.target)" in js
    assert "!history.contains(event.target)" in js
    assert "showHistory(false,false)" in js
    assert "if(focusHandle)(open ? historyClose : historyOpen).focus" in js
    assert 'id="sg-create-history"' in markup
    assert 'id="sg-create-info-panel"' in markup
    assert 'id="sg-create-suggestions"' in markup
