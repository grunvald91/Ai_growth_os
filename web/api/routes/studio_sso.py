"""Single sign-on between the ServGo admin and the marketing studio (growth.servgo.ru).

Two small, independent pieces:

* ``POST /studio/ticket`` — a signed-in person asks for a link into the studio. The answer is a
  one-time link that lives 60 seconds. The studio exchanges it for its own sign-in.
* ``GET /internal/studio/access`` — the studio asks, with a private key, whether this person
  still has access to this business *right now*. Removing someone here cuts the studio access too.

Nothing here lets the studio read bookings, clients or money. It only answers yes/no and a role.
Both pieces refuse to work (503) until the secrets are set in the environment.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Business, BusinessOwner, Employee, Service, User, get_db
from ..deps import get_current_user

router = APIRouter()

TICKET_TTL_SECONDS = 60
AUDIENCE = "growth-studio"
MIN_SECRET_LENGTH = 32
STUDIO_URL = "https://growth.servgo.ru"
CANONICAL_STUDIO_URL = "https://servgo.ru"


def studio_public_url() -> str:
    """Feature-gated same-origin sign-in endpoint for the installed ServGo PWA.

    Never accept a caller-provided URL, Host header, or unvalidated environment
    origin here: the one-time ticket must land at a fixed trusted destination.
    Leave disabled until nginx /studio routing, Growth Origin validation,
    SSO and PWA cache upgrade have passed staging smoke tests.
    """
    enabled = os.getenv("SERVGO_GROWTH_GATEWAY_ENABLED", "").casefold() in {"1", "true", "yes"}
    return CANONICAL_STUDIO_URL if enabled else STUDIO_URL



def _secret(name: str) -> bytes:
    value = os.getenv(name, "")
    if len(value) < MIN_SECRET_LENGTH:
        raise HTTPException(status_code=503, detail="Studio sign-in is not configured")
    return value.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_ticket(*, user_id: int, business_id: int, role: str, name: str, now: Optional[float] = None) -> str:
    payload = _b64(json.dumps({
        "v": 1, "aud": AUDIENCE, "uid": user_id, "biz": business_id, "role": role, "name": name[:120],
        "jti": secrets.token_urlsafe(16), "exp": int((now if now is not None else time.time()) + TICKET_TTL_SECONDS),
    }, separators=(",", ":"), ensure_ascii=False).encode())
    signature = _b64(hmac.new(_secret("STUDIO_SSO_SECRET"), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


async def studio_role(db: AsyncSession, user_id: int, business_id: int) -> Optional[str]:
    """Role this person has in the studio for this business, or None when they have no access."""
    business = await db.get(Business, business_id)
    if business is None:
        return None
    if business.owner_id == user_id:
        return "owner"
    link = await db.scalar(select(BusinessOwner).where(
        BusinessOwner.business_id == business_id, BusinessOwner.user_id == user_id))
    if link is not None:
        return link.role if link.role in ("owner", "co_owner") else "co_owner"
    if not business.studio_admin_access:
        return None
    user = await db.get(User, user_id)
    if user is None or not user.telegram_id:
        return None
    admin = await db.scalar(select(Employee).where(
        Employee.business_id == business_id, Employee.telegram_id == user.telegram_id,
        Employee.role == "administrator", Employee.is_active.is_(True)))
    return "administrator" if admin is not None else None


class StudioBusinessOut(BaseModel):
    id: int
    name: str
    role: str


@router.get("/studio/businesses", response_model=list[StudioBusinessOut])
async def studio_businesses(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Businesses this person can open in the marketing studio. Empty while the sign-in is not configured,
    so the «Маркетинг» entry only appears when it will really work."""
    if (len(os.getenv("STUDIO_SSO_SECRET", "")) < MIN_SECRET_LENGTH
            or len(os.getenv("STUDIO_INTERNAL_KEY", "")) < MIN_SECRET_LENGTH):
        return []
    ids = set((await db.scalars(select(Business.id).where(Business.owner_id == user.id))).all())
    ids |= set((await db.scalars(select(BusinessOwner.business_id).where(BusinessOwner.user_id == user.id))).all())
    if user.telegram_id:
        ids |= set((await db.scalars(
            select(Employee.business_id).join(Business, Business.id == Employee.business_id).where(
                Employee.telegram_id == user.telegram_id, Employee.role == "administrator",
                Employee.is_active.is_(True), Business.studio_admin_access.is_(True)))).all())
    result = []
    for business_id in sorted(ids):
        role = await studio_role(db, user.id, business_id)
        business = await db.get(Business, business_id)
        if role and business:
            result.append(StudioBusinessOut(id=business_id, name=business.name or f"№{business_id}", role=role))
    return result


class TicketIn(BaseModel):
    business_id: int


class TicketOut(BaseModel):
    url: str
    expires_in: int = TICKET_TTL_SECONDS


@router.post("/studio/ticket", response_model=TicketOut)
async def studio_ticket(data: TicketIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """One-time link into the studio for a business the person is allowed to manage."""
    role = await studio_role(db, user.id, data.business_id)
    if role is None:
        raise HTTPException(status_code=403, detail="No access to the marketing studio for this business")
    business = await db.get(Business, data.business_id)
    ticket = make_ticket(user_id=user.id, business_id=business.id, role=role, name=business.name or "")
    return TicketOut(url=f"{studio_public_url()}/studio/sso?t={ticket}")


class AccessOut(BaseModel):
    allowed: bool
    role: Optional[str] = None


@router.get("/internal/studio/access", response_model=AccessOut)
async def studio_access(user_id: int, business_id: int, x_studio_key: str = Header(default=""),
                        db: AsyncSession = Depends(get_db)):
    """Server-to-server check used by the studio on every request. Answers only yes/no and a role."""
    expected = _secret("STUDIO_INTERNAL_KEY")
    if not hmac.compare_digest(x_studio_key.encode(), expected):
        raise HTTPException(status_code=403, detail="Forbidden")
    role = await studio_role(db, user_id, business_id)
    return AccessOut(allowed=role is not None, role=role)


class ServiceOut(BaseModel):
    name: str
    description: Optional[str] = None
    tariff: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[int] = None
    per_person: bool = False


class ResourceOut(BaseModel):
    """A hall, boat or room of a rental business — shown to clients on the booking page."""
    name: str
    capacity: Optional[int] = None
    area_sqm: Optional[int] = None
    description: Optional[str] = None


class BusinessProfileOut(BaseModel):
    name: str
    business_type: str
    description: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    working_hours: Optional[str] = None
    break_hours: Optional[str] = None
    prepayment_percent: Optional[int] = None
    services: list[ServiceOut]
    resources: list[ResourceOut] = []


def _hours(start, end) -> Optional[str]:
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}" if start and end else None


@router.get("/internal/studio/business", response_model=BusinessProfileOut)
async def studio_business(business_id: int, user_id: int, x_studio_key: str = Header(default=""),
                          db: AsyncSession = Depends(get_db)):
    """What the studio needs to start a company profile: only what the business shows publicly.
    No clients, bookings, payment details or staff. Only for someone who owns the business."""
    expected = _secret("STUDIO_INTERNAL_KEY")
    if not hmac.compare_digest(x_studio_key.encode(), expected):
        raise HTTPException(status_code=403, detail="Forbidden")
    if await studio_role(db, user_id, business_id) not in ("owner", "co_owner"):
        raise HTTPException(status_code=403, detail="Forbidden")
    business = await db.get(Business, business_id)
    services = (await db.scalars(select(Service).where(
        Service.business_id == business_id, Service.is_active.is_(True), Service.is_additional.is_(False)
    ).order_by(Service.sort_order, Service.id))).all()
    resources = []
    if business.business_type == "venue_rental":
        # Rental halls are stored as employees; for other businesses employees are people and are not sent.
        halls = (await db.scalars(select(Employee).where(
            Employee.business_id == business_id, Employee.is_active.is_(True), Employee.role != "administrator"
        ).order_by(Employee.id))).all()
        resources = [ResourceOut(name=hall.name, capacity=hall.capacity, area_sqm=hall.area_sqm,
                                 description=hall.description) for hall in halls]
    return BusinessProfileOut(
        resources=resources,
        name=business.name, business_type=business.business_type, description=business.description,
        address=business.address, phone=business.phone,
        working_hours=_hours(business.working_hours_start, business.working_hours_end),
        break_hours=_hours(business.break_start, business.break_end),
        prepayment_percent=business.prepayment_percent if business.prepayment_enabled else None,
        services=[ServiceOut(name=item.name, description=item.description, tariff=item.tariff_description,
                             duration_minutes=item.duration_minutes or None, price=item.price,
                             per_person=item.pricing_type == "per_person") for item in services])
