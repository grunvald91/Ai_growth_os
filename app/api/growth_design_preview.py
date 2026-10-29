"""Opt-in read-only design preview; no production actions or live APIs.

The standalone v4 HTML has demonstration state only. The route is intentionally
disabled unless GROWTH_DESIGN_PREVIEW_ENABLED=1; authentication remains required.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
PREVIEW = Path(__file__).resolve().parent / "assets" / "growth_os_v4_preview.html"


@router.get("/studio/design-preview", include_in_schema=False)
def design_preview() -> FileResponse:
    if os.getenv("GROWTH_DESIGN_PREVIEW_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="Design preview disabled")
    return FileResponse(
        PREVIEW,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
