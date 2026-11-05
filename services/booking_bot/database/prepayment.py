"""Shared ten-minute payment window for every booking channel and business type."""

from datetime_utils import utc_now
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from database.models import Booking, PrepaymentReview

PAYMENT_WINDOW = timedelta(minutes=10)


class PaymentWindowError(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def payment_deadline(booking):
    if booking.status != "pending_prepayment" or booking.prepayment_amount == 0:
        return None
    start = booking.prepayment_requested_at or booking.created_at
    return start + PAYMENT_WINDOW if start else None


def request_prepayment(booking, amount, *, now=None):
    """Start when an actual amount is requested; identical quotes do not extend it."""
    if amount < 0:
        raise ValueError("Negative prepayment")
    if amount > 0:
        if booking.status != "pending_prepayment" or booking.prepayment_amount != amount:
            booking.prepayment_requested_at = now or utc_now()
        booking.status = "pending_prepayment"
    elif booking.status == "pending_prepayment":
        booking.status = "pending"
        booking.prepayment_requested_at = None
    booking.prepayment_amount = amount


async def expire_due_prepayments(db, *, now=None):
    """Caller commits. Row locks serialize expiry with payment submission/confirmation."""
    now = now or utc_now()
    query = select(Booking).where(
        Booking.status == "pending_prepayment",
        or_(Booking.prepayment_amount.is_(None), Booking.prepayment_amount > 0),
        func.coalesce(Booking.prepayment_requested_at, Booking.created_at) <= now - PAYMENT_WINDOW,
    ).order_by(Booking.id).with_for_update().options(
        selectinload(Booking.client), selectinload(Booking.business), selectinload(Booking.service))
    expired = list((await db.execute(query)).scalars())
    for booking in expired:
        booking.status = "cancelled"
    return expired


async def require_payment_window(db, booking, *, receipt=False, now=None):
    """Routes load the booking FOR UPDATE first; never revive cancelled reservations."""
    if booking.status == "prepayment_sent":
        return False  # Idempotent retry; never turn receipt submission into payment confirmation.
    if booking.status != "pending_prepayment" or booking.prepayment_amount == 0:
        raise PaymentWindowError(409, "Эта запись не ожидает предоплаты")
    deadline = payment_deadline(booking)
    if deadline is not None and deadline <= (now or utc_now()):
        booking.status = "cancelled"
        await db.commit()
        raise PaymentWindowError(409, "Срок предоплаты истёк. Бронь отменена; выберите свободное время заново")
    if not receipt and booking.business.require_receipt_for_prepayment:
        raise PaymentWindowError(422, "Для отправки предоплаты необходимо приложить чек")
    return True


async def payment_visit(db, booking):
    from database.booking_safety import contiguous_visit_bookings
    statuses = ("pending_prepayment", "prepayment_sent") if booking.status in ("pending_prepayment", "prepayment_sent") else (booking.status,)
    visit = await contiguous_visit_bookings(db, booking, statuses=statuses)
    # Every payment operation locks the entire visit in the same order.
    return list((await db.execute(select(Booking).where(Booking.id.in_([item.id for item in visit]))
        .order_by(Booking.id).with_for_update().execution_options(populate_existing=True)
        .options(selectinload(Booking.client), selectinload(Booking.business),
                 selectinload(Booking.service), selectinload(Booking.employee)))).scalars())


async def submit_payment(db, booking, *, receipt=False):
    visit = await payment_visit(db, booking)
    if booking.status == "prepayment_sent":
        return []
    for item in visit:
        await require_payment_window(db, item, receipt=receipt)
    for item in visit:
        item.status = "prepayment_sent"
        item.version += 1
    return visit


async def review_payment(db, booking, reviewer_id, decision, *, expected_version=None):
    """Caller commits; one state machine for web and Telegram owner decisions."""
    visit = await payment_visit(db, booking)
    if expected_version is not None and booking.version != expected_version:
        raise PaymentWindowError(409, "Заявка уже изменилась. Обновите список перед проверкой")
    if decision not in ("confirm", "reject"):
        raise PaymentWindowError(422, "Неизвестное действие")
    if booking.status not in ("prepayment_sent", "pending_prepayment"):
        raise PaymentWindowError(409, "Эта запись уже обработана")
    if any(item.status not in ("prepayment_sent", "pending_prepayment") for item in visit):
        raise PaymentWindowError(409, "Часть визита уже обработана. Обновите список")
    if decision == "reject" and booking.status != "prepayment_sent":
        raise PaymentWindowError(409, "Нет нового сообщения об оплате для отклонения")
    now = utc_now()
    if decision == "confirm":
        for item in visit:
            if item.duration_minutes is not None and (item.price_requires_confirmation or item.prepayment_amount is None):
                raise PaymentWindowError(409, "Сначала укажите окончательную цену и предоплату")
            if item.status == "pending_prepayment":
                deadline = payment_deadline(item)
                if deadline and deadline <= now:
                    item.status = "cancelled"
                    await db.commit()
                    raise PaymentWindowError(409, "Срок предоплаты истёк. Бронь отменена")
    for item in visit:
        db.add(PrepaymentReview(booking_id=item.id, reviewer_id=reviewer_id, decision=decision,
                               booking_version=item.version))
        item.version += 1
        item.status = "confirmed" if decision == "confirm" else "pending_prepayment"
        item.prepayment_confirmed_at = now if decision == "confirm" else None
        if decision == "reject":
            # The owner explicitly offers a fresh ten-minute retry window.
            item.prepayment_requested_at = now
    return visit
