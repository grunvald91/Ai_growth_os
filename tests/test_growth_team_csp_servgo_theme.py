"""Regression contracts for the strict-CSP AI-team page in unified ServGo."""
from pathlib import Path

from app.api.team_dashboard import _secure_response

ROOT = Path(__file__).resolve().parents[1] / "app" / "api"


def test_team_page_uses_external_servgo_assets_without_inline_handlers():
    page = (ROOT / "team_dashboard_assets" / "index.html").read_text()
    assert '/studio/team/assets/team.css' in page
    assert '/studio/team/assets/servgo-team-air.css' in page
    assert '/studio/assets/servgo-air-tokens.css' in page
    assert '/studio/assets/servgo-air-theme.js' in page
    assert '<style' not in page
    assert '<script>' not in page
    assert ' onclick=' not in page and ' style=' not in page


def test_team_security_headers_are_unchanged():
    response = _secure_response("<h1>Team</h1>", "text/html")
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "'unsafe-inline'" not in policy


def test_growth_middleware_excludes_team_from_v4_inline_wrapper():
    source = (ROOT / "growth_live.py").read_text()
    assert "path == '/studio/team' or path.startswith('/studio/team/')" in source
    assert (ROOT / "team_dashboard_assets" / "servgo-team-air.css").is_file()


def test_small_create_form_keeps_bounded_columns():
    css = (ROOT / "assets" / "growth-live.css").read_text()
    assert "@media(max-width:360px)" in css
    assert "grid-template-columns:minmax(0,1fr)" in css
