"""Owner-only read-only SEO booking outcomes, no visitor/client details."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SeoBookingAttribution
from datetime_utils import utc_now
from ..database import Booking, get_db, User
from ..deps import get_current_user
from ..seo_referrals import tracking_enabled
from .admin import _get_owner_business_ids

router = APIRouter(prefix="/admin/businesses", tags=["seo-attribution"])

CONFIRMED = ("confirmed", "completed")


@router.get("/{business_id}/seo/conversions")
async def seo_conversions(
    business_id: int,
    days: int = Query(90, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count unique referred visits (not service rows) by published article slug.

    "Created" is not "confirmed": status is read live from Booking so
    cancellation and prepayment decisions change the result automatically.
    """
    if business_id not in await _get_owner_business_ids(db, user):
        raise HTTPException(status_code=403, detail="Нет доступа к этому бизнесу")
    since = utc_now() - timedelta(days=days)
    rows = await db.execute(
        select(
            SeoBookingAttribution.article_slug,
            func.count(SeoBookingAttribution.id),
            func.sum(case((Booking.status.in_(CONFIRMED), 1), else_=0)),
        )
        .join(Booking, Booking.id == SeoBookingAttribution.booking_id)
        .where(
            SeoBookingAttribution.business_id == business_id,
            Booking.business_id == business_id,
            SeoBookingAttribution.created_at >= since,
        )
        .group_by(SeoBookingAttribution.article_slug)
        .order_by(func.count(SeoBookingAttribution.id).desc(), SeoBookingAttribution.article_slug)
    )
    articles = [
        {"article_slug": slug, "bookings_created": int(created),
         "bookings_confirmed": int(confirmed or 0)}
        for slug, created, confirmed in rows.all()
    ]
    return {
        "business_id": business_id,
        "tracking_enabled": tracking_enabled(),
        "days": days,
        "articles": articles,
        "total_created": sum(a["bookings_created"] for a in articles),
        "total_confirmed": sum(a["bookings_confirmed"] for a in articles),
    }
