"""Owner-only campaign dashboard, delivered under the mail subdomain API proxy."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

from .owner_mail import mail_owner

router = APIRouter(prefix="/owner-mail", tags=["mail-campaign-ui"])
# The PWA already bundles the local Onest variable font. Serve the SAME font
# on both canonical servgo.ru and mail.servgo.ru origins (no Google Fonts).
# The names are content-hashed by Vite; select only these two known subsets.
_ONEST_ASSETS = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "assets"


def _onest_asset(subset: str) -> Path:
    if subset not in {"cyrillic", "latin"}:
        raise HTTPException(404, "Font subset not found")
    matches = sorted(_ONEST_ASSETS.glob(f"onest-{subset}-wght-normal-*.woff2"))
    if not matches:
        raise HTTPException(404, "Onest font asset unavailable")
    return matches[-1]


@router.get("/api/servgo-air-onest-{subset}.woff2", include_in_schema=False)
def air_onest_font(subset: str):
    return FileResponse(
        _onest_asset(subset),
        media_type="font/woff2",
        headers={"Cache-Control": "public, max-age=300",
                 "X-Content-Type-Options": "nosniff"},
    )


# Public static design resources. No mail/customer data is exposed.
# Mail HTML itself remains owner-only via mail_owner.
@router.get("/api/servgo-air-tokens.css", include_in_schema=False)
def air_tokens():
    return FileResponse(
        Path(__file__).with_name("servgo-air-tokens.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/servgo-mail-air.css", include_in_schema=False)
def mail_air_styles():
    return FileResponse(
        Path(__file__).with_name("servgo-mail-air.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/servgo-mail-theme-picker.js", include_in_schema=False)
def mail_theme_picker():
    return FileResponse(
        Path(__file__).with_name("servgo-mail-theme-picker.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/servgo-air-theme.js", include_in_schema=False)
def mail_theme_bootstrap():
    return FileResponse(
        Path(__file__).with_name("servgo-air-theme.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/campaigns-dashboard")
async def campaign_dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    # Expired or missing session: back to the login form instead of a bare 401.
    try:
        await mail_owner(request, session)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse("/", status_code=303)
    return FileResponse(
        Path(__file__).with_name("mail_campaign_dashboard.html"),
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


@router.get("/api/mailbox-dashboard")
async def mailbox_dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    # Expired or missing session: back to the login form instead of a bare 401.
    try:
        await mail_owner(request, session)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse("/", status_code=303)
    return FileResponse(
        Path(__file__).with_name("owner_mail_ui.html"),
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )
