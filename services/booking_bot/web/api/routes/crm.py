"""Business-isolated lightweight CRM for booking clients and manually saved contacts.

A booking user appears without copying global identity into a new CRM record.
Private notes, lifecycle stage and contact overrides are scoped to one business.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CRMContact, SalesConversation
from ..database import Booking, Employee, Service, User, get_db
from ..deps import get_current_admin
from .admin import _get_owner_business_ids

router = APIRouter()
Stage = Literal["new", "active", "vip", "paused"]


class ContactCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    notes: str = Field(default="", max_length=5000)
    stage: Stage = "new"
    follow_up_on: date | None = None


class ContactUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=5000)
    stage: Stage | None = None
    follow_up_on: date | None = None


async def _guard(db: AsyncSession, owner: User, business_id: int):
    if business_id not in await _get_owner_business_ids(db, owner):
        raise HTTPException(status_code=403, detail="Нет доступа к этому бизнесу")


def _strip(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


def _row(
    key: str, user: User | None, meta: CRMContact | None,
    bookings: int = 0, completed: int = 0,
    last_booking: date | None = None, next_booking: date | None = None,
    account_contact: bool = True,
) -> dict:
    return {
        "key": key,
        "name": (meta.name if meta and meta.name else None) or (user.full_name if user else "Без имени"),
        # Account-wide phone/email are disclosed only if the customer has booked
        # THIS business; merely opening an authenticated AI chat is not consent
        # to share contact details from an unrelated company.
        "phone": (meta.phone if meta and meta.phone else None) or (
            user.phone if user and account_contact else None
        ),
        "email": (meta.email if meta and meta.email else None) or (
            user.email if user and account_contact else None
        ),
        "notes": meta.notes if meta else "",
        "stage": meta.stage if meta else "new",
        "follow_up_on": meta.follow_up_on if meta else None,
        "bookings_count": bookings,
        "completed_count": completed,
        "last_booking": last_booking,
        "next_booking": next_booking,
        "created_at": meta.created_at if meta else (user.created_at if user else None),
    }


async def _booking_stats(db: AsyncSession, business_id: int):
    """Aggregate by customer in the selected business, not across other tenants."""
    today = date.today()
    subq = (
        select(
            Booking.client_id.label("uid"),
            func.count(case((Booking.occupies_resource.is_(True), Booking.id))).label("total"),
            func.sum(case((and_(Booking.status == "completed", Booking.occupies_resource.is_(True)), 1), else_=0)).label("completed"),
            func.max(Booking.booking_date).label("last_date"),
            func.min(
                case(
                    (and_(Booking.booking_date >= today,
                          Booking.status.notin_(["cancelled", "completed"])), Booking.booking_date),
                    else_=None,
                )
            ).label("next_date"),
        )
        .where(Booking.business_id == business_id)
        .group_by(Booking.client_id)
        .subquery()
    )
    return subq


async def _find(db: AsyncSession, business_id: int, key: str):
    """Resolve a contact only if owned through this business's bookings or CRM."""
    kind, sep, raw = key.partition(":")
    if not sep or not raw.isdecimal() or int(raw) <= 0:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    identifier = int(raw)
    if kind == "m":
        meta = await db.scalar(
            select(CRMContact).where(CRMContact.business_id == business_id,
                                     CRMContact.id == identifier,
                                     CRMContact.user_id.is_(None))
        )
        if not meta:
            raise HTTPException(status_code=404, detail="Контакт не найден")
        return None, meta
    if kind != "u":
        raise HTTPException(status_code=404, detail="Контакт не найден")
    has_booking = await db.scalar(
        select(Booking.id).where(Booking.business_id == business_id,
                                 Booking.client_id == identifier).limit(1)
    )
    if has_booking is None:
        has_thread = await db.scalar(
            select(SalesConversation.id).where(
                SalesConversation.business_id == business_id,
                SalesConversation.principal == f"user:{identifier}",
            ).limit(1)
        )
        if has_thread is None:
            raise HTTPException(status_code=404, detail="Контакт не найден")
    user = await db.get(User, identifier)
    if user is None:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    meta = await db.scalar(
        select(CRMContact).where(CRMContact.business_id == business_id,
                                 CRMContact.user_id == identifier)
    )
    return user, meta


@router.get("/businesses/{business_id}/contacts")
async def list_contacts(
    business_id: int,
    q: str = Query("", max_length=100),
    stage: str = Query("all", pattern="^(all|new|active|vip|paused|follow_up)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    owner: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _guard(db, owner, business_id)
    stats = await _booking_stats(db, business_id)
    booked = await db.execute(
        select(User, CRMContact, stats.c.total, stats.c.completed,
               stats.c.last_date, stats.c.next_date)
        .join(stats, User.id == stats.c.uid)
        .outerjoin(CRMContact, and_(CRMContact.business_id == business_id,
                                    CRMContact.user_id == User.id))
    )
    rows = [
        _row(f"u:{u.id}", u, m, total, completed, last, upcoming)
        for u, m, total, completed, last, upcoming in booked.all()
    ]
    # Registered visitors who only talked to the sales assistant are also contacts.
    # Anonymous guest sessions have no verified user identity and are intentionally
    # left in the conversation inbox rather than inventing person records.
    principals = await db.scalars(
        select(SalesConversation.principal).where(
            SalesConversation.business_id == business_id,
            SalesConversation.principal.like("user:%"),
        )
    )
    seen = {r["key"] for r in rows}
    chat_user_ids = set()
    for principal in principals.all():
        suffix = principal.removeprefix("user:")
        if suffix.isdecimal() and f"u:{suffix}" not in seen:
            chat_user_ids.add(int(suffix))
    if chat_user_ids:
        chat_users = (await db.scalars(select(User).where(User.id.in_(chat_user_ids)))).all()
        chat_meta = (await db.scalars(select(CRMContact).where(
            CRMContact.business_id == business_id,
            CRMContact.user_id.in_(chat_user_ids),
        ))).all()
        metas = {m.user_id: m for m in chat_meta}
        rows.extend(_row(f"u:{u.id}", u, metas.get(u.id), account_contact=False)
                    for u in chat_users)
    manual = await db.scalars(
        select(CRMContact).where(CRMContact.business_id == business_id,
                                 CRMContact.user_id.is_(None))
    )
    rows.extend(_row(f"m:{m.id}", None, m) for m in manual.all())

    term = q.casefold().strip()
    if term:
        digits = "".join(c for c in term if c.isdigit())
        rows = [r for r in rows if any(term in str(r[k] or "").casefold()
                    for k in ("name", "phone", "email"))
                or (len(digits) >= 3 and digits in "".join(c for c in (r["phone"] or "") if c.isdigit()))]
    if stage == "follow_up":
        rows = [r for r in rows if r["follow_up_on"] and r["follow_up_on"] <= date.today()]
    elif stage != "all":
        rows = [r for r in rows if r["stage"] == stage]
    rows.sort(key=lambda r: (str(r["last_booking"] or ""), str(r["created_at"] or "")), reverse=True)
    total = len(rows)
    offset = (page - 1) * page_size
    return {"items": rows[offset:offset + page_size], "total": total, "page": page, "page_size": page_size}


@router.post("/businesses/{business_id}/contacts", status_code=201)
async def create_contact(
    business_id: int,
    body: ContactCreateIn,
    owner: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _guard(db, owner, business_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, detail="Укажите имя")
    meta = CRMContact(
        business_id=business_id, name=name, phone=_strip(body.phone),
        email=_strip(body.email), notes=body.notes.strip(), stage=body.stage,
        follow_up_on=body.follow_up_on,
    )
    db.add(meta)
    await db.commit()
    await db.refresh(meta)
    return _row(f"m:{meta.id}", None, meta)


@router.get("/businesses/{business_id}/contacts/{key}")
async def get_contact(
    business_id: int,
    key: str,
    owner: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _guard(db, owner, business_id)
    user, meta = await _find(db, business_id, key)
    if not user:
        return {**_row(key, None, meta), "history": [], "history_has_more": False}
    result = await db.execute(
        select(Booking, Service.name, Service.price, Employee.name)
        .join(Service, Booking.service_id == Service.id)
        .outerjoin(Employee, Booking.employee_id == Employee.id)
        .where(Booking.business_id == business_id, Booking.client_id == user.id)
        .order_by(Booking.booking_date.desc(), Booking.booking_time.desc(), Booking.id.desc())
        .limit(101)
    )
    records = result.all()
    history = [{
        "id": booking.id,
        "date": booking.booking_date,
        "time": booking.booking_time.strftime("%H:%M"),
        "service": service_name,
        "employee": employee_name,
        "status": booking.status,
        # Price is a current service/estimate, NOT proof of actual payment.
        "price_estimate": booking.estimated_price if booking.estimated_price is not None else service_price,
    } for booking, service_name, service_price, employee_name in records[:100]]
    return {
        **_row(key, user, meta, bookings=len(history), account_contact=bool(history)),
        "bookings_count": await db.scalar(
            select(func.count(Booking.id)).where(Booking.business_id == business_id,
                                                  Booking.client_id == user.id,
                                                  Booking.occupies_resource.is_(True))
        ),
        "completed_count": await db.scalar(
            select(func.count(Booking.id)).where(Booking.business_id == business_id,
                                                  Booking.client_id == user.id,
                                                  Booking.occupies_resource.is_(True),
                                                  Booking.status == "completed")
        ),
        "last_booking": history[0]["date"] if history else None,
        "next_booking": await db.scalar(
            select(func.min(Booking.booking_date)).where(
                Booking.business_id == business_id,
                Booking.client_id == user.id,
                Booking.booking_date >= date.today(),
                Booking.status.notin_(["cancelled", "completed"]),
            )
        ),
        "history": history,
        "history_has_more": len(records) > 100,
    }


@router.patch("/businesses/{business_id}/contacts/{key}")
async def update_contact(
    business_id: int,
    key: str,
    body: ContactUpdateIn,
    owner: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _guard(db, owner, business_id)
    user, meta = await _find(db, business_id, key)
    changes = body.model_dump(exclude_unset=True)
    if "stage" in changes and changes["stage"] is None:
        raise HTTPException(422, detail="Статус обязателен")
    if "notes" in changes and changes["notes"] is None:
        changes["notes"] = ""
    for field in ("name", "phone", "email"):
        if field in changes:
            changes[field] = _strip(changes[field])
    if user is None and "name" in changes and not changes["name"]:
        raise HTTPException(422, detail="Имя обязательно")
    if meta is None:
        meta = CRMContact(business_id=business_id, user_id=user.id)
        db.add(meta)
    for field, value in changes.items():
        setattr(meta, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="Контакт изменён одновременно. Обновите страницу.")
    return await get_contact(business_id, key, owner, db)


@router.delete("/businesses/{business_id}/contacts/{key}")
async def delete_contact(
    business_id: int,
    key: str,
    owner: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _guard(db, owner, business_id)
    user, meta = await _find(db, business_id, key)
    if user is not None:
        # Existing bookings must never be deleted from the CRM screen.
        raise HTTPException(409, detail="Клиент с записями не удаляется. Можно очистить заметки.")
    await db.delete(meta)
    await db.commit()
    return {"ok": True}
