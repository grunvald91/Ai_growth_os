"""Owner-only payment review inbox and authenticated proof downloads."""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import selectinload

from database.models import Booking, BookingReceipt, PrepaymentReview
from database.prepayment import payment_visit, review_payment, PaymentWindowError
from ..database import get_db
from ..deps import get_current_admin
from .admin import _get_owner_business_ids, _admin_booking_out

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/prepayments/attention")
async def attention(response: Response, user=Depends(get_current_admin), db=Depends(get_db)):
    """All owned businesses, independent of the currently selected business."""
    response.headers["Cache-Control"] = "private, no-store"
    ids = await _get_owner_business_ids(db, user)
    query = select(Booking).where(Booking.business_id.in_(ids), Booking.status == "prepayment_sent")
    count = await db.scalar(select(func.count()).select_from(query.subquery()))
    first = await db.scalar(query.order_by(Booking.id).limit(1).options(
        selectinload(Booking.business), selectinload(Booking.client),
        selectinload(Booking.service), selectinload(Booking.employee)))
    return {"count": count, "first": {"booking": _admin_booking_out(first),
        "business_name": first.business.name, "business_id": first.business_id} if first else None}


async def owned_booking(db, user, booking_id):
    booking = (await db.execute(select(Booking).where(Booking.id == booking_id).options(
        selectinload(Booking.client), selectinload(Booking.business), selectinload(Booking.service),
        selectinload(Booking.employee)))).scalar_one_or_none()
    if not booking or booking.business_id not in await _get_owner_business_ids(db, user):
        raise HTTPException(404, "Запись не найдена")
    return booking


@router.get("/prepayments")
async def inbox(business_id: int, status: Literal["prepayment_sent", "pending_prepayment", "all"] = "prepayment_sent",
                before_id: int | None = None, limit: int = Query(50, ge=1, le=100),
                user=Depends(get_current_admin), db=Depends(get_db)):
    if business_id not in await _get_owner_business_ids(db, user):
        raise HTTPException(403, "Нет доступа к этому бизнесу")
    query = select(Booking).where(Booking.business_id == business_id, or_(Booking.prepayment_amount > 0,
        and_(Booking.prepayment_amount.is_(None), Booking.status.in_(["pending_prepayment", "prepayment_sent"]))))
    if status == "all":
        query = query.where(Booking.status.in_(["prepayment_sent", "pending_prepayment", "confirmed", "cancelled", "completed"]))
    else:
        query = query.where(Booking.status == status)
    if before_id:
        query = query.where(Booking.id < before_id)
    bookings = list((await db.execute(query.order_by(Booking.id.desc()).limit(limit + 1).options(
        selectinload(Booking.client), selectinload(Booking.service), selectinload(Booking.employee)))).scalars())
    return {"items": [_admin_booking_out(b) for b in bookings[:limit]],
            "next_before_id": bookings[limit - 1].id if len(bookings) > limit else None}


@router.get("/bookings/{booking_id}/payment-details")
async def details(booking_id: int, user=Depends(get_current_admin), db=Depends(get_db)):
    booking = await owned_booking(db, user, booking_id)
    visit = await payment_visit(db, booking)
    proofs = list((await db.execute(select(BookingReceipt).where(BookingReceipt.booking_id.in_([b.id for b in visit]))
        .order_by(BookingReceipt.id.desc()))).scalars())
    reviews = list((await db.execute(select(PrepaymentReview).where(PrepaymentReview.booking_id == booking_id)
        .order_by(PrepaymentReview.id.desc()))).scalars())
    return {"booking": _admin_booking_out(booking),
            "visit": [_admin_booking_out(b) for b in visit],
            "prepayment_total": sum(b.prepayment_amount or 0 for b in visit),
            "receipts": [{"id": p.id, "mime_type": p.mime_type, "size": p.size, "created_at": p.created_at} for p in proofs],
            "reviews": [{"decision": r.decision, "reviewer_id": r.reviewer_id, "reviewed_at": r.reviewed_at} for r in reviews]}


@router.get("/receipts/{receipt_id}")
async def download(receipt_id: int, user=Depends(get_current_admin), db=Depends(get_db)):
    proof = await db.get(BookingReceipt, receipt_id)
    if not proof:
        raise HTTPException(404, "Чек не найден")
    await owned_booking(db, user, proof.booking_id)
    await db.refresh(proof, ["content"])
    extension = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[proof.mime_type]
    return Response(content=proof.content, media_type=proof.mime_type, headers={
        "Content-Disposition": f'attachment; filename="receipt-{proof.id}.{extension}"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class Decision(BaseModel):
    decision: Literal["confirm", "reject"]
    expected_version: int = Field(ge=1)


@router.post("/bookings/{booking_id}/prepayment-review")
async def review(booking_id: int, body: Decision, user=Depends(get_current_admin), db=Depends(get_db)):
    booking = await owned_booking(db, user, booking_id)
    try:
        visit = await review_payment(db, booking, user.id, body.decision, expected_version=body.expected_version)
    except PaymentWindowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    await db.commit()
    try:
        from bot.services.notification_events import dispatch_booking_event
        from bot.services.email_notifications import send_booking_email
        await dispatch_booking_event(visit, db, "booking_created" if body.decision == "confirm" else "prepayment_rejected", include_client=True)
        await send_booking_email(visit, "prepayment_confirmed" if body.decision == "confirm" else "prepayment_rejected")
    except Exception:
        logger.exception("Payment review notification failed for booking %s", booking_id)
    return _admin_booking_out(booking)
