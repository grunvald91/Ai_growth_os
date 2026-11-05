"""Mail visual contract must use the same ServGo Air design tokens and active tab indicator.

These tests compare the source of truth in BookingBot, NOT a hand-written
idea of what the ServGo design looks like.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = (ROOT / "web/frontend/src/styles/design-system.css").read_text()
MAIL = (ROOT / "web/api/routes/servgo-mail-air.css").read_text()
TOKENS = (ROOT / "web/api/routes/servgo-air-tokens.css").read_text()
CAMPAIGNS = (ROOT / "web/api/routes/mail_campaign_dashboard.html").read_text()
MAILBOX = (ROOT / "web/api/routes/owner_mail_ui.html").read_text()
BACKEND = (ROOT / "web/api/routes/mail_campaign_ui.py").read_text()


def rule(css: str, selector: str):
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    found = re.search(pattern, css, re.S)
    assert found, "Missing CSS selector: " + selector
    return dict((name.strip(), val.strip()) for name, val in re.findall(
        r"([a-z-]+)\s*:\s*([^;]+);", found.group(1)
    ))


def test_active_mail_tab_uses_exact_servgo_admin_tapered_flow():
    canonical = rule(PLATFORM, ".ds-bn::after")
    mail = rule(MAIL, "html[data-theme] body.servgo-mail-v2 .mail-v3-nav a[aria-current]::after")
    for name in ("clip-path", "background", "background-size", "height",
                 "bottom", "left", "right", "animation", "pointer-events"):
        assert name in canonical, name
        assert mail[name] == canonical[name], (name, canonical[name], mail.get(name))
    assert "width" not in canonical or mail.get("width") == canonical["width"]
    assert rule(PLATFORM, ".ds-bn.active")["color"] == rule(
        MAIL, "html[data-theme] body.servgo-mail-v2 .mail-v3-nav a[aria-current]"
    )["color"]
    assert "@keyframes dsFlowLine" in PLATFORM and "@keyframes dsFlowLine" in MAIL


def test_mail_reuses_platform_colors_radii_and_typography():
    for token in ("--s-bg", "--s-surface", "--s-surface-2", "--s-text",
                  "--s-muted", "--s-accent", "--s-accent-soft", "--s-accent-grad",
                  "--s-flow-grad", "--r-md", "--r-lg", "--r-xl", "--ds-font"):
        assert token + ":" in PLATFORM
        assert token + ":" in TOKENS
    assert "font-family: var(--ds-font)" in MAIL
    assert 'font-family: "Onest Variable"' in MAIL
    assert 'src: url("servgo-air-onest-cyrillic.woff2")' in MAIL
    assert 'src: url("servgo-air-onest-latin.woff2")' in MAIL
    assert "font-weight: 550" in MAIL
    assert "border-radius: 13px" in MAIL
    assert 'background: var(--s-accent-grad)' in MAIL


def test_two_mail_sections_have_same_styled_bottom_nav_and_real_svg_icons():
    for source in (CAMPAIGNS, MAILBOX):
        nav = source.split('<nav class="mail-v3-nav"', 1)[1].split("</nav>", 1)[0]
        assert nav.count("<svg ") == 3
        assert nav.count('viewBox="0 0 24 24"') == 3
        for label in ("Поиск", "Рассылки", "Письма"):
            assert "<span>" + label + "</span>" in nav
        assert "⌕" not in nav and "<b>↗</b>" not in nav and "<b>✉</b>" not in nav
        assert '<nav class="mail-workspace-links"' in source
        assert '<a class="mail-workspace-link mail-workspace-studio"' in source


def test_local_font_route_is_bounded_to_vite_onest_assets():
    assert '@router.get("/api/servgo-air-onest-{subset}.woff2"' in BACKEND
    assert '_ONEST_ASSETS.glob(f"onest-{subset}-wght-normal-*.woff2")' in BACKEND
    assert 'subset not in {"cyrillic", "latin"}' in BACKEND
