"""
Admin routes: dashboard, booking management, analytics.
All routes require the user to be a business owner.
"""

from datetime_utils import utc_now
from datetime import date, datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from typing import List, Optional
import io
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile
from fastapi.responses import StreamingResponse
import json
import httpx
from sqlalchemy import select, func, and_, case, delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..photo_transport import create_photo_client
from ..database import (
    get_db, Business, Service, Employee, Booking, User, BusinessOwner,
    VenueTimePricing, Subscription, SubscriptionService, EmployeeService,
)
from database.crud import get_or_create_guest_user
from notification_rules import normalize_reminder_minutes
from database.booking_safety import (
    ACTIVE_BOOKING_STATUSES,
    BookingConflictError,
    booking_resource_duration,
    ensure_resource_available,
    lock_resource_days,
    service_occupies_resource,
    contiguous_visit_bookings,
)
from ..deps import get_current_admin
from database.booking_changes import BookingChangeError, actual_end_time, change_booking_service
from ..booking_utils import compute_recurring_dates
from ..schemas import (
    BusinessOut,
    BusinessCreateIn,
    BusinessUpdateIn,
    BusinessAdminDetailOut,
    DashboardOut,
    AdminBookingOut,
    ServiceOut,
    ServiceCreateIn,
    ServiceUpdateIn,
    ServiceReorderIn,
    EmployeeOut,
    EmployeeAdminOut,
    EmployeeCreateIn,
    EmployeeUpdateIn,
    EmployeeServiceLinkIn,
    EmployeeServiceOut,
    AnalyticsPointOut,
    AnalyticsKPIs,
    AnalyticsTopItem,
    AnalyticsDailyPoint,
    AnalyticsPrepaymentStats,
    AnalyticsHeatmapCell,
    AnalyticsOverviewOut,
    ManualBookingIn,
    UpdateBookingStatusIn,
    ChangeBookingServiceIn,
    AdminRescheduleIn,
    OwnerOut,
    OwnerAddIn,
    TimePricingCreateIn,
    TimePricingAdminOut,
    SubscriptionCreateIn,
    SubscriptionUpdateIn,
    SubscriptionAdminOut,
    SubscriptionServiceOut,
)

logger = logging.getLogger(__name__)

router = APIRouter()


BOOKING_LABEL_KEYS = {"service", "resource", "additional_services", "date", "time"}


def _parse_booking_labels(raw) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, label in value.items():
        if key in BOOKING_LABEL_KEYS and isinstance(label, str) and label.strip():
            result[key] = label.strip()[:80]
    return result


def _normalize_booking_labels(value: Optional[dict[str, str]]) -> dict[str, str]:
    if not value:
        return {}
    result: dict[str, str] = {}
    for key, label in value.items():
        if key not in BOOKING_LABEL_KEYS or not isinstance(label, str):
            continue
        clean = label.strip()
        if clean:
            result[key] = clean[:80]
    return result


def _normalize_reminder_minutes(value) -> list[int]:
    return normalize_reminder_minutes(value)


def _business_admin_out(business: Business) -> BusinessAdminDetailOut:
    data = {column.name: getattr(business, column.name) for column in Business.__table__.columns}
    data["booking_labels"] = _parse_booking_labels(getattr(business, "booking_labels", None))
    data["owner_reminder_minutes"] = _normalize_reminder_minutes(
        getattr(business, "owner_reminder_minutes", None)
    )
    data["master_reminder_minutes"] = _normalize_reminder_minutes(
        getattr(business, "master_reminder_minutes", None)
    )
    order = getattr(business, "booking_flow_order", "service_first") or "service_first"
    data["booking_flow_order"] = order if order in ("service_first", "resource_first") else "service_first"
    return BusinessAdminDetailOut(**data)


async def _get_owner_business_ids(
    db: AsyncSession, user: User
) -> List[int]:
    """Return all business IDs owned by the user (including co-ownership via BusinessOwner)."""
    # Legacy: Business.owner_id
    result1 = await db.execute(
        select(Business.id).where(Business.owner_id == user.id)
    )
    ids = {row[0] for row in result1.all()}
    # Junction table: BusinessOwner
    result2 = await db.execute(
        select(BusinessOwner.business_id).where(BusinessOwner.user_id == user.id)
    )
    ids.update(row[0] for row in result2.all())
    return list(ids)


def _admin_booking_out(b: Booking) -> AdminBookingOut:
    return AdminBookingOut(
        id=b.id,
        client_name=b.client.full_name if b.client else "Unknown",
        client_phone=b.client.phone if b.client else None,
        client_email=b.client.email if b.client else None,
        service_id=b.service_id,
        service_name=b.service.name if b.service else "",
        service_duration=b.duration_minutes or (b.service.duration_minutes if b.service else None),
        actual_end_time=actual_end_time(b),
        price=b.estimated_price if b.duration_minutes is not None else (b.service.price if b.service else None),
        is_rental=b.duration_minutes is not None,
        price_requires_confirmation=b.price_requires_confirmation,
        prepayment_amount=b.prepayment_amount,
        employee_name=b.employee.name if b.employee else None,
        employee_id=b.employee.id if b.employee else None,
        date=b.booking_date,
        time=b.booking_time.strftime("%H:%M"),
        status=b.status,
        notes=b.notes,
        created_at=b.created_at,
        version=b.version,
    )


def _service_out(service: Service) -> ServiceOut:
    """Convert Service model to ServiceOut, parsing group_pricing JSON."""
    data = {
        "id": service.id,
        "name": service.name,
        "description": service.description,
        "duration_minutes": service.duration_minutes,
        "price": service.price,
        "is_active": service.is_active,
        "is_additional": service.is_additional,
        "is_hourly_price": service.is_hourly_price,
        "sort_order": service.sort_order,
        "pricing_type": getattr(service, 'pricing_type', 'fixed') or 'fixed',
    }
    gp = getattr(service, 'group_pricing', None)
    if gp:
        try:
            data["group_pricing"] = json.loads(gp) if isinstance(gp, str) else gp
        except (json.JSONDecodeError, TypeError):
            data["group_pricing"] = None
    return ServiceOut(**data)


# ---------- GET /admin/dashboard ----------

@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Today's stats across all businesses owned by the user."""
    biz_ids = await _get_owner_business_ids(db, user)
    today = date.today()

    result = await db.execute(
        select(
            func.count(case((Booking.occupies_resource.is_(True), Booking.id))),
            func.coalesce(
                func.sum(
                    case(
                        (Booking.status.in_(["confirmed", "completed"]), func.coalesce(Booking.estimated_price, Service.price, 0)),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((and_(Booking.status == "pending", Booking.occupies_resource.is_(True)), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((and_(Booking.status == "confirmed", Booking.occupies_resource.is_(True)), 1), else_=0)), 0
            ),
        )
        .join(Service, Booking.service_id == Service.id)
        .where(
            Booking.business_id.in_(biz_ids),
            Booking.booking_date == today,
        )
    )
    row = result.one()

    return DashboardOut(
        today_bookings=row[0],
        total_revenue=row[1] or 0,
        pending_count=row[2] or 0,
        confirmed_count=row[3] or 0,
    )


# ---------- GET /admin/bookings ----------

@router.get("/bookings", response_model=List[AdminBookingOut])
async def list_bookings(
    date: Optional[date] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    business_id: Optional[int] = Query(None),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List bookings for the owner's businesses with optional filters."""
    biz_ids = await _get_owner_business_ids(db, user)

    if business_id:
        if business_id not in biz_ids:
            raise HTTPException(status_code=403, detail="Not your business")
        biz_ids = [business_id]

    filters = [Booking.business_id.in_(biz_ids)]
    if date:
        filters.append(Booking.booking_date == date)
    elif start_date and end_date:
        filters.append(Booking.booking_date >= start_date)
        filters.append(Booking.booking_date <= end_date)
    if status:
        filters.append(Booking.status == status)

    result = await db.execute(
        select(Booking)
        .where(and_(*filters))
        .options(
            selectinload(Booking.client),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .order_by(Booking.booking_date.desc(), Booking.booking_time.desc())
        .limit(200)
    )
    bookings = list(result.scalars().all())
    return [_admin_booking_out(b) for b in bookings]


# ---------- GET /admin/businesses/{id}/services ----------

@router.get("/businesses/{business_id}/services", response_model=List[ServiceOut])
async def list_services(
    business_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List services for a business owned by the current user."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Service).where(
            Service.business_id == business_id,
            Service.is_active == True,
        )
    )
    return [_service_out(s) for s in result.scalars().all()]


def _clean_access_email(value: Optional[str]) -> Optional[str]:
    """Email мастера для входа: приводим к нижнему регистру, пустое значение = доступа нет."""
    from email_utils import normalize_optional_email
    try:
        return normalize_optional_email(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# ---------- GET /admin/businesses/{id}/employees ----------

@router.get("/businesses/{business_id}/employees", response_model=List[EmployeeAdminOut])
async def list_employees(
    business_id: int,
    include_inactive: bool = Query(False),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List employees for a business owned by the current user."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    filters = [Employee.business_id == business_id]
    if not include_inactive:
        filters.append(Employee.is_active == True)

    result = await db.execute(
        select(Employee)
        .where(and_(*filters))
        .options(selectinload(Employee.employee_services))
    )
    employees = result.scalars().all()
    out = []
    for e in employees:
        d = EmployeeAdminOut.model_validate(e)
        d.service_ids = [es.service_id for es in e.employee_services]
        out.append(d)
    return out


# ---------- GET /admin/analytics/{business_id} ----------

@router.get("/analytics/{business_id}", response_model=List[AnalyticsPointOut])
async def analytics(
    business_id: int,
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bookings per day for the last N days (default 30)."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    start_date = date.today() - timedelta(days=days)

    result = await db.execute(
        select(
            Booking.booking_date,
            func.count(Booking.id),
        )
        .where(
            Booking.business_id == business_id,
            Booking.booking_date >= start_date,
            Booking.status.in_(["confirmed", "completed"]),
        )
        .group_by(Booking.booking_date)
        .order_by(Booking.booking_date)
    )

    return [
        AnalyticsPointOut(date=row[0], count=row[1])
        for row in result.all()
    ]


# ---------- GET /admin/analytics/{business_id}/overview ----------

@router.get("/analytics/{business_id}/overview", response_model=AnalyticsOverviewOut)
async def analytics_overview(
    business_id: int,
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """KPIs, top services/employees, daily breakdown, cancellations,
    prepayment conversion, and a fixed-90-day load heatmap."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    today = date.today()
    start_date = today - timedelta(days=days - 1)
    end_date = today
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    # ---- load business for working_hours ----
    biz_result = await db.execute(select(Business).where(Business.id == business_id))
    business = biz_result.scalar_one_or_none()

    # ---- helper: fetch bookings with service+employee for a date range, filtered by status ----
    async def _fetch_by_status(d_start: date, d_end: date, statuses: list[str]):
        res = await db.execute(
            select(Booking)
            .where(
                Booking.business_id == business_id,
                Booking.booking_date >= d_start,
                Booking.booking_date <= d_end,
                Booking.status.in_(statuses),
            )
            .options(selectinload(Booking.service), selectinload(Booking.employee))
        )
        return res.scalars().all()

    cur_bookings = await _fetch_by_status(start_date, end_date, ["confirmed", "completed"])
    prev_bookings = await _fetch_by_status(prev_start, prev_end, ["confirmed", "completed"])
    cur_cancelled = await _fetch_by_status(start_date, end_date, ["cancelled"])
    prev_cancelled = await _fetch_by_status(prev_start, prev_end, ["cancelled"])

    # Legacy API field names remain for compatibility. Values are booked-service
    # price ESTIMATES, not captured payments, and add-ons are not extra visits.
    def _calc_revenue(bookings):
        return sum(
            b.estimated_price if b.estimated_price is not None
            else (b.service.price or 0 if b.service else 0)
            for b in bookings
        )

    def _calc_booked_minutes(bookings):
        return sum(
            (b.resource_duration_minutes or b.duration_minutes
             or (b.service.duration_minutes if b.service else None) or 60)
            for b in bookings if b.occupies_resource
        )

    cur_revenue = _calc_revenue(cur_bookings)
    cur_count = sum(1 for b in cur_bookings if b.occupies_resource)
    cur_avg = round(cur_revenue / cur_count) if cur_count else 0

    prev_revenue = _calc_revenue(prev_bookings)
    prev_count = sum(1 for b in prev_bookings if b.occupies_resource)
    prev_avg = round(prev_revenue / prev_count) if prev_count else 0

    def _cancellation_rate(cancelled_list, active_count):
        total = len(cancelled_list) + active_count
        return round(len(cancelled_list) / total * 100, 1) if total > 0 else 0.0

    cur_cancelled = [b for b in cur_cancelled if b.occupies_resource]
    prev_cancelled = [b for b in prev_cancelled if b.occupies_resource]
    cur_cancellation_rate = _cancellation_rate(cur_cancelled, cur_count)
    prev_cancellation_rate = _cancellation_rate(prev_cancelled, prev_count)

    # ---- load_percent ----
    # Fetch active employee count once
    emp_count_res = await db.execute(
        select(func.count(Employee.id)).where(
            Employee.business_id == business_id,
            Employee.is_active == True,
            Employee.role.notin_(["administrator", "admin"]),
        )
    )
    active_emp_count = emp_count_res.scalar() or 0

    def _load_percent(bookings, n_days):
        if (
            business is None
            or business.working_hours_start is None
            or business.working_hours_end is None
        ):
            return None
        wh_start = business.working_hours_start
        wh_end = business.working_hours_end
        working_hours = (
            wh_end.hour + wh_end.minute / 60.0
            - wh_start.hour - wh_start.minute / 60.0
        )
        if working_hours <= 0 or active_emp_count == 0:
            return None
        available_minutes = n_days * working_hours * active_emp_count * 60
        booked_minutes = _calc_booked_minutes(bookings)
        return round(booked_minutes / available_minutes * 100, 1)

    cur_load = _load_percent(cur_bookings, days)
    prev_load = _load_percent(prev_bookings, days)

    kpis = AnalyticsKPIs(
        revenue=cur_revenue,
        bookings=cur_count,
        avg_check=cur_avg,
        load_percent=cur_load,
        revenue_prev=prev_revenue,
        bookings_prev=prev_count,
        avg_check_prev=prev_avg,
        load_percent_prev=prev_load,
        cancellation_rate=cur_cancellation_rate,
        cancelled_count=len(cur_cancelled),
        cancellation_rate_prev=prev_cancellation_rate,
        cancelled_count_prev=len(prev_cancelled),
    )

    # ---- top services ----
    svc_map: dict[int, dict] = {}
    for b in cur_bookings:
        if b.service is None:
            continue
        sid = b.service.id
        if sid not in svc_map:
            svc_map[sid] = {"id": sid, "name": b.service.name, "count": 0, "revenue": 0}
        svc_map[sid]["count"] += 1
        svc_map[sid]["revenue"] += b.estimated_price if b.estimated_price is not None else (b.service.price or 0)

    svc_list = sorted(
        svc_map.values(),
        key=lambda x: (x["revenue"], x["count"]),
        reverse=True,
    )[:5]
    top_services = [
        AnalyticsTopItem(
            id=s["id"],
            name=s["name"],
            count=s["count"],
            revenue=s["revenue"],
            share_percent=round(s["revenue"] / cur_revenue * 100, 1) if cur_revenue > 0 else 0.0,
        )
        for s in svc_list
    ]

    # ---- top employees ----
    emp_map: dict[int, dict] = {}
    for b in cur_bookings:
        if b.employee is None:
            continue
        eid = b.employee.id
        if eid not in emp_map:
            emp_map[eid] = {"id": eid, "name": b.employee.name, "count": 0, "revenue": 0}
        if b.occupies_resource:
            emp_map[eid]["count"] += 1
        if b.service:
            emp_map[eid]["revenue"] += b.estimated_price if b.estimated_price is not None else (b.service.price or 0)

    emp_list = sorted(
        emp_map.values(),
        key=lambda x: (x["revenue"], x["count"]),
        reverse=True,
    )[:5]
    top_employees = [
        AnalyticsTopItem(
            id=e["id"],
            name=e["name"],
            count=e["count"],
            revenue=e["revenue"],
            share_percent=round(e["revenue"] / cur_revenue * 100, 1) if cur_revenue > 0 else 0.0,
        )
        for e in emp_list
    ]

    # ---- daily breakdown (bookings + revenue + cancelled) ----
    daily_map: dict[date, dict] = {}
    for b in cur_bookings:
        d = b.booking_date
        if d not in daily_map:
            daily_map[d] = {"bookings": 0, "revenue": 0, "cancelled": 0}
        if b.occupies_resource:
            daily_map[d]["bookings"] += 1
        if b.service:
            daily_map[d]["revenue"] += b.estimated_price if b.estimated_price is not None else (b.service.price or 0)
    for b in cur_cancelled:
        d = b.booking_date
        if d not in daily_map:
            daily_map[d] = {"bookings": 0, "revenue": 0, "cancelled": 0}
        daily_map[d]["cancelled"] += 1

    daily = []
    current = start_date
    while current <= end_date:
        entry = daily_map.get(current, {"bookings": 0, "revenue": 0, "cancelled": 0})
        daily.append(AnalyticsDailyPoint(
            date=current, bookings=entry["bookings"], revenue=entry["revenue"], cancelled=entry["cancelled"],
        ))
        current += timedelta(days=1)

    # ---- prepayment conversion (current period, ALL statuses) ----
    all_period_bookings = await _fetch_by_status(
        start_date, end_date,
        ["pending", "confirmed", "completed", "cancelled", "pending_prepayment", "prepayment_sent"],
    )
    required = [b for b in all_period_bookings if b.occupies_resource and b.prepayment_amount is not None]
    confirmed_pp = [b for b in required if b.prepayment_confirmed_at is not None]
    conversion_rate = round(len(confirmed_pp) / len(required) * 100, 1) if required else 0.0
    if confirmed_pp:
        total_minutes = sum(
            (b.prepayment_confirmed_at - b.created_at).total_seconds() / 60.0
            for b in confirmed_pp
            if b.created_at is not None
        )
        avg_confirmation_minutes = round(total_minutes / len(confirmed_pp), 1)
    else:
        avg_confirmation_minutes = None

    prepayment = AnalyticsPrepaymentStats(
        required_count=len(required),
        confirmed_count=len(confirmed_pp),
        conversion_rate=conversion_rate,
        avg_confirmation_minutes=avg_confirmation_minutes,
    )

    # ---- heatmap: fixed last 90 days, confirmed+completed only ----
    heatmap_start = today - timedelta(days=89)
    heatmap_bookings = await _fetch_by_status(heatmap_start, today, ["confirmed", "completed"])
    heatmap_map: dict[tuple[int, int], int] = {}
    for b in heatmap_bookings:
        if not b.occupies_resource:
            continue
        dow = b.booking_date.weekday()
        hour = b.booking_time.hour
        key = (dow, hour)
        heatmap_map[key] = heatmap_map.get(key, 0) + 1
    heatmap = [
        AnalyticsHeatmapCell(day_of_week=dow, hour=hour, count=count)
        for (dow, hour), count in heatmap_map.items()
    ]

    return AnalyticsOverviewOut(
        kpis=kpis,
        top_services=top_services,
        top_employees=top_employees,
        daily=daily,
        prepayment=prepayment,
        heatmap=heatmap,
    )


# ---------- POST /admin/bookings (manual booking) ----------

@router.post("/bookings", response_model=List[AdminBookingOut])
async def create_manual_booking(
    data: ManualBookingIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create one or recurring manual bookings with the same safety as clients."""
    biz_ids = await _get_owner_business_ids(db, user)
    if data.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    try:
        h, m = map(int, data.time.split(":"))
        booking_time = dt_time(h, m)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid time")

    service = (
        await db.execute(
            select(Service).where(
                Service.id == data.service_id,
                Service.business_id == data.business_id,
                Service.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if service.is_additional:
        raise HTTPException(status_code=400, detail="Additional service cannot be booked separately")

    if data.employee_id:
        employee = (
            await db.execute(
                select(Employee).where(
                    Employee.id == data.employee_id,
                    Employee.business_id == data.business_id,
                    Employee.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee/resource not found")

    occurrence_dates = compute_recurring_dates(data.date, data.repeat_weeks or 1)
    guest_user = await get_or_create_guest_user(db, data.client_name, data.client_phone)

    resource_duration = service.duration_minutes or 30
    await lock_resource_days(db, data.employee_id, occurrence_dates)
    for occ_date in occurrence_dates:
        try:
            await ensure_resource_available(
                db,
                employee_id=data.employee_id,
                booking_date=occ_date,
                booking_time=booking_time,
                duration_minutes=resource_duration,
                acquire_lock=False,
            )
        except BookingConflictError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Занято: {occ_date.strftime('%d.%m')} {data.time}",
            ) from exc

    created_ids: list[int] = []
    for occ_date in occurrence_dates:
        booking = Booking(
            business_id=data.business_id,
            client_id=guest_user.id,
            service_id=data.service_id,
            employee_id=data.employee_id,
            booking_date=occ_date,
            booking_time=booking_time,
            status="confirmed",
            notes=data.notes,
            notify_token=str(uuid.uuid4()),
            resource_duration_minutes=resource_duration,
            occupies_resource=service_occupies_resource(service),
        )
        db.add(booking)
        await db.flush()
        created_ids.append(booking.id)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        message = str(exc).lower()
        if "bookings_no_active_resource_overlap" in message or "exclusion" in message:
            raise HTTPException(
                status_code=409,
                detail="На выбранное время уже есть другая запись",
            ) from exc
        raise

    result = await db.execute(
        select(Booking)
        .where(Booking.id.in_(created_ids))
        .options(
            selectinload(Booking.client),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .order_by(Booking.booking_date)
    )
    bookings = list(result.scalars().all())

    try:
        from bot.services.notification_events import dispatch_booking_event
        # Recurring manual bookings are distinct visits, so notify each one
        # independently rather than presenting the whole series as one visit.
        for created in bookings:
            await dispatch_booking_event(
                [created],
                db,
                "booking_created",
                include_client=True,
                staff_audience="all",
            )
    except Exception as notification_error:
        logger.error(
            "Manual booking notification failed ids=%s: %s",
            [item.id for item in bookings],
            notification_error,
            exc_info=True,
        )

    return [_admin_booking_out(b) for b in bookings]


# ---------- PATCH /admin/bookings/{id}/status ----------

@router.patch("/bookings/{booking_id}/status", response_model=AdminBookingOut)
async def update_booking_status(
    booking_id: int,
    data: UpdateBookingStatusIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a whole visit status and emit the appropriate system event."""
    if data.status not in ("confirmed", "completed", "cancelled", "pending"):
        raise HTTPException(status_code=400, detail="Invalid status")

    biz_ids = await _get_owner_business_ids(db, user)
    result = await db.execute(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.business),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    old_status = booking.status
    if old_status in ("pending_prepayment", "prepayment_sent") and data.status in ("pending", "completed"):
        raise HTTPException(409, "Сначала подтвердите поступление предоплаты или отклоните сообщение об оплате")
    if data.status == "confirmed" and booking.duration_minutes is not None and (
        booking.price_requires_confirmation or booking.prepayment_amount is None
    ):
        raise HTTPException(
            status_code=409,
            detail="Укажите окончательную цену и предоплату (0 ₽, если не требуется)",
        )

    visit_statuses = (
        ("prepayment_sent", "pending_prepayment")
        if old_status in ("prepayment_sent", "pending_prepayment")
        else (old_status,)
    )
    visit = await contiguous_visit_bookings(db, booking, statuses=visit_statuses)
    visit_ids = [item.id for item in visit]
    visit = list((await db.execute(select(Booking).where(Booking.id.in_(visit_ids)).order_by(Booking.id)
        .with_for_update().execution_options(populate_existing=True))).scalars())
    if booking.status != old_status:
        raise HTTPException(409, "Статус записи уже изменился. Обновите страницу")

    payment_reviewed = False
    if data.status == "confirmed" and old_status in ("prepayment_sent", "pending_prepayment"):
        from database.prepayment import review_payment, PaymentWindowError
        try:
            visit = await review_payment(db, booking, user.id, "confirm")
            payment_reviewed = True
        except PaymentWindowError as exc:
            raise HTTPException(exc.status_code, exc.detail) from None

    target_is_active = data.status in ACTIVE_BOOKING_STATUSES
    current_is_active = old_status in ACTIVE_BOOKING_STATUSES
    if target_is_active and not current_is_active and booking.occupies_resource:
        await lock_resource_days(db, booking.employee_id, [booking.booking_date])
        visit_duration = sum(
            booking_resource_duration(item)
            for item in visit
            if getattr(item, "occupies_resource", True)
        )
        try:
            await ensure_resource_available(
                db,
                employee_id=booking.employee_id,
                booking_date=booking.booking_date,
                booking_time=min(item.booking_time for item in visit),
                duration_minutes=visit_duration or booking_resource_duration(booking),
                exclude_booking_ids=visit_ids,
                acquire_lock=False,
            )
        except BookingConflictError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Нельзя вернуть запись: это время уже занято",
            ) from exc

    confirmed_at = utc_now()
    for item in visit:
        if not payment_reviewed:
            if data.status == "cancelled" and old_status in ("prepayment_sent", "pending_prepayment"):
                from database.models import PrepaymentReview
                db.add(PrepaymentReview(booking_id=item.id, reviewer_id=user.id,
                    decision="cancel", booking_version=item.version))
            item.status = data.status
            item.version += 1
        # Запоминаем момент завершения: в расписании время освободится именно с него.
        item.finished_at = confirmed_at if data.status == "completed" else None
        if (
            data.status == "confirmed"
            and old_status in ("prepayment_sent", "pending_prepayment")
        ):
            item.prepayment_confirmed_at = confirmed_at

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        message = str(exc).lower()
        if "bookings_no_active_resource_overlap" in message or "exclusion" in message:
            raise HTTPException(
                status_code=409,
                detail="Нельзя вернуть запись: это время уже занято",
            ) from exc
        raise

    event_kind = None
    if data.status == "cancelled" and old_status != "cancelled":
        event_kind = "booking_cancelled"
    elif (
        data.status == "confirmed"
        and old_status in ("prepayment_sent", "pending_prepayment")
    ):
        # This is the moment a prepayment-gated booking becomes real.
        event_kind = "booking_created"

    if event_kind:
        try:
            from bot.services.notification_events import dispatch_booking_event
            await dispatch_booking_event(
                visit,
                db,
                event_kind,
                include_client=True,
                staff_audience="all",
            )
        except Exception as notification_error:
            logger.error(
                "Admin status notification failed booking=%s event=%s: %s",
                booking_id,
                event_kind,
                notification_error,
                exc_info=True,
            )

    refreshed = (
        await db.execute(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.employee),
            )
        )
    ).scalar_one()
    return _admin_booking_out(refreshed)


# ---------- PATCH /admin/bookings/{id}/service ----------

@router.patch("/bookings/{booking_id}/service", response_model=AdminBookingOut)
async def change_booking_service_route(
    booking_id: int,
    data: ChangeBookingServiceIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Заменить услугу в записи, если клиент выбрал не ту."""
    biz_ids = await _get_owner_business_ids(db, user)
    booking = (await db.execute(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.business),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .with_for_update()
    )).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")
    try:
        await change_booking_service(db, booking, data.service_id)
        await db.commit()
    except BookingChangeError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Это время уже занято") from exc
    refreshed = (await db.execute(
        select(Booking).where(Booking.id == booking_id).options(
            selectinload(Booking.client), selectinload(Booking.business),
            selectinload(Booking.service), selectinload(Booking.employee),
        ).execution_options(populate_existing=True)
    )).scalar_one()
    return _admin_booking_out(refreshed)


# ---------- POST /admin/bookings/{id}/reschedule ----------

@router.post("/bookings/{booking_id}/reschedule", response_model=AdminBookingOut)
async def admin_reschedule_booking(
    booking_id: int,
    data: AdminRescheduleIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Move a whole visit atomically and notify every affected party."""
    biz_ids = await _get_owner_business_ids(db, user)

    result = await db.execute(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.business),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
        .with_for_update()
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise HTTPException(status_code=400, detail="Эту запись нельзя перенести")

    visit = await contiguous_visit_bookings(
        db,
        booking,
        statuses=ACTIVE_BOOKING_STATUSES,
    )
    visit_ids = [item.id for item in visit]
    visit = sorted(visit, key=lambda item: (item.booking_time, item.id))
    old_date = visit[0].booking_date
    old_time = visit[0].booking_time

    try:
        h, m = map(int, data.time.split(":"))
        new_time = dt_time(h, m)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid time")

    target_employee_id = (
        data.employee_id if data.employee_id is not None else booking.employee_id
    )
    if target_employee_id:
        target_employee = (
            await db.execute(
                select(Employee).where(
                    Employee.id == target_employee_id,
                    Employee.business_id == booking.business_id,
                    Employee.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not target_employee:
            raise HTTPException(status_code=404, detail="Employee/resource not found")

    visit_duration = sum(
        booking_resource_duration(item)
        for item in visit
        if getattr(item, "occupies_resource", True)
    )
    if visit_duration <= 0:
        visit_duration = booking_resource_duration(booking)

    is_rental = any(item.duration_minutes is not None for item in visit)
    if is_rental:
        if target_employee_id != booking.employee_id:
            raise HTTPException(status_code=400, detail="Для смены судна создайте новую заявку")
        business = await db.get(Business, booking.business_id)
        from web.api.rental_logic import available_starts
        if not business.working_hours_start or not business.working_hours_end:
            raise HTTPException(status_code=409, detail="Расписание не настроено")
        try:
            allowed = available_starts(
                data.date,
                business.working_hours_start,
                business.working_hours_end,
                visit_duration,
                [],
                now=datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if data.time not in allowed:
            raise HTTPException(status_code=409, detail="Время вне рабочих часов или уже прошло")

    resource_ids = sorted(
        {
            resource_id
            for resource_id in (booking.employee_id, target_employee_id)
            if resource_id is not None
        }
    )
    for resource_id in resource_ids:
        await lock_resource_days(db, resource_id, [old_date, data.date])

    try:
        await ensure_resource_available(
            db,
            employee_id=target_employee_id,
            booking_date=data.date,
            booking_time=new_time,
            duration_minutes=visit_duration,
            exclude_booking_ids=visit_ids,
            acquire_lock=False,
        )
    except BookingConflictError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="На это время уже есть запись") from exc

    current_dt = datetime.combine(data.date, new_time)
    for item in visit:
        item.booking_date = data.date
        item.booking_time = current_dt.time()
        if data.employee_id is not None:
            item.employee_id = target_employee_id
        current_dt += timedelta(minutes=booking_resource_duration(item))

    from bot.services.notification_events import clear_upcoming_reminder_deliveries
    await clear_upcoming_reminder_deliveries(visit, db)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        message = str(exc).lower()
        if "bookings_no_active_resource_overlap" in message or "exclusion" in message:
            raise HTTPException(status_code=409, detail="На это время уже есть запись") from exc
        raise

    try:
        from bot.services.notification_events import dispatch_booking_event
        await dispatch_booking_event(
            visit,
            db,
            "booking_rescheduled",
            include_client=True,
            staff_audience="all",
            old_date=old_date,
            old_time=old_time,
        )
    except Exception as notification_error:
        logger.error(
            "Admin reschedule notification failed booking=%s: %s",
            booking_id,
            notification_error,
            exc_info=True,
        )

    refreshed = (
        await db.execute(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.employee),
            )
        )
    ).scalar_one()
    return _admin_booking_out(refreshed)


# ---------- GET /admin/businesses ----------

@router.get("/businesses", response_model=List[BusinessOut])
async def list_admin_businesses(
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all businesses owned by the current admin."""
    biz_ids = await _get_owner_business_ids(db, user)
    result = await db.execute(
        select(Business).where(Business.id.in_(biz_ids))
    )
    businesses = result.scalars().all()
    return [
        BusinessOut(
            id=b.id, name=b.name, description=b.description,
            business_type=b.business_type, address=b.address, phone=b.phone,
            working_hours_start=b.working_hours_start, working_hours_end=b.working_hours_end,
            photos=b.photos, services_count=0,
        )
        for b in businesses
    ]


# ---------- POST /admin/businesses ----------

@router.post("/businesses", response_model=BusinessAdminDetailOut)
async def create_admin_business(
    data: BusinessCreateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create one more business for the current owner — same rules and defaults as the bot's
    «Добавить бизнес»: subscription/limit check, working hours and booking step from the type template."""
    from bot.utils.business_templates import BUSINESS_TEMPLATES, get_template
    from bot.utils.business_types import is_venue_rental_type
    from bot.utils.validators import validate_phone, format_phone
    from database.crud import can_user_create_business, create_business, create_service

    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Введите название бизнеса")
    if data.business_type not in BUSINESS_TEMPLATES:
        raise HTTPException(status_code=422, detail="Неизвестный тип бизнеса")

    phone = (data.phone or "").strip() or None
    if phone:
        if not validate_phone(phone):
            raise HTTPException(
                status_code=422,
                detail="Неверный формат телефона. Используйте +7XXXXXXXXXX или 8XXXXXXXXXX",
            )
        phone = format_phone(phone)

    can_create, reason = await can_user_create_business(db, user.id)
    if not can_create:
        raise HTTPException(status_code=403, detail=reason or "Невозможно создать бизнес")

    template = get_template(data.business_type)
    business = await create_business(
        db,
        owner_id=user.id,
        name=name,
        business_type=data.business_type,
        description=(data.description or "").strip() or template["description"] or None,
        address=(data.address or "").strip() or None,
        phone=phone,
        working_hours_start=template["working_hours_start"],
        working_hours_end=template["working_hours_end"],
        break_start=template["break_start"],
        break_end=template["break_end"],
        booking_interval_minutes=template["booking_interval_minutes"],
    )

    # Для аренды помещений сразу создаём варианты времени аренды — как в боте.
    if is_venue_rental_type(data.business_type):
        for svc in template.get("default_services", []):
            await create_service(
                db, business_id=business.id, name=svc["name"],
                duration_minutes=svc["duration_minutes"], price=svc.get("price"),
            )
        for svc in template.get("default_additional_services", []):
            await create_service(
                db, business_id=business.id, name=svc["name"],
                duration_minutes=svc["duration_minutes"], price=svc.get("price"),
                is_additional=True,
            )

    return _business_admin_out(business)


# ---------- Service CRUD ----------

@router.post("/businesses/{business_id}/services", response_model=ServiceOut)
async def create_service(
    business_id: int,
    data: ServiceCreateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    service = Service(
        business_id=business_id,
        name=data.name,
        description=data.description,
        duration_minutes=data.duration_minutes,
        price=data.price,
        is_active=data.is_active,
        is_additional=data.is_additional,
        is_hourly_price=data.is_hourly_price,
        pricing_type=data.pricing_type or 'fixed',
        group_pricing=json.dumps([t.model_dump() for t in data.group_pricing]) if data.group_pricing else None,
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return _service_out(service)


@router.put("/businesses/{business_id}/services/{service_id}", response_model=ServiceOut)
async def update_service(
    business_id: int,
    service_id: int,
    data: ServiceUpdateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Service).where(Service.id == service_id, Service.business_id == business_id)
    )
    service = result.scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == 'group_pricing' and value is not None:
            setattr(service, field, json.dumps(value))
        else:
            setattr(service, field, value)

    await db.commit()
    await db.refresh(service)
    return _service_out(service)


@router.delete("/businesses/{business_id}/services/{service_id}")
async def delete_service(
    business_id: int,
    service_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Service).where(Service.id == service_id, Service.business_id == business_id)
    )
    service = result.scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    service.is_active = False
    await db.commit()
    return {"message": "Service deactivated"}


# ---------- Employee CRUD ----------

@router.post("/businesses/{business_id}/employees", response_model=EmployeeAdminOut)
async def create_employee(
    business_id: int,
    data: EmployeeCreateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    employee = Employee(
        business_id=business_id,
        name=data.name,
        role=data.role,
        specialization=data.specialization,
        description=data.description,
        telegram_id=data.telegram_id,
        access_email=_clean_access_email(data.access_email),
        booking_interval_minutes=data.booking_interval_minutes,
        capacity=data.capacity,
        area_sqm=data.area_sqm,
        min_booking_minutes=data.min_booking_minutes,
        solo_price_per_hour=data.solo_price_per_hour,
        is_active=True,
    )
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    return EmployeeAdminOut.model_validate(employee)


@router.put("/businesses/{business_id}/employees/{employee_id}", response_model=EmployeeAdminOut)
async def update_employee(
    business_id: int,
    employee_id: int,
    data: EmployeeUpdateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    updates = data.model_dump(exclude_unset=True)
    if "access_email" in updates:
        updates["access_email"] = _clean_access_email(updates["access_email"])
    for field, value in updates.items():
        setattr(employee, field, value)

    await db.commit()
    await db.refresh(employee)
    return EmployeeAdminOut.model_validate(employee)


@router.delete("/businesses/{business_id}/employees/{employee_id}")
async def delete_employee(
    business_id: int,
    employee_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    employee.is_active = False
    await db.commit()
    return {"message": "Employee deactivated"}


# ---------- Business Photo Management ----------

@router.post("/businesses/{business_id}/photo")
async def upload_business_photo(
    business_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    """Upload a photo for the business. Stores file_id via Telegram."""
    owner_biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in owner_biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")
    file_content = await file.read()
    content_type = file.content_type or "image/jpeg"
    filename = file.filename or "photo.jpg"

    # Upload to Telegram to get file_id (send to owner, then delete)
    chat_id = user.telegram_id if user.telegram_id and user.telegram_id > 0 else 437862772
    async with create_photo_client() as http:
        resp = await http.post(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data={"chat_id": str(chat_id)},
            files={"photo": (filename, file_content, content_type)},
        )
        data = resp.json()

    if not data.get("ok"):
        raise HTTPException(status_code=500, detail="Failed to upload photo to Telegram")

    # Get the file_id and clean up the message
    msg_id = data["result"]["message_id"]
    photos_arr = data["result"]["photo"]
    file_id = photos_arr[-1]["file_id"]  # largest size

    # Delete the temp message
    async with create_photo_client() as http:
        await http.post(
            f"https://api.telegram.org/bot{bot_token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": msg_id},
        )

    # Update business photos JSON array
    existing_photos = []
    if business.photos:
        try:
            existing_photos = json.loads(business.photos)
        except (json.JSONDecodeError, TypeError):
            existing_photos = []

    existing_photos.append(file_id)
    business.photos = json.dumps(existing_photos)
    await db.commit()

    return {"file_id": file_id, "photos": existing_photos}


@router.delete("/businesses/{business_id}/photo/{photo_index}")
async def delete_business_photo(
    business_id: int,
    photo_index: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    """Delete a specific photo by index."""
    owner_biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in owner_biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    existing_photos = []
    if business.photos:
        try:
            existing_photos = json.loads(business.photos)
        except (json.JSONDecodeError, TypeError):
            existing_photos = []

    if photo_index < 0 or photo_index >= len(existing_photos):
        raise HTTPException(status_code=404, detail="Photo not found")

    existing_photos.pop(photo_index)
    business.photos = json.dumps(existing_photos) if existing_photos else None
    await db.commit()

    return {"photos": existing_photos}


# ---------- Employee Photo Management ----------

@router.post("/businesses/{business_id}/employees/{employee_id}/photo")
async def upload_employee_photo(
    business_id: int,
    employee_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    """Upload a photo for an employee/room. Stores file_id via Telegram."""
    owner_biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in owner_biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")
    file_content = await file.read()
    content_type = file.content_type or "image/jpeg"
    filename = file.filename or "photo.jpg"

    chat_id = user.telegram_id if user.telegram_id and user.telegram_id > 0 else 437862772
    async with create_photo_client() as http:
        resp = await http.post(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data={"chat_id": str(chat_id)},
            files={"photo": (filename, file_content, content_type)},
        )
        data = resp.json()

    if not data.get("ok"):
        raise HTTPException(status_code=500, detail="Failed to upload photo to Telegram")

    msg_id = data["result"]["message_id"]
    photos_arr = data["result"]["photo"]
    file_id = photos_arr[-1]["file_id"]

    async with create_photo_client() as http:
        await http.post(
            f"https://api.telegram.org/bot{bot_token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": msg_id},
        )

    existing_photos = []
    if employee.photos:
        try:
            existing_photos = json.loads(employee.photos)
            if not isinstance(existing_photos, list):
                existing_photos = []
        except (json.JSONDecodeError, TypeError):
            existing_photos = []

    existing_photos.append(file_id)
    employee.photos = json.dumps(existing_photos)
    await db.commit()

    if not employee.primary_photo:
        employee.primary_photo = file_id
        await db.commit()

    return {"file_id": file_id, "photos": existing_photos, "primary_photo": employee.primary_photo}


@router.put("/businesses/{business_id}/employees/{employee_id}/primary-photo")
async def set_employee_primary_photo(
    business_id: int,
    employee_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    """Choose one of the already uploaded employee/resource photos as the preview."""
    owner_biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in owner_biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    file_id = payload.get("file_id") if isinstance(payload, dict) else None
    if not isinstance(file_id, str) or not file_id:
        raise HTTPException(status_code=422, detail="file_id is required")

    try:
        photos = json.loads(employee.photos) if employee.photos else []
    except (json.JSONDecodeError, TypeError):
        photos = []
    if not isinstance(photos, list) or file_id not in photos:
        raise HTTPException(status_code=422, detail="Photo is not uploaded for this resource")

    employee.primary_photo = file_id
    await db.commit()
    return {"photos": photos, "primary_photo": file_id}


@router.delete("/businesses/{business_id}/employees/{employee_id}/photo/{photo_index}")
async def delete_employee_photo(
    business_id: int,
    employee_id: int,
    photo_index: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    """Delete a specific employee photo by index."""
    owner_biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in owner_biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    existing_photos = []
    if employee.photos:
        try:
            existing_photos = json.loads(employee.photos)
            if not isinstance(existing_photos, list):
                existing_photos = []
        except (json.JSONDecodeError, TypeError):
            existing_photos = []

    if photo_index < 0 or photo_index >= len(existing_photos):
        raise HTTPException(status_code=404, detail="Photo not found")

    removed_photo = existing_photos.pop(photo_index)
    employee.photos = json.dumps(existing_photos) if existing_photos else None
    if employee.primary_photo == removed_photo or employee.primary_photo not in existing_photos:
        employee.primary_photo = existing_photos[0] if existing_photos else None
    await db.commit()

    return {"photos": existing_photos, "primary_photo": employee.primary_photo}


# ---------- GET /admin/businesses/{id}/detail ----------

@router.get("/businesses/{business_id}/detail", response_model=BusinessAdminDetailOut)
async def get_business_detail(
    business_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Full business details for admin editing."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Business).where(Business.id == business_id)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    return _business_admin_out(business)


# ---------- PUT /admin/businesses/{id} ----------

@router.put("/businesses/{business_id}", response_model=BusinessAdminDetailOut)
async def update_business(
    business_id: int,
    data: BusinessUpdateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update business profile and prepayment settings."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Business).where(Business.id == business_id)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    updates = data.model_dump(exclude_unset=True)
    if "booking_labels" in updates:
        updates["booking_labels"] = json.dumps(
            _normalize_booking_labels(updates["booking_labels"]),
            ensure_ascii=False,
        )
    if "booking_flow_order" in updates:
        order = updates["booking_flow_order"] or "service_first"
        if order not in ("service_first", "resource_first"):
            raise HTTPException(status_code=422, detail="Invalid booking_flow_order")
        updates["booking_flow_order"] = order

    if "allow_multiple_services" in updates:
        if updates["allow_multiple_services"] is None:
            updates.pop("allow_multiple_services")
        else:
            updates["allow_multiple_services"] = bool(updates["allow_multiple_services"])

    for reminder_field in ("owner_reminder_minutes", "master_reminder_minutes"):
        if reminder_field in updates:
            normalized = normalize_reminder_minutes(
                updates[reminder_field],
                fallback=None,
            )
            if not normalized:
                raise HTTPException(
                    status_code=422,
                    detail=f"{reminder_field} must contain at least one interval between 5 and 10080 minutes",
                )
            updates[reminder_field] = json.dumps(normalized)

    if updates.get("legal_documents_enabled") is True:
        legal_name = (updates.get("legal_name") if "legal_name" in updates else business.legal_name) or ""
        legal_email = (updates.get("legal_email") if "legal_email" in updates else business.legal_email) or ""
        legal_address = (updates.get("legal_address") if "legal_address" in updates else business.legal_address) or ""
        if not legal_name.strip() or not legal_email.strip() or not legal_address.strip():
            raise HTTPException(
                status_code=422,
                detail="Перед включением юридических документов заполните наименование/ФИО, адрес и email оператора.",
            )
    # Convert time strings to time objects. Accept both "HH:MM" and "HH:MM:SS"
    # (the API serializes Time columns as "HH:MM:SS", and an unedited <input type="time">
    # round-trips that exact string back to us).
    for time_field in ("working_hours_start", "working_hours_end", "break_start", "break_end"):
        if time_field in updates:
            val = updates[time_field]
            if val:
                parts = val.split(":")
                h = int(parts[0])
                m = int(parts[1]) if len(parts) > 1 else 0
                updates[time_field] = dt_time(h, m)
            else:
                updates[time_field] = None

    for field, value in updates.items():
        setattr(business, field, value)

    await db.commit()
    await db.refresh(business)
    return _business_admin_out(business)


# ---------- Service Reorder ----------

@router.post("/businesses/{business_id}/services/reorder")
async def reorder_services(
    business_id: int,
    data: ServiceReorderIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reorder services by providing ordered list of service IDs."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Service).where(Service.business_id == business_id)
    )
    services = {s.id: s for s in result.scalars().all()}

    for idx, sid in enumerate(data.service_ids):
        if sid in services:
            services[sid].sort_order = idx

    await db.commit()
    return {"message": "Reordered"}


# ---------- Employee-Service Links ----------

@router.get("/businesses/{business_id}/employees/{employee_id}/services")
async def get_employee_services(
    business_id: int,
    employee_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get services linked to an employee."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(EmployeeService).join(Employee, EmployeeService.employee_id == Employee.id)
        .where(
            EmployeeService.employee_id == employee_id,
            Employee.business_id == business_id,
        )
        .options(selectinload(EmployeeService.service))
    )
    links = result.scalars().all()
    return [
        EmployeeServiceOut(service_id=es.service_id, service_name=es.service.name)
        for es in links if es.service
    ]


@router.put("/businesses/{business_id}/employees/{employee_id}/services")
async def set_employee_services(
    business_id: int,
    employee_id: int,
    data: EmployeeServiceLinkIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Set services for an employee (replaces existing links)."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    # Verify employee belongs to business
    emp_result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    if not emp_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Employee not found")

    # Delete existing links
    await db.execute(
        sa_delete(EmployeeService).where(EmployeeService.employee_id == employee_id)
    )

    # Create new links
    for sid in data.service_ids:
        db.add(EmployeeService(employee_id=employee_id, service_id=sid))

    await db.commit()
    return {"message": "Services updated"}


# ---------- Venue Time Pricing ----------

@router.get("/businesses/{business_id}/employees/{employee_id}/time-pricing", response_model=List[TimePricingAdminOut])
async def get_time_pricing(
    business_id: int,
    employee_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(VenueTimePricing).join(Employee, VenueTimePricing.employee_id == Employee.id)
        .where(
            VenueTimePricing.employee_id == employee_id,
            Employee.business_id == business_id,
        )
    )
    return [
        TimePricingAdminOut(
            id=tp.id,
            time_from=tp.time_from.strftime("%H:%M"),
            time_to=tp.time_to.strftime("%H:%M"),
            price_per_hour=tp.price_per_hour,
            day_type=tp.day_type,
        )
        for tp in result.scalars().all()
    ]


@router.post("/businesses/{business_id}/employees/{employee_id}/time-pricing", response_model=TimePricingAdminOut)
async def add_time_pricing(
    business_id: int,
    employee_id: int,
    data: TimePricingCreateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    emp_result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    if not emp_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Employee not found")

    h1, m1 = map(int, data.time_from.split(":"))
    h2, m2 = map(int, data.time_to.split(":"))

    tp = VenueTimePricing(
        employee_id=employee_id,
        time_from=dt_time(h1, m1),
        time_to=dt_time(h2, m2),
        price_per_hour=data.price_per_hour,
        day_type=data.day_type,
    )
    db.add(tp)
    await db.commit()
    await db.refresh(tp)

    return TimePricingAdminOut(
        id=tp.id,
        time_from=tp.time_from.strftime("%H:%M"),
        time_to=tp.time_to.strftime("%H:%M"),
        price_per_hour=tp.price_per_hour,
        day_type=tp.day_type,
    )


@router.delete("/businesses/{business_id}/employees/{employee_id}/time-pricing/{pricing_id}")
async def delete_time_pricing(
    business_id: int,
    employee_id: int,
    pricing_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(VenueTimePricing).join(Employee, VenueTimePricing.employee_id == Employee.id)
        .where(
            VenueTimePricing.id == pricing_id,
            VenueTimePricing.employee_id == employee_id,
            Employee.business_id == business_id,
        )
    )
    tp = result.scalar_one_or_none()
    if not tp:
        raise HTTPException(status_code=404, detail="Pricing not found")

    await db.delete(tp)
    await db.commit()
    return {"message": "Deleted"}


# ---------- Duty Toggle ----------

@router.post("/businesses/{business_id}/employees/{employee_id}/toggle-duty")
async def toggle_duty(
    business_id: int,
    employee_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Toggle employee on-duty status for today."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    today = date.today()
    if emp.on_duty_date == today:
        emp.on_duty_date = None
        on_duty = False
    else:
        emp.on_duty_date = today
        on_duty = True

    await db.commit()
    return {"on_duty": on_duty, "date": str(today)}


@router.get("/businesses/{business_id}/employees/{employee_id}/duty-schedule")
async def get_duty_schedule(
    business_id: int,
    employee_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get duty schedule (list of dates) for an employee."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    schedule = []
    raw = getattr(emp, 'duty_schedule', None)
    if raw:
        try:
            schedule = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"dates": schedule}


@router.put("/businesses/{business_id}/employees/{employee_id}/duty-schedule")
async def set_duty_schedule(
    business_id: int,
    employee_id: int,
    data: dict,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Set duty schedule (list of date strings) for an employee."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    dates = data.get("dates", [])
    # Validate date format
    valid_dates = []
    for d in dates:
        try:
            date.fromisoformat(str(d))
            valid_dates.append(str(d))
        except (ValueError, TypeError):
            pass

    emp.duty_schedule = json.dumps(valid_dates) if valid_dates else None
    await db.commit()
    return {"dates": valid_dates}


# ---------- Co-Owners ----------

@router.get("/businesses/{business_id}/owners", response_model=List[OwnerOut])
async def list_owners(
    business_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(BusinessOwner)
        .where(BusinessOwner.business_id == business_id)
        .options(selectinload(BusinessOwner.user))
    )
    owners = result.scalars().all()
    out = []
    for o in owners:
        out.append(OwnerOut(
            id=o.id,
            user_id=o.user_id,
            role=o.role,
            user_name=o.user.full_name if o.user else "Unknown",
            user_phone=o.user.phone if o.user else None,
            telegram_id=o.user.telegram_id if o.user else None,
            added_at=o.added_at,
        ))

    # Also include legacy owner_id if not already in BusinessOwner
    biz_result = await db.execute(select(Business).where(Business.id == business_id))
    biz = biz_result.scalar_one_or_none()
    if biz:
        owner_user_ids = {o.user_id for o in owners}
        if biz.owner_id not in owner_user_ids:
            owner_user_result = await db.execute(select(User).where(User.id == biz.owner_id))
            owner_user = owner_user_result.scalar_one_or_none()
            if owner_user:
                out.insert(0, OwnerOut(
                    id=0,
                    user_id=owner_user.id,
                    role="owner",
                    user_name=owner_user.full_name,
                    user_phone=owner_user.phone,
                    telegram_id=owner_user.telegram_id,
                    added_at=biz.created_at,
                ))

    return out


@router.post("/businesses/{business_id}/owners", response_model=OwnerOut)
async def add_owner(
    business_id: int,
    data: OwnerAddIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    # Email grants access only after verification by the target account.
    if sum(bool(value) for value in (data.email, data.telegram_id, data.username)) != 1:
        raise HTTPException(422, "Укажите один идентификатор: email, Telegram ID или username")
    if data.email:
        from email_utils import normalize_optional_email
        from database.models import EmailIdentity
        try:
            email = normalize_optional_email(data.email)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        result = await db.execute(select(User).join(EmailIdentity, EmailIdentity.user_id == User.id).where(EmailIdentity.email == email))
    elif data.telegram_id:
        result = await db.execute(select(User).where(User.telegram_id == data.telegram_id))
    elif data.username:
        uname = data.username.lstrip("@")
        result = await db.execute(select(User).where(User.username == uname))
    else:
        raise HTTPException(status_code=400, detail="Provide telegram_id or username")

    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала зарегистрируйтесь по email или в боте")

    # Check not already owner
    existing = await db.execute(
        select(BusinessOwner).where(
            BusinessOwner.business_id == business_id,
            BusinessOwner.user_id == target_user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already an owner")

    owner = BusinessOwner(
        business_id=business_id,
        user_id=target_user.id,
        role="co_owner",
        added_by_id=user.id,
    )
    db.add(owner)
    await db.commit()
    await db.refresh(owner)

    return OwnerOut(
        id=owner.id,
        user_id=target_user.id,
        role=owner.role,
        user_name=target_user.full_name,
        user_phone=target_user.phone,
        telegram_id=target_user.telegram_id,
        added_at=owner.added_at,
    )


@router.delete("/businesses/{business_id}/owners/{owner_id}")
async def remove_owner(
    business_id: int,
    owner_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(BusinessOwner).where(
            BusinessOwner.id == owner_id,
            BusinessOwner.business_id == business_id,
        )
    )
    owner = result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    await db.delete(owner)
    await db.commit()
    return {"message": "Owner removed"}


# ---------- Subscription CRUD ----------

@router.get("/businesses/{business_id}/subscriptions", response_model=List[SubscriptionAdminOut])
async def list_subscriptions(
    business_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Subscription)
        .where(Subscription.business_id == business_id)
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
        out.append(SubscriptionAdminOut(
            id=s.id,
            name=s.name,
            description=s.description,
            price=s.price,
            validity_days=s.validity_days,
            rules=s.rules,
            is_active=s.is_active,
            services=services,
        ))
    return out


@router.post("/businesses/{business_id}/subscriptions", response_model=SubscriptionAdminOut)
async def create_subscription(
    business_id: int,
    data: SubscriptionCreateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    sub = Subscription(
        business_id=business_id,
        name=data.name,
        description=data.description,
        price=data.price,
        validity_days=data.validity_days,
        rules=data.rules,
    )
    db.add(sub)
    await db.flush()

    for svc in data.services:
        db.add(SubscriptionService(
            subscription_id=sub.id,
            service_id=svc.service_id,
            quantity=svc.quantity,
        ))

    await db.commit()

    # Reload with services
    result = await db.execute(
        select(Subscription)
        .where(Subscription.id == sub.id)
        .options(selectinload(Subscription.services).selectinload(SubscriptionService.service))
    )
    sub = result.scalar_one()
    services = []
    for ss in sub.services:
        if ss.service:
            services.append(SubscriptionServiceOut(
                id=ss.service.id, name=ss.service.name, quantity=ss.quantity,
            ))
    return SubscriptionAdminOut(
        id=sub.id, name=sub.name, description=sub.description,
        price=sub.price, validity_days=sub.validity_days, rules=sub.rules,
        is_active=sub.is_active, services=services,
    )


@router.put("/businesses/{business_id}/subscriptions/{subscription_id}", response_model=SubscriptionAdminOut)
async def update_subscription(
    business_id: int,
    subscription_id: int,
    data: SubscriptionUpdateIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.business_id == business_id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    updates = data.model_dump(exclude_unset=True)
    services_data = updates.pop("services", None)

    for field, value in updates.items():
        setattr(sub, field, value)

    if services_data is not None:
        # Replace subscription services
        await db.execute(
            sa_delete(SubscriptionService).where(SubscriptionService.subscription_id == sub.id)
        )
        for svc in services_data:
            db.add(SubscriptionService(
                subscription_id=sub.id,
                service_id=svc["service_id"],
                quantity=svc.get("quantity", 1),
            ))

    await db.commit()

    # Reload
    result = await db.execute(
        select(Subscription)
        .where(Subscription.id == sub.id)
        .options(selectinload(Subscription.services).selectinload(SubscriptionService.service))
    )
    sub = result.scalar_one()
    services = []
    for ss in sub.services:
        if ss.service:
            services.append(SubscriptionServiceOut(
                id=ss.service.id, name=ss.service.name, quantity=ss.quantity,
            ))
    return SubscriptionAdminOut(
        id=sub.id, name=sub.name, description=sub.description,
        price=sub.price, validity_days=sub.validity_days, rules=sub.rules,
        is_active=sub.is_active, services=services,
    )


@router.delete("/businesses/{business_id}/subscriptions/{subscription_id}")
async def delete_subscription(
    business_id: int,
    subscription_id: int,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.business_id == business_id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.is_active = False
    await db.commit()
    return {"message": "Subscription deactivated"}


# ---------- QR Code Generation ----------

@router.get("/businesses/{business_id}/qr")
async def generate_qr(
    business_id: int,
    target: str = Query("webapp", pattern="^(webapp|telegram)$"),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Generate QR code for booking (webapp or telegram link)."""
    biz_ids = await _get_owner_business_ids(db, user)
    if business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    try:
        import qrcode
    except ImportError:
        raise HTTPException(status_code=501, detail="qrcode library not installed")

    bot_username = "Sochiservice_bot"
    import os
    webapp_url = os.getenv("WEBAPP_URL", "https://booking.sochi-service.com")

    if target == "telegram":
        url = f"https://t.me/{bot_username}?start=book_{business_id}"
    else:
        url = f"{webapp_url}/book/{business_id}"

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png", headers={
        "Content-Disposition": f"inline; filename=qr_{target}_{business_id}.png",
        "X-QR-URL": url,
    })
