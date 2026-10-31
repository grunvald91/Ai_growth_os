"""Theme wiring and SSR smoke tests for the existing Growth v4 renderer."""
from pathlib import Path

from starlette.requests import Request

from app.api.growth_v4 import render_v4


ASSETS = Path(__file__).resolve().parents[1] / "app" / "api" / "assets"


def test_growth_v4_loads_canonical_css_and_theme_script():
    html = render_v4(
        "<html><head><title>Тест Grow</title></head><body>"
        "<header>Проверка</header><main><h1>Команда</h1>"
        '<form method="post" action="/studio/team/apply"><input name="role"></form>'
        "</main></body></html>",
        Request({
            "type": "http", "method": "GET", "path": "/studio/team",
            "headers": [], "scheme": "https", "server": ("growth.servgo.ru", 443),
            "query_string": b"",
        }),
        None,
        "/studio/team?design=classic",
    )
    assert "/studio/assets/servgo-air-tokens.css?v=1" in html
    assert "/studio/assets/growth-servgo-air.css?v=1" in html
    assert "/studio/assets/servgo-air-theme.js?v=1" in html
    assert 'action="/studio/team/apply"' in html
    assert 'name="role"' in html
    assert 'data-growth-design="v4"' in html


def test_growth_css_has_light_dark_tokens_and_maps_to_servgo_air():
    shared = (ASSETS / "servgo-air-tokens.css").read_text()
    adapter = (ASSETS / "growth-servgo-air.css").read_text()
    assert "--s-bg: #ffffff" in shared
    assert "--s-bg: #111a29" in shared
    assert "--bg: var(--s-bg)" in adapter
    assert "--blue: var(--s-accent)" in adapter
    assert 'data-growth-design="v4"' in adapter


def test_growth_theme_keeps_old_value_as_fallback_only():
    old = (ASSETS / "growth-live.js").read_text()
    bootstrap = (ASSETS / "servgo-air-theme.js").read_text()
    assert "window.ServGoTheme" in old
    assert "themeApi.setPreference" in old
    assert "localStorage.setItem('servgo-desktop-theme'" not in old
    assert "'servgo-theme'" in bootstrap
    assert "'servgo-desktop-theme'" in bootstrap
    assert "prefers-color-scheme: dark" in bootstrap
