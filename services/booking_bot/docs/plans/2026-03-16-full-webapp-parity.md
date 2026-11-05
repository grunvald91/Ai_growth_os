# Full WebApp Feature Parity — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Port ALL features from old Telegram WebApp (aiohttp) to new React+FastAPI app.

**Architecture:** FastAPI backend adds missing endpoints (calculate-price, enhanced booking, reschedule, subscriptions). React frontend gets full booking flow with dynamic pricing, room selection, subscriptions, and Telegram WebApp integration.

**Tech Stack:** FastAPI + SQLAlchemy async, React 18 + TypeScript + Tailwind CSS v4

---

## Workstream A: Backend — Models + API Endpoints

### Task A1: Update SQLAlchemy Models

**Files:**
- Modify: `database/models.py`

Add missing fields to Employee model:
```python
# Already in DB but not in SQLAlchemy model:
role = Column(String(50), default="master")
capacity = Column(Integer, nullable=True)
area_sqm = Column(Integer, nullable=True)
description = Column(Text, nullable=True)
min_booking_minutes = Column(Integer, nullable=True)
solo_price_per_hour = Column(Integer, nullable=True)
photos = Column(Text, nullable=True)
video = Column(Text, nullable=True)
ai_instruction = Column(Text, nullable=True)
```

Add missing fields to Service model:
```python
# Already in DB:
is_additional = Column(Boolean, default=False)
is_hourly_price = Column(Boolean, default=False)
```

Add VenueTimePricing model:
```python
class VenueTimePricing(Base):
    __tablename__ = "venue_time_pricing"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"))
    time_from = Column(Time, nullable=False)
    time_to = Column(Time, nullable=False)
    price_per_hour = Column(Integer, nullable=False)
    day_type = Column(String(20), default="all")  # all, weekday, weekend
```

Add missing fields to Business model:
```python
prepayment_enabled = Column(Boolean, default=False)
prepayment_percent = Column(Integer, nullable=True)
payment_phone = Column(String(20), nullable=True)
payment_details = Column(Text, nullable=True)
```

### Task A2: Update API Schemas

**Files:**
- Modify: `web/api/schemas.py`

Update EmployeeOut to include pricing:
```python
class EmployeeOut(BaseModel):
    id: int
    name: str
    role: Optional[str] = "master"
    specialization: Optional[str] = None
    description: Optional[str] = None
    capacity: Optional[int] = None
    solo_price_per_hour: Optional[int] = None

class TimePricingOut(BaseModel):
    time_from: str
    time_to: str
    price_per_hour: int
    day_type: str = "all"

class EmployeeWithPricingOut(EmployeeOut):
    time_pricing: List[TimePricingOut] = []
```

Add calculate-price schemas:
```python
class PriceSegment(BaseModel):
    time_from: str
    time_to: str
    minutes: int
    price_per_hour: int
    subtotal: int

class CalculatePriceOut(BaseModel):
    has_time_price: bool
    total: int
    segments: List[PriceSegment] = []
```

Add enhanced booking schemas:
```python
class CreateBookingIn(BaseModel):
    business_id: int
    service_ids: List[int]  # multiple services
    additional_service_ids: List[int] = []
    additional_service_hours: Dict[str, float] = {}
    is_solo_booking: bool = False
    employee_id: Optional[int] = None
    date: date
    time: str
    notes: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
```

Add reschedule schema:
```python
class RescheduleIn(BaseModel):
    date: date
    time: str
```

### Task A3: Add calculate-price Endpoint

**Files:**
- Modify: `web/api/routes/client.py`

```python
@router.get("/businesses/{biz_id}/calculate-price")
async def calculate_price(
    biz_id: int,
    employee_id: int,
    time: str,        # HH:MM
    duration: int,     # minutes
    date: str = None,  # YYYY-MM-DD
    session: AsyncSession = Depends(get_session),
):
    # 1. Get VenueTimePricing rules for employee
    # 2. Filter by day_type (all/weekday/weekend)
    # 3. Calculate segments: iterate minute by minute through duration
    # 4. Return total + segments breakdown
```

Port pricing logic from old server.py `calculate_time_based_price_detailed()`.

### Task A4: Enhanced Booking Creation

**Files:**
- Modify: `web/api/routes/client.py`

Update POST /bookings to support:
- `service_ids` (array) — create separate booking per service, sequential times
- `additional_service_ids` — create bookings at same start time
- `additional_service_hours` — hours per add-on service
- `is_solo_booking` — use solo_price_per_hour if true
- `employee_id` auto-assignment if null
- Prepayment flow if business has it enabled

### Task A5: Reschedule Endpoint

**Files:**
- Modify: `web/api/routes/client.py`

```python
@router.post("/bookings/{booking_id}/reschedule")
async def reschedule_booking(booking_id: int, data: RescheduleIn, ...):
    # 1. Find booking group (consecutive bookings same date/employee)
    # 2. Validate new slot available
    # 3. Update all bookings in group
```

### Task A6: Enhanced Employee/Business Detail

**Files:**
- Modify: `web/api/routes/client.py`

Update GET /businesses/{id} to include:
- `venue_time_pricing` per employee (for venue_rental businesses)
- Employee `description`, `capacity`, `solo_price_per_hour`
- Business `prepayment_enabled`, `prepayment_percent`

---

## Workstream B: Frontend — BookingPage Full Rewrite

### Task B1: Update API Client Types

**Files:**
- Modify: `web/frontend/src/api/client.ts`

Add types: TimePricing, EmployeeWithPricing, PriceSegment, CalculatePriceResponse
Add API calls: calculatePrice(), rescheduleBooking()
Update createBooking() to send service_ids array, additional_service_ids, etc.

### Task B2: Rewrite BookingPage Flow

**Files:**
- Rewrite: `web/frontend/src/pages/client/BookingPage.tsx`

New step flow for venue_rental businesses:
1. Room/Employee selection (with pricing info per room)
2. Service selection (multi-select, duration-based)
3. Date selection (calendar)
4. Time selection (slots, show price per slot based on room pricing)
5. Booking format (solo/group) — only if room has solo_price_per_hour
6. Additional services (with hourly +/- controls)
7. Confirmation (price breakdown segments, contact info, notes)

For non-venue businesses: simpler flow (service → employee → date → time → confirm)

### Task B3: Dynamic Price Display

Show price that updates in real-time:
- Call /calculate-price when room + time + duration are known
- Display segment breakdown: "09:00-12:00 (3ч) × 600₽ = 1800₽"
- Add-on prices multiplied by hours
- Solo price shown as alternative if available

---

## Workstream C: Frontend — MyBookings + Subscriptions

### Task C1: Enhance MyBookingsPage

**Files:**
- Modify: `web/frontend/src/pages/client/MyBookingsPage.tsx`

Add:
- Reschedule button → date/time picker → confirm
- Cancel with confirmation dialog
- Show price and services list per booking
- Status badges (pending, confirmed, pending_prepayment, etc.)

### Task C2: Subscriptions Pages

**Files:**
- Create: `web/frontend/src/pages/client/SubscriptionsPage.tsx`

Features:
- List available subscriptions per business
- Purchase flow with payment details display
- My subscriptions with remaining sessions
- Upload receipt (photo)

### Task C3: Telegram WebApp Integration

**Files:**
- Create: `web/frontend/src/utils/telegram.ts`

Integrate:
- `tg.ready()`, `tg.expand()`
- `tg.HapticFeedback` on selections
- Theme variables → CSS custom properties
- `tg.initData` for auth (alternative to JWT)
- `tg.showConfirm()` for cancel/reschedule dialogs

---

## Workstream D: Integration & Deploy

### Task D1: Nginx Config Update
- Point Telegram WEBAPP_URL to React app
- Keep /api → FastAPI
- Add /static → old webapp (temporary compatibility)

### Task D2: Build + Deploy
- npm run build
- Upload dist to server
- Restart booking-web-api service
- nginx reload
