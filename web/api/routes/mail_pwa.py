"""Owner-only Mail workspace rendered on the *same* ServGo PWA origin.

The existing Mail backend already lives behind /api/owner-mail on servgo.ru.
These optional HTML entry points adapt the two trusted static templates to
their same-origin API paths; no IMAP/SMTP/AI behavior or permissions change.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from .owner_mail import COOKIE, canonical_mail_request, mail_owner

router = APIRouter(prefix="/owner-mail", tags=["owner-mail-pwa"])
ROOT = Path(__file__).resolve().parent
PWA_API_PREFIX = "/api/owner-mail"
HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    ),
}


def _require_pwa(request: Request):
    if not canonical_mail_request(request):
        raise HTTPException(404, "Почтовый раздел в приложении не включён")


def adapt_mail_html(page: str, *, mailbox: bool = False) -> str:
    """Adapt only trusted repository-owned HTML; never rewrite user content.

    The original mail dashboards were hosted at mail.servgo.ru, with /api
    pointing to Mail. On servgo.ru, BookingBot's existing API is at /api and
    the same protected Mail routes are under /api/owner-mail/api.
    """
    page = page.replace('"/api', '"/api/owner-mail/api').replace("'/api", "'/api/owner-mail/api")
    # Navigation links must stay in the adapted workspace, not load the
    # unadapted legacy HTML templates at their old paths.
    page = page.replace('href="/api/owner-mail/api/campaigns-dashboard"',
                        'href="/api/owner-mail/workspace"')
    page = page.replace('href="/api/owner-mail/api/mailbox-dashboard',
                        'href="/api/owner-mail/workspace/mailbox')
    if mailbox:
        page = page.replace('href="/"', 'href="/api/owner-mail/workspace"')
        page = page.replace('location.replace("/")', 'location.replace("/mail")')
    # Same-origin workspace transitions belong in the existing mail heading,
    # not in a fixed footer that overlaps the mobile navigation. Growth entry
    # returns through ServGo's server-checked business/SSO flow.
    navigation = (
        '<nav class="sg-pwa-workspace-nav" aria-label="Разделы ServGo">'
        '<a href="/admin">← ServGo</a>'
        '<a href="/workspace/growth">Маркетинг</a>'
        '<a href="/api/owner-mail/workspace/logout">Выйти из почты</a>'
        '</nav>'
    )
    style = (
        '<style>'
        '.sg-pwa-workspace-nav{display:flex;align-items:center;flex-wrap:wrap;'
        'gap:6px 14px;margin:0 0 10px;color:var(--s-muted,#62778b);'
        'font-size:12px;line-height:1.3}'
        '.sg-pwa-workspace-nav a{color:inherit;text-decoration:none;'
        'padding:6px 0;min-height:30px;display:inline-flex;align-items:center}'
        '.sg-pwa-workspace-nav a:hover,.sg-pwa-workspace-nav a:focus-visible{'
        'color:var(--s-accent,#2465bd);text-decoration:underline}'
        '</style>'
    )
    # Both trusted templates have a heading. This adds inline breadcrumbs,
    # leaving all original controls and JS IDs unchanged.
    page = page.replace('<h1', navigation + '<h1', 1)
    return page.replace('</head>', style + '</head>', 1)


async def _workspace(request: Request, session: AsyncSession, template: str, *, mailbox=False):
    _require_pwa(request)
    try:
        await mail_owner(request, session)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse("/mail", status_code=303, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"
        })
    body = adapt_mail_html((ROOT / template).read_text(encoding="utf-8"), mailbox=mailbox)
    return HTMLResponse(body, headers=HEADERS)


@router.get("/workspace", include_in_schema=False)
async def mail_workspace(request: Request, session: AsyncSession = Depends(get_db)):
    return await _workspace(request, session, "mail_campaign_dashboard.html")


@router.get("/workspace/mailbox", include_in_schema=False)
async def mail_workspace_mailbox(request: Request, session: AsyncSession = Depends(get_db)):
    return await _workspace(request, session, "owner_mail_ui.html", mailbox=True)


@router.get("/workspace/logout", include_in_schema=False)
def mail_workspace_logout(request: Request):
    _require_pwa(request)
    # Exit the mail workspace rather than instantly issuing a new SSO ticket.
    response = RedirectResponse("/admin", status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"
    })
    response.delete_cookie(COOKIE, path=PWA_API_PREFIX, secure=True,
                           httponly=True, samesite="lax")
    return response
