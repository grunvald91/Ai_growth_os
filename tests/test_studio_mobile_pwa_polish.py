"""Contracts for the ServGo same-origin PWA and compact mobile Studio UI."""
from pathlib import Path

from starlette.requests import Request

from app.api.growth_v4 import render_v4


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app" / "api" / "assets"


def test_v4_material_pages_advertise_one_existing_servgo_pwa():
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/studio/products/00000000-0000-0000-0000-000000000001/materials",
        "headers": [(b"host", b"servgo.ru")],
        "scheme": "https",
        "server": ("servgo.ru", 443),
        "query_string": b"",
    })
    legacy = (
        '<!doctype html><html><head><title>Материалы</title>'
        '<link rel="stylesheet" href="/studio/assets/studio.css?v=14">'
        '</head><body><main><div class="route-content">'
        '<section class="panel material-detail-card"><div class="article-text">'
        'Visible material text</div></section></div></main></body></html>'
    )
    page = render_v4(legacy, request, None, "/studio?design=classic")
    assert page.count('<link rel="manifest" href="/manifest.webmanifest">') == 1
    assert '<meta name="theme-color" content="#ffffff">' in page
    assert '/studio/assets/servgo-air-theme.js' in page
    assert 'apple-mobile-web-app-capable' in page


def test_classic_and_login_pages_use_the_same_pwa_manifest():
    files = [
        ROOT / "app/api/content_studio.py",
        ROOT / "app/api/studio_login.py",
        ROOT / "app/api/studio_sso.py",
        ROOT / "app/api/dispatcher.py",
    ]
    for file in files:
        assert 'rel="manifest" href="/manifest.webmanifest"' in file.read_text(), file


def test_daily_panel_is_a_rounded_compact_click_drawer():
    css = (ASSETS / "growth-daily.css").read_text()
    drawer = css.split(".sg-daily-drawer {", 1)[1].split("body.sg-daily-open", 1)[0]
    assert "top:50%" in drawer and "bottom:auto" in drawer
    assert "height:min(70dvh,660px)" in drawer
    assert "border-radius:20px" in drawer
    assert "overflow:hidden" in drawer
    assert "top:0;bottom:0" not in drawer
    js = (ASSETS / "growth-daily.js").read_text()
    assert "handle.addEventListener('click'" in js
    assert "backdrop.addEventListener('click',closeDrawer)" in js
    assert "touchstart" not in js


def test_material_text_never_reverts_to_classic_dark_ink():
    css = (ASSETS / "growth-servgo-air.css").read_text()
    bridge = css.split("/* Material-detail contrast bridge:", 1)[1]
    for selector in (".article-text", ".preview-copy", ".sg-post-preview",
                     ".sg-storyboard-card p", ".sg-storyboard-screen"):
        assert selector in bridge
    assert "color:var(--s-text) !important" in bridge
    assert "color:var(--s-muted) !important" in bridge
    assert "-webkit-text-fill-color:currentColor !important" in bridge
    assert "background:var(--s-surface-2) !important" in bridge
