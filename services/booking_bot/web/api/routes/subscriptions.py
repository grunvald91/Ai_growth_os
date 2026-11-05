"""
Subscription routes: browse, buy, manage client subscriptions.
"""

from datetime_utils import utc_now
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import (
    get_db, Subscription, SubscriptionService, ClientSubscription,
    SubscriptionUsage, Service, Business, User,
)
from ..deps import get_current_user
from ..schemas import SubscriptionOut, SubscriptionServiceOut, ClientSubscriptionOut

router = APIRouter()


# ---------- GET /subscriptions ----------

@router.get("/subscriptions", response_model=List[SubscriptionOut])
async def list_subscriptions(
    business_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """List active subscriptions for a business."""
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.business_id == business_id,
            Subscription.is_active == True,
        )
        .options(selectinload(Subscription.services).selectinload(SubscriptionService.service))
    )
    subs = result.scalars().all()

    out = []
    for s in subs:
        services = []
        for ss in s.services:
            if ss.service:
                services.append(SubscriptionServiceOut(
                    id=ss.service.id,
                    name=ss.service.name,
                    quantity=ss.quantity,
                ))
        out.append(SubscriptionOut(
            id=s.id,
            name=s.name,
            description=s.description,
            price=s.price,
            validity_days=s.validity_days,
            services=services,
        ))
    return out


# ---------- GET /subscriptions/{id} ----------

@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionOut)
async def get_subscription(
    subscription_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get subscription details."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.id == subscription_id)
        .options(selectinload(Subscription.services).selectinload(SubscriptionService.service))
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Subscription not found")

    services = []
    for ss in s.services:
        if ss.service:
            services.append(SubscriptionServiceOut(
                id=ss.service.id,
                name=ss.service.name,
                quantity=ss.quantity,
            ))
    return SubscriptionOut(
        id=s.id,
        name=s.name,
        description=s.description,
        price=s.price,
        validity_days=s.validity_days,
        services=services,
    )


# ---------- POST /subscriptions/{id}/buy ----------

@router.post("/subscriptions/{subscription_id}/buy", response_model=ClientSubscriptionOut)
async def buy_subscription(
    subscription_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Purchase a subscription."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.is_active == True)
        .options(selectinload(Subscription.services))
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    now = datetime.now()
    client_sub = ClientSubscription(
        subscription_id=sub.id,
        client_id=user.id,
        business_id=sub.business_id,
        purchased_at=now,
        expires_at=now + timedelta(days=sub.validity_days),
        status="pending_payment",
    )
    db.add(client_sub)
    await db.commit()
    await db.refresh(client_sub)

    # Count total sessions
    total_sessions = sum(ss.quantity for ss in sub.services)

    # Load business name
    biz_result = await db.execute(select(Business.name).where(Business.id == sub.business_id))
    biz_name = biz_result.scalar_one_or_none() or ""

    return ClientSubscriptionOut(
        id=client_sub.id,
        subscription_name=sub.name,
        business_name=biz_name,
        status=client_sub.status,
        sessions_remaining=total_sessions,
        sessions_total=total_sessions,
        valid_until=client_sub.expires_at,
        payment_status="pending",
        created_at=client_sub.purchased_at,
    )


# ---------- GET /my/subscriptions ----------

@router.get("/my/subscriptions", response_model=List[ClientSubscriptionOut])
async def my_subscriptions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List current user's subscriptions."""
    result = await db.execute(
        select(ClientSubscription)
        .where(ClientSubscription.client_id == user.id)
        .options(
            selectinload(ClientSubscription.subscription).selectinload(Subscription.services),
            selectinload(ClientSubscription.business),
        )
        .order_by(ClientSubscription.purchased_at.desc())
    )
    client_subs = result.scalars().all()

    out = []
    now = utc_now()
    for cs in client_subs:
        sub = cs.subscription
        total_sessions = sum(ss.quantity for ss in sub.services) if sub else 0

        # Count used sessions
        usage_result = await db.execute(
            select(func.count(SubscriptionUsage.id))
            .where(SubscriptionUsage.client_subscription_id == cs.id)
        )
        used = usage_result.scalar() or 0

        # Auto-expire if valid_until has passed
        status = cs.status
        if status == "active" and cs.expires_at and cs.expires_at < now:
            status = "expired"
            cs.status = "expired"

        payment_status = "confirmed" if cs.payment_confirmed_at else "pending"
        if cs.payment_receipt:
            payment_status = "receipt_sent"

        out.append(ClientSubscriptionOut(
            id=cs.id,
            subscription_name=sub.name if sub else "",
            business_name=cs.business.name if cs.business else "",
            status=status,
            sessions_remaining=max(0, total_sessions - used),
            sessions_total=total_sessions,
            valid_until=cs.expires_at,
            payment_status=payment_status,
            created_at=cs.purchased_at,
        ))

    # Persist any status changes
    await db.commit()
    return out


# ---------- POST /my/subscriptions/{id}/payment-sent ----------

@router.post("/my/subscriptions/{client_sub_id}/payment-sent")
async def subscription_payment_sent(
    client_sub_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark subscription payment as sent."""
    result = await db.execute(
        select(ClientSubscription).where(
            ClientSubscription.id == client_sub_id,
            ClientSubscription.client_id == user.id,
        )
    )
    cs = result.scalar_one_or_none()
    if not cs:
        raise HTTPException(status_code=404, detail="Subscription not found")

    cs.status = "payment_sent"
    await db.commit()

    return {"message": "Payment notification sent"}
