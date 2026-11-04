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
    assert "/studio/assets/growth-servgo-air.css?v=7" in html
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



def test_new_growth_theme_assets_have_registered_routes_and_serve_successfully():
    from app.api.growth_live import air_tokens, air_adapter, air_theme_script, router

    registered = {route.path for route in router.routes}
    for uri in (
        "/studio/assets/servgo-air-tokens.css",
        "/studio/assets/growth-servgo-air.css",
        "/studio/assets/servgo-air-theme.js",
    ):
        assert uri in registered

    for handler, mime in (
        (air_tokens, "text/css"),
        (air_adapter, "text/css"),
        (air_theme_script, "application/javascript"),
    ):
        response = handler()
        assert response.status_code == 200
        assert response.media_type == mime
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.body


def test_growth_air_mobile_shell_matches_booking_and_dark_hero_is_readable():
    adapter = (ASSETS / "growth-servgo-air.css").read_text(encoding="utf-8")
    for term in ('--s-text', '--s-muted', '--s-bg', 'grid-template-columns: repeat(5',
                 'min-height: 54px', 'stroke-width: 1.65', 'sgNavFlow',
                 '.hero-h', 'font-weight: 550', 'font-family: "ServGo Onest"',
                 '/fonts/servgo-onest-cyrillic.woff2', '/fonts/servgo-onest-latin.woff2'):
        assert term in adapter, term
    assert '-webkit-text-fill-color: var(--s-text) !important' in adapter
    assert 'color: var(--s-text) !important' in adapter



def test_v4_intake_profile_uses_readable_light_and_dark_semantic_inks():
    """Regression: profile intake headings/source text were dark-on-dark."""
    adapter = (ASSETS / "growth-servgo-air.css").read_text(encoding="utf-8")
    studio = (Path(__file__).resolve().parents[1] / "app/api/content_studio_design.py").read_text(encoding="utf-8")
    refreshed = (Path(__file__).resolve().parents[1] / "app/api/content_studio_refresh.py").read_text(encoding="utf-8")
    profile = (Path(__file__).resolve().parents[1] / "app/api/content_studio_company.py").read_text(encoding="utf-8")
    shared = (ASSETS / "servgo-air-tokens.css").read_text(encoding="utf-8")

    # Source Studio uses an old light-only ink, even in the v4 wrapper.
    assert ".sg-surface h2{font-size:21px;color:var(--sg-navy)" in refreshed
    assert ".sg-intake-source span{overflow-wrap:anywhere;color:#324b68}" in studio
    for marker in ("sg-intake-working", "sg-intake-sources", "sg-intake-source", "sg-muted"):
        assert marker in profile

    # v4 must bridge both the older variable-based and fixed-ink selectors,
    # rather than just setting body color or patching the one visible heading.
    assert 'body[data-growth-design="v4"] {' in adapter
    assert "--sg-navy: var(--s-text);" in adapter
    assert "--sg-text: var(--s-text);" in adapter
    assert "--sg-muted: var(--s-muted);" in adapter
    assert '.route-content .sg-intake-section :is(h2, h3)' in adapter
    assert ".sg-intake-source span" in adapter
    assert ".sg-profile-list dt" in adapter
    assert ".sg-intake-spinner" in adapter
    assert "border-top-color: var(--s-accent);" in adapter
    assert "--s-text: #1b2d42" in shared
    assert "--s-text: #f0f5fc" in shared
    assert "--s-muted: #b6c4d5" in shared


def test_growth_profile_adapter_is_after_legacy_studio_css_in_html_head():
    from app.api.growth_v4 import render_v4
    from starlette.requests import Request

    legacy = (
        "<!doctype html><html lang='ru'><head><title>Профиль компании</title>"
        '<link rel="stylesheet" href="/studio/assets/studio.css?v=14">'
        "</head><body><main><header class='route-page-head'><h1>Профиль компании</h1></header>"
        "<div class='route-content'><section class='sg-surface sg-intake-section'>"
        "<h2>Материалы · 1</h2><ul class='sg-intake-sources'>"
        "<li class='sg-intake-source'><b>Ваши сведения</b><span>ServGo: данные заведения</span></li>"
        "</ul></section></div></main></body></html>"
    )
    request = Request({
        "type": "http", "method": "GET",
        "path": "/studio/products/00000000-0000-0000-0000-000000000001/profile",
        "headers": [(b"host", b"servgo.ru")], "scheme": "https",
        "server": ("servgo.ru", 443), "query_string": b"",
    })
    result = render_v4(legacy, request, None, "/studio?design=classic")
    assert "Материалы · 1" in result
    assert "ServGo: данные заведения" in result
    assert result.index("/studio/assets/studio.css?v=14") < result.index("/studio/assets/growth-servgo-air.css?v=7")
    assert result.count("/studio/assets/growth-servgo-air.css?v=7") == 1



def test_profile_text_colors_have_sufficient_dark_and_light_contrast():
    """Check the actual shared token pairs used by the v4 intake bridge."""
    import re

    theme = (ASSETS / "servgo-air-tokens.css").read_text(encoding="utf-8")
    def segment(mode):
        match = re.search(r"\[data-theme=['\"]" + mode + r"['\"]\]\s*\{([^}]+)\}", theme)
        assert match, mode
        return match.group(1)
    def color(css, token):
        hit = re.search(r"--" + re.escape(token) + r":\s*(#[0-9a-fA-F]{6})\s*;", css)
        assert hit, token
        return hit.group(1)
    def luma(hex_color):
        rgb = [int(hex_color[pos:pos+2], 16)/255 for pos in (1, 3, 5)]
        values = [x/12.92 if x <= 0.04045 else ((x+0.055)/1.055)**2.4 for x in rgb]
        return sum(a*b for a,b in zip(values, (0.2126, 0.7152, 0.0722)))
    for mode in ("light", "dark"):
        colors = segment(mode)
        background = luma(color(colors, "s-surface"))
        for ink in ("s-text", "s-muted"):
            foreground = luma(color(colors, ink))
            ratio = (max(foreground, background)+0.05)/(min(foreground, background)+0.05)
            assert ratio >= 4.5, f"{mode}: {ink} contrast ratio {ratio:.2f} below 4.5"


def test_v4_mobile_indicator_matches_tapered_servgo_air_client_shape():
    """The client/admin indicator has pointed ends, not the old flat rectangle."""
    source = (ASSETS / "growth-servgo-air.css").read_text(encoding="utf-8")
    import re
    selector = re.search(
        r'body\[data-growth-design="v4"\] \.mobile-nav > a::after\s*\{([^}]+)\}',
        source,
    )
    assert selector, "Growth mobile indicator selector missing"
    properties = selector.group(1)
    assert "clip-path: polygon(0% 50%, 10% 45%, 24% 36%, 36% 23%" in properties
    assert "90% 55%, 76% 64%, 64% 77%, 55% 90%" in properties
    assert "left: 22%;" in properties and "right: 22%;" in properties
    assert "height: 2px;" in properties and "bottom: 0;" in properties
    assert "background-size: 310% 100%;" in properties
    assert "animation: sgNavFlow 3.7s linear infinite;" in properties
    assert "to { background-position: 100% 50%; }" in source


def test_v4_dark_forms_and_dispatcher_messages_use_theme_text_not_fixed_ink():
    css = (ASSETS / "growth-servgo-air.css").read_text(encoding="utf-8")
    assert ".sg-choice-copy b" in css
    assert ".sg-surface h2" in css
    assert ".sg-field > span" in css
    assert ".sg-work-item-main b" in css
    assert ".dp-home .dp-msg" in css
    assert "color: var(--s-text);" in css
    assert "color: var(--s-muted);" in css
    assert ":is(input, textarea)::placeholder" in css
    assert "opacity: 1;" in css
    assert ".sg-choice:has(input:checked)" in css



def test_dispatcher_review_cards_keep_video_subjects_and_material_status_separate():
    adapter = (ASSETS / "growth-servgo-air.css").read_text(encoding="utf-8")
    renderer = (ASSETS.parent / "dispatcher_assets/dispatcher.js").read_text(encoding="utf-8")
    v4 = (ASSETS.parent / "growth_v4.py").read_text(encoding="utf-8")
    assert 'function renderResourceList(block)' in renderer
    assert "dp-resource-visual" in renderer
    assert "dp-resource-kind" in renderer
    assert "function legacyVideoTitle(label)" in renderer
    assert "videoCards.push({ kind: 'video'" in renderer
    assert 'renderResourceList({ items: videoCards })' in renderer
    assert 'renderMaterial(block)' in renderer
    assert "dp-material-top" in renderer
    for fragment in (
        '.dp-resource--video .dp-resource-visual',
        '.dp-resource-title',
        '-webkit-line-clamp: 3',
        '.dp-resource-status',
        '.dp-material-top',
        'display: flex;',
        'flex-wrap: wrap;',
        '.dp-material-status',
        '.dp-material-text',
        '-webkit-line-clamp: 5',
    ):
        assert fragment in adapter, fragment
    assert 'growth-servgo-air.css?v=7' in v4
    assert 'history-20261104' in v4
