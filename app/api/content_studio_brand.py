"""Canonical ServGo logo, mirrored verbatim from booking_bot/webapp/static/favicon.svg.

The official website frontend renders this icon together with the ServGo
wordmark; Growth Studio must use the same brand rather than letter placeholders.
Reference: grunvald91/booking_bot/web/frontend/src/components/Logo.tsx
"""
import html

SERVGO_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <rect x="5" y="8" width="34" height="33" rx="8" fill="#0D1B2A"/>
  <rect x="9.5" y="18" width="25" height="18.5" rx="4" fill="#FFFFFF"/>
  <rect x="13" y="4" width="4.5" height="9" rx="2.25" fill="#0D1B2A"/>
  <rect x="26.5" y="4" width="4.5" height="9" rx="2.25" fill="#0D1B2A"/>
  <g fill="#2F5BEB" opacity="0.5">
    <rect x="13" y="22" width="4" height="4" rx="1.2"/>
    <rect x="20" y="22" width="4" height="4" rx="1.2"/>
    <rect x="27" y="22" width="4" height="4" rx="1.2"/>
    <rect x="13" y="29" width="4" height="4" rx="1.2"/>
    <rect x="20" y="29" width="4" height="4" rx="1.2"/>
  </g>
  <path d="M40.5 19.5 L44 23 L31.5 35.5 L27.5 36.5 L28.5 32.5 Z"
        fill="#2F5BEB" stroke="#FFFFFF" stroke-width="1.6" stroke-linejoin="round"/>
</svg>"""


def servgo_brand_html(*, compact: bool = False, href: str = "/studio") -> str:
    """Official image + wordmark, simple and readable at mobile widths."""
    safe_href = html.escape(href, quote=True)
    tagline = "" if compact else '<small class="sg-brand-caption">ИИ-команда маркетинга</small>'
    return (
        f'<a class="sg-official-brand" href="{safe_href}" aria-label="ServGo — главная">'
        '<img class="sg-official-brand-icon" src="/studio/assets/servgo.svg" '
        'width="42" height="42" alt="" loading="eager" decoding="async">'
        '<span class="sg-official-brand-wordmark">'
        '<span class="sg-official-brand-name">Serv<span>Go</span></span>'
        f'{tagline}</span></a>'
    )
