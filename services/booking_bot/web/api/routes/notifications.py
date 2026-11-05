"""
Web Push subscription endpoints for ServGo PWA.

A subscription may be attached either to the authenticated user or, immediately
after a guest booking, to the booking owner resolved by the booking notify_token.
"""

from datetime_utils import utc_now
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import Booking, PushSubscription, User, get_db
from ..deps import get_current_user_optional

router = APIRouter()


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=4096)
    keys: PushKeysIn
    notify_token: Optional[str] = None


async def _resolve_subscription_user(
    body: PushSubscriptionIn,
    user: Optional[User],
    db: AsyncSession,
) -> User:
    if user:
        return user

    if not body.notify_token:
        raise HTTPException(status_code=401, detail="Authentication or booking notify token required")

    booking_result = await db.execute(
        select(Booking).where(Booking.notify_token == body.notify_token)
    )
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    user_result = await db.execute(select(User).where(User.id == booking.client_id))
    booking_user = user_result.scalar_one_or_none()
    if not booking_user:
        raise HTTPException(status_code=404, detail="Booking client not found")
    return booking_user


@router.get("/vapid-public-key")
async def vapid_public_key():
    import os

    public_key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if not public_key:
        raise HTTPException(status_code=503, detail="Web Push is not configured")
    return {"public_key": public_key}


@router.post("/subscribe")
async def subscribe(
    body: PushSubscriptionIn,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    owner = await _resolve_subscription_user(body, user, db)

    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    )
    subscription = result.scalar_one_or_none()

    if subscription:
        subscription.user_id = owner.id
        subscription.p256dh = body.keys.p256dh
        subscription.auth = body.keys.auth
        subscription.is_active = True
        subscription.failure_count = 0
        subscription.updated_at = utc_now()
    else:
        subscription = PushSubscription(
            user_id=owner.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            is_active=True,
        )
        db.add(subscription)

    await db.commit()

    # Guest PWA subscriptions are created only after the booking response gives
    # the browser a notify_token. Deliver the already-created booking event now
    # so guest clients get the same immediate PWA confirmation as authenticated
    # clients. Prepayment-gated bookings wait for owner confirmation.
    if body.notify_token:
        try:
            booking_result = await db.execute(
                select(Booking)
                .where(Booking.notify_token == body.notify_token)
                .options(
                    selectinload(Booking.business),
                    selectinload(Booking.service),
                    selectinload(Booking.employee),
                    selectinload(Booking.client),
                )
            )
            booking = booking_result.scalar_one_or_none()
            if booking and booking.status in ("pending", "confirmed", "prepayment_confirmed"):
                from bot.services.webpush import send_client_event_webpush
                event_kind = (
                    "rental_request_created"
                    if getattr(booking, "duration_minutes", None) is not None
                    else "booking_created"
                )
                await send_client_event_webpush([booking], db, event_kind)
                await db.commit()
        except Exception:
            # Subscription itself succeeded; notification bootstrap must never
            # make the endpoint fail.
            await db.rollback()

    return {"ok": True}


@router.delete("/subscribe")
async def unsubscribe(
    body: PushSubscriptionIn,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    owner = await _resolve_subscription_user(body, user, db)
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == owner.id,
        )
    )
    subscription = result.scalar_one_or_none()
    if subscription:
        subscription.is_active = False
        subscription.updated_at = utc_now()
        await db.commit()
    return {"ok": True}
