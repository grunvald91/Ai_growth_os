# Повторяющаяся еженедельная запись — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Клиент (`BookingPage.tsx`) и владелец (`ManualBookingModal` в `SchedulePage.tsx`) могут при создании записи включить повтор той же услуги/мастера/времени еженедельно на 2–8 недель вперёд; если хоть одна неделя конфликтует — не создаётся ничего.

**Architecture:** Чистая функция `compute_recurring_dates(start, weeks)` в новом `web/api/booking_utils.py` вычисляет даты; оба роута (`POST /bookings`, `POST /admin/bookings`) сначала прогоняют проверку конфликтов по ВСЕМ датам (без записи в БД), и только если конфликтов нет — создают все записи одной транзакцией. Никакой новой сущности "серия" — N полностью независимых строк `Booking`.

**Tech Stack:** FastAPI + SQLAlchemy (async, Postgres на проде) для бэкенда; React + TypeScript для фронтенда. Тестирование — `unittest` (без БД, как единственный существующий тест-конвеншн в репо, `test_schedule_business_guard.py`) + прогон сборки/линтера + ручная проверка в браузере (mock-харнесс) + живая проверка на проде через SSH/curl на тестовых датах в далёком будущем.

## Global Constraints

- `repeat_weeks` — целое от 2 до 8 включительно; отсутствие поля/`None` = обычная одиночная запись (как сейчас), поведение не меняется.
- Атомарность: конфликт хотя бы одной недели → ничего не создаётся, `409` с деталями.
- Никакой групповой сущности — N независимых `Booking`-записей, отмена/перенос каждой по отдельности как обычно.
- Повтор недоступен для оплаты через абонемент (не актуально для `POST /bookings` — этот эндпоинт никогда не обрабатывает оплату абонементом, это отдельный флоу через `SubscriptionsPage.tsx`/`buySubscription` — гейтинг не нужен).
- Не вводить новых зависимостей/тестовых фреймворков, которых нет в проекте (никакого pytest+DB-фикстур — в репо только `unittest`, без `conftest.py`).

---

### Task 1: Чистая функция вычисления дат повтора

**Files:**
- Create: `web/api/booking_utils.py`
- Test: `test_booking_utils.py` (корень репозитория, рядом с существующими `test_*.py`)

**Interfaces:**
- Produces: `compute_recurring_dates(start: date, weeks: int) -> list[date]` — используется в Task 3 и Task 4.

- [ ] **Step 1: Написать падающий тест**

Создать `test_booking_utils.py`:

```python
import unittest
from datetime import date, timedelta

from web.api.booking_utils import compute_recurring_dates


class ComputeRecurringDatesTest(unittest.TestCase):
    def test_single_week_returns_only_start_date(self):
        self.assertEqual(compute_recurring_dates(date(2026, 8, 12), 1), [date(2026, 8, 12)])

    def test_multiple_weeks_are_spaced_seven_days_apart(self):
        result = compute_recurring_dates(date(2026, 8, 12), 4)
        self.assertEqual(result, [
            date(2026, 8, 12),
            date(2026, 8, 19),
            date(2026, 8, 26),
            date(2026, 9, 2),
        ])

    def test_eight_weeks_is_the_max_allowed(self):
        result = compute_recurring_dates(date(2026, 1, 1), 8)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[-1], date(2026, 1, 1) + timedelta(weeks=7))

    def test_zero_weeks_raises(self):
        with self.assertRaises(ValueError):
            compute_recurring_dates(date(2026, 1, 1), 0)

    def test_nine_weeks_raises(self):
        with self.assertRaises(ValueError):
            compute_recurring_dates(date(2026, 1, 1), 9)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 test_booking_utils.py -v`
Expected: `ModuleNotFoundError: No module named 'web.api.booking_utils'`

- [ ] **Step 3: Реализовать функцию**

Создать `web/api/booking_utils.py`:

```python
"""Чистые хелперы для повторяющихся (еженедельных) записей — без обращения к БД."""
from datetime import date, timedelta
from typing import List


def compute_recurring_dates(start: date, weeks: int) -> List[date]:
    """Вернуть `weeks` дат начиная с `start`, с шагом в неделю.

    `weeks=1` — вернёт только `[start]` (без повтора, это дефолтный случай,
    когда `repeat_weeks` не задан клиентом). Диапазон 2-8 — то, что видит
    пользователь в UI; 1 используется внутри роутов как "без повтора".
    """
    if weeks < 1 or weeks > 8:
        raise ValueError(f"weeks must be between 1 and 8, got {weeks}")
    return [start + timedelta(weeks=i) for i in range(weeks)]
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 test_booking_utils.py -v`
Expected: `OK` (5 тестов, все `ok`)

- [ ] **Step 5: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/api/booking_utils.py test_booking_utils.py
git commit -m "feat(web/api): compute_recurring_dates — чистая функция для повтора записи"
```

---

### Task 2: Поле `repeat_weeks` в схемах запроса

**Files:**
- Modify: `web/api/schemas.py:8` (импорт), `:159-171` (`CreateBookingIn`), `:216-225` (`ManualBookingIn`)

**Interfaces:**
- Consumes: ничего нового.
- Produces: `CreateBookingIn.repeat_weeks: Optional[int]`, `ManualBookingIn.repeat_weeks: Optional[int]` — читаются в Task 3 и Task 4 как `body.repeat_weeks` / `data.repeat_weeks`.

- [ ] **Step 1: Добавить импорт `Field`**

В `web/api/schemas.py` заменить:

```python
from pydantic import BaseModel
```

на:

```python
from pydantic import BaseModel, Field
```

- [ ] **Step 2: Добавить поле в `CreateBookingIn`**

Заменить:

```python
class CreateBookingIn(BaseModel):
    business_id: int
    service_id: Optional[int] = None
    service_ids: List[int] = []
    additional_service_ids: List[int] = []
    additional_service_hours: Optional[dict] = None
    is_solo_booking: bool = False
    date: date
    time: str  # "HH:MM"
    employee_id: Optional[int] = None
    notes: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
```

на:

```python
class CreateBookingIn(BaseModel):
    business_id: int
    service_id: Optional[int] = None
    service_ids: List[int] = []
    additional_service_ids: List[int] = []
    additional_service_hours: Optional[dict] = None
    is_solo_booking: bool = False
    date: date
    time: str  # "HH:MM"
    employee_id: Optional[int] = None
    notes: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    # Повтор той же брони еженедельно на N недель (включая первую); None = без повтора.
    repeat_weeks: Optional[int] = Field(default=None, ge=2, le=8)
```

- [ ] **Step 3: Добавить поле в `ManualBookingIn`**

Заменить:

```python
class ManualBookingIn(BaseModel):
    business_id: int
    service_id: int
    employee_id: Optional[int] = None
    date: date
    time: str
    client_name: str
    client_phone: Optional[str] = None
    notes: Optional[str] = None
```

на:

```python
class ManualBookingIn(BaseModel):
    business_id: int
    service_id: int
    employee_id: Optional[int] = None
    date: date
    time: str
    client_name: str
    client_phone: Optional[str] = None
    notes: Optional[str] = None
    # Повтор той же брони еженедельно на N недель (включая первую); None = без повтора.
    repeat_weeks: Optional[int] = Field(default=None, ge=2, le=8)
```

- [ ] **Step 4: Проверить синтаксис**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 -c "import ast; ast.parse(open('web/api/schemas.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Проверить валидацию диапазона вручную**

Run:
```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 -c "
from web.api.schemas import CreateBookingIn
from datetime import date
CreateBookingIn(business_id=1, service_ids=[1], date=date(2026,8,1), time='10:00', repeat_weeks=1)
"
```
Expected: `pydantic_core._pydantic_core.ValidationError` (1 < 2, вне диапазона) — подтверждает что валидация работает.

- [ ] **Step 6: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/api/schemas.py
git commit -m "feat(web/api): repeat_weeks (2-8) в CreateBookingIn и ManualBookingIn"
```

---

### Task 3: Повтор в клиентской записи (`POST /bookings`)

**Files:**
- Modify: `web/api/routes/client.py:1` (импорт), `~538-615` (тело `create_booking`)

**Interfaces:**
- Consumes: `compute_recurring_dates(start, weeks)` из Task 1; `body.repeat_weeks` из Task 2.
- Produces: не меняется — `POST /bookings` по-прежнему возвращает `List[BookingOut]`, при повторе туда попадают записи по всем неделям.

- [ ] **Step 1: Добавить импорт хелпера**

В `web/api/routes/client.py` после строки `from ..deps import get_current_user, get_current_user_optional` добавить:

```python
from ..booking_utils import compute_recurring_dates
```

- [ ] **Step 2: Заменить тело создания записей на двухфазную схему**

Найти в `web/api/routes/client.py` блок, начинающийся с `current_time = datetime.combine(body.date, booking_time)` и заканчивающийся `await db.commit()` (строки ~538-615). Заменить целиком:

Было:

```python
    current_time = datetime.combine(body.date, booking_time)
    created_bookings = []

    # Pre-compute additional services info for notes
    addon_notes_parts = []
    addon_total_price = 0
    if body.additional_service_ids:
        for addon_id in body.additional_service_ids:
            addon_res = await db.execute(
                select(Service).where(Service.id == addon_id, Service.business_id == body.business_id)
            )
            addon_svc = addon_res.scalar_one_or_none()
            if addon_svc:
                hours = (body.additional_service_hours or {}).get(str(addon_id), 1)
                price = (addon_svc.price or 0) * hours
                addon_total_price += price
                addon_notes_parts.append(f"+ {addon_svc.name} ({price}₽)")

    # Build notes with addon info
    booking_notes = body.notes or ""
    if addon_notes_parts:
        if booking_notes:
            booking_notes += "\n"
        booking_notes += "Доп. услуги: " + ", ".join(addon_notes_parts)

    for svc_id in svc_ids:
        # Validate service
        svc_result = await db.execute(
            select(Service).where(
                Service.id == svc_id,
                Service.business_id == body.business_id,
                Service.is_active == True,
            )
        )
        service = svc_result.scalar_one_or_none()
        if not service:
            raise HTTPException(status_code=404, detail=f"Service {svc_id} not found")

        bt = current_time.time()

        # Check for conflicts with row locking to prevent race conditions
        conflict_filter = [
            Booking.business_id == body.business_id,
            Booking.booking_date == body.date,
            Booking.booking_time == bt,
            Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
        ]
        if body.employee_id:
            conflict_filter.append(Booking.employee_id == body.employee_id)

        conflict_result = await db.execute(
            select(Booking).where(and_(*conflict_filter)).with_for_update(skip_locked=True)
        )
        if conflict_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Time slot {bt.strftime('%H:%M')} is already taken",
            )

        # Use pending_prepayment status when prepayment is enabled (matches bot flow)
        initial_status = "pending_prepayment" if getattr(business, 'prepayment_enabled', False) else "pending"
        booking = Booking(
            business_id=body.business_id,
            client_id=booking_user.id,
            service_id=svc_id,
            employee_id=body.employee_id,
            booking_date=body.date,
            booking_time=bt,
            status=initial_status,
            notes=booking_notes or None,
        )
        db.add(booking)
        created_bookings.append(booking)

        # Advance time by service duration for sequential bookings
        current_time += timedelta(minutes=service.duration_minutes or 30)

    await db.commit()
```

Стало:

```python
    # Повтор: тот же день недели/время, 2-8 повторов включая первый (без repeat_weeks — 1 дата, как раньше).
    occurrence_dates = compute_recurring_dates(body.date, body.repeat_weeks or 1)
    created_bookings = []

    # Pre-compute additional services info for notes
    addon_notes_parts = []
    addon_total_price = 0
    if body.additional_service_ids:
        for addon_id in body.additional_service_ids:
            addon_res = await db.execute(
                select(Service).where(Service.id == addon_id, Service.business_id == body.business_id)
            )
            addon_svc = addon_res.scalar_one_or_none()
            if addon_svc:
                hours = (body.additional_service_hours or {}).get(str(addon_id), 1)
                price = (addon_svc.price or 0) * hours
                addon_total_price += price
                addon_notes_parts.append(f"+ {addon_svc.name} ({price}₽)")

    # Build notes with addon info
    booking_notes = body.notes or ""
    if addon_notes_parts:
        if booking_notes:
            booking_notes += "\n"
        booking_notes += "Доп. услуги: " + ", ".join(addon_notes_parts)

    # Phase 1: проверка конфликтов по ВСЕМ датам повтора, без записи в БД.
    # Атомарность: если конфликтует хоть одна дата — не создаём ничего.
    if len(occurrence_dates) > 1:
        conflicts = []
        for occ_date in occurrence_dates:
            check_time = datetime.combine(occ_date, booking_time)
            for svc_id in svc_ids:
                svc_result = await db.execute(
                    select(Service).where(
                        Service.id == svc_id,
                        Service.business_id == body.business_id,
                        Service.is_active == True,
                    )
                )
                service = svc_result.scalar_one_or_none()
                if not service:
                    raise HTTPException(status_code=404, detail=f"Service {svc_id} not found")
                bt = check_time.time()
                conflict_filter = [
                    Booking.business_id == body.business_id,
                    Booking.booking_date == occ_date,
                    Booking.booking_time == bt,
                    Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
                ]
                if body.employee_id:
                    conflict_filter.append(Booking.employee_id == body.employee_id)
                conflict_result = await db.execute(select(Booking).where(and_(*conflict_filter)))
                if conflict_result.scalar_one_or_none():
                    conflicts.append(f"{occ_date.strftime('%d.%m')} {bt.strftime('%H:%M')}")
                check_time += timedelta(minutes=service.duration_minutes or 30)
        if conflicts:
            raise HTTPException(status_code=409, detail="Занято: " + ", ".join(conflicts))

    # Phase 2: создание записей по каждой дате повтора.
    for occ_date in occurrence_dates:
        current_time = datetime.combine(occ_date, booking_time)
        for svc_id in svc_ids:
            # Validate service
            svc_result = await db.execute(
                select(Service).where(
                    Service.id == svc_id,
                    Service.business_id == body.business_id,
                    Service.is_active == True,
                )
            )
            service = svc_result.scalar_one_or_none()
            if not service:
                raise HTTPException(status_code=404, detail=f"Service {svc_id} not found")

            bt = current_time.time()

            # Check for conflicts with row locking to prevent race conditions
            conflict_filter = [
                Booking.business_id == body.business_id,
                Booking.booking_date == occ_date,
                Booking.booking_time == bt,
                Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
            ]
            if body.employee_id:
                conflict_filter.append(Booking.employee_id == body.employee_id)

            conflict_result = await db.execute(
                select(Booking).where(and_(*conflict_filter)).with_for_update(skip_locked=True)
            )
            if conflict_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=f"Time slot {bt.strftime('%H:%M')} is already taken",
                )

            # Use pending_prepayment status when prepayment is enabled (matches bot flow)
            initial_status = "pending_prepayment" if getattr(business, 'prepayment_enabled', False) else "pending"
            booking = Booking(
                business_id=body.business_id,
                client_id=booking_user.id,
                service_id=svc_id,
                employee_id=body.employee_id,
                booking_date=occ_date,
                booking_time=bt,
                status=initial_status,
                notes=booking_notes or None,
            )
            db.add(booking)
            created_bookings.append(booking)

            # Advance time by service duration for sequential bookings
            current_time += timedelta(minutes=service.duration_minutes or 30)

    await db.commit()
```

Обратить внимание: остальная часть функции (перезагрузка `created_bookings` в `result_bookings`, отправка уведомлений) идёт СРАЗУ ПОСЛЕ `await db.commit()` и не меняется — она уже работает по списку `created_bookings`/`result_bookings`, которые теперь просто длиннее при повторе. Уведомления (`_send_booking_notifications`) как и раньше ссылаются на `body.date`/`result_bookings[0]` — это первая (ближайшая) дата серии, что осознанно оставлено как есть в этой версии (не рассылаем N уведомлений).

- [ ] **Step 3: Проверить синтаксис**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 -c "import ast; ast.parse(open('web/api/routes/client.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/api/routes/client.py
git commit -m "feat(web/api): повтор еженедельной записи в POST /bookings (атомарно, 2-8 недель)"
```

---

### Task 4: Повтор в ручной записи владельца (`POST /admin/bookings`)

**Files:**
- Modify: `web/api/routes/admin.py:~484-537` (`create_manual_booking`)

**Interfaces:**
- Consumes: `compute_recurring_dates` из Task 1; `data.repeat_weeks` из Task 2.
- Produces: `POST /admin/bookings` теперь возвращает `List[AdminBookingOut]` вместо одиночного `AdminBookingOut` — фронтенд обновляется в Task 6.

- [ ] **Step 1: Добавить импорт хелпера**

В `web/api/routes/admin.py`, рядом с остальными относительными импортами (после `from ..deps import get_current_admin`), добавить:

```python
from ..booking_utils import compute_recurring_dates
```

- [ ] **Step 2: Переписать `create_manual_booking`**

Заменить весь блок (строки ~484-537):

```python
# ---------- POST /admin/bookings (manual booking) ----------

@router.post("/bookings", response_model=AdminBookingOut)
async def create_manual_booking(
    data: ManualBookingIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a booking on behalf of a client (manual booking by owner)."""
    biz_ids = await _get_owner_business_ids(db, user)
    if data.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    # Get or create a guest user for the client
    import time as time_module
    guest_telegram_id = -int(time_module.time() * 1000)
    guest_user = User(
        telegram_id=guest_telegram_id,
        full_name=data.client_name,
        phone=data.client_phone,
    )
    db.add(guest_user)
    await db.flush()

    # Parse time
    from datetime import time as dt_time
    h, m = map(int, data.time.split(":"))
    booking_time = dt_time(h, m)

    booking = Booking(
        business_id=data.business_id,
        client_id=guest_user.id,
        service_id=data.service_id,
        employee_id=data.employee_id,
        booking_date=data.date,
        booking_time=booking_time,
        status="confirmed",
        notes=data.notes,
    )
    db.add(booking)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(Booking)
        .where(Booking.id == booking.id)
        .options(
            selectinload(Booking.client),
            selectinload(Booking.service),
            selectinload(Booking.employee),
        )
    )
    booking = result.scalar_one()
    return _admin_booking_out(booking)
```

на:

```python
# ---------- POST /admin/bookings (manual booking) ----------

@router.post("/bookings", response_model=List[AdminBookingOut])
async def create_manual_booking(
    data: ManualBookingIn,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a booking on behalf of a client (manual booking by owner).

    Поддерживает data.repeat_weeks (2-8) — еженедельный повтор. Атомарно:
    если конфликтует хоть одна из дат повтора, не создаётся ничего. Без
    repeat_weeks поведение не меняется (одна запись, без проверки конфликта —
    админ уже свёрился с расписанием визуально при клике по календарю)."""
    biz_ids = await _get_owner_business_ids(db, user)
    if data.business_id not in biz_ids:
        raise HTTPException(status_code=403, detail="Not your business")

    # Parse time
    from datetime import time as dt_time
    h, m = map(int, data.time.split(":"))
    booking_time = dt_time(h, m)

    occurrence_dates = compute_recurring_dates(data.date, data.repeat_weeks or 1)

    # Проверка конфликтов по всем датам ТОЛЬКО при повторе — за 8 недель вперёд
    # визуально свериться с расписанием нереально, в отличие от одиночного клика.
    if len(occurrence_dates) > 1:
        conflicts = []
        for occ_date in occurrence_dates:
            conflict_filter = [
                Booking.business_id == data.business_id,
                Booking.booking_date == occ_date,
                Booking.booking_time == booking_time,
                Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
            ]
            if data.employee_id:
                conflict_filter.append(Booking.employee_id == data.employee_id)
            conflict_result = await db.execute(select(Booking).where(and_(*conflict_filter)))
            if conflict_result.scalar_one_or_none():
                conflicts.append(f"{occ_date.strftime('%d.%m')} {data.time}")
        if conflicts:
            raise HTTPException(status_code=409, detail="Занято: " + ", ".join(conflicts))

    # Get or create a guest user for the client (один клиент на всю серию)
    import time as time_module
    guest_telegram_id = -int(time_module.time() * 1000)
    guest_user = User(
        telegram_id=guest_telegram_id,
        full_name=data.client_name,
        phone=data.client_phone,
    )
    db.add(guest_user)
    await db.flush()

    created_ids = []
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
        )
        db.add(booking)
        await db.flush()
        created_ids.append(booking.id)

    await db.commit()

    # Reload with relationships
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
    bookings = result.scalars().all()
    return [_admin_booking_out(b) for b in bookings]
```

- [ ] **Step 3: Проверить синтаксис**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo && python3 -c "import ast; ast.parse(open('web/api/routes/admin.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/api/routes/admin.py
git commit -m "feat(web/api): повтор еженедельной ручной записи в POST /admin/bookings

Response model меняется с AdminBookingOut на List[AdminBookingOut] —
единственное изменение контракта существующего эндпоинта, фронт
обновляется отдельным коммитом."
```

---

### Task 5: Обновить `api/client.ts` (типы и вызовы)

**Files:**
- Modify: `web/frontend/src/api/client.ts:153-165` (`CreateBookingData`), `:183-192` (`ManualBookingData`), `:465-469` (`createManualBooking`)

**Interfaces:**
- Produces: `CreateBookingData.repeat_weeks?: number`, `ManualBookingData.repeat_weeks?: number`, `api.createManualBooking(data): Promise<AdminBooking[]>` (было `Promise<AdminBooking>`) — используется в Task 7.

- [ ] **Step 1: Добавить поле в `CreateBookingData`**

Заменить:

```typescript
export interface CreateBookingData {
  business_id: number;
  service_ids: number[];
  additional_service_ids?: number[];
  additional_service_hours?: Record<string, number>;
  is_solo_booking?: boolean;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone: string;
  notes?: string;
}
```

на:

```typescript
export interface CreateBookingData {
  business_id: number;
  service_ids: number[];
  additional_service_ids?: number[];
  additional_service_hours?: Record<string, number>;
  is_solo_booking?: boolean;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone: string;
  notes?: string;
  repeat_weeks?: number;
}
```

- [ ] **Step 2: Добавить поле в `ManualBookingData`**

Заменить:

```typescript
export interface ManualBookingData {
  business_id: number;
  service_id: number;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone?: string;
  notes?: string;
}
```

на:

```typescript
export interface ManualBookingData {
  business_id: number;
  service_id: number;
  employee_id?: number;
  date: string;
  time: string;
  client_name: string;
  client_phone?: string;
  notes?: string;
  repeat_weeks?: number;
}
```

- [ ] **Step 3: Обновить возвращаемый тип `createManualBooking`**

Заменить:

```typescript
  createManualBooking: (data: ManualBookingData) =>
    request<AdminBooking>('/admin/bookings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
```

на:

```typescript
  createManualBooking: (data: ManualBookingData) =>
    request<AdminBooking[]>('/admin/bookings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
```

- [ ] **Step 4: Типовая проверка**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo/web/frontend && npx tsc -b 2>&1 | tail -40`
Expected: ошибки только в `SchedulePage.tsx` (вызывающий код `createManualBooking` ещё не обновлён — это Task 8; если ошибок 0 — тоже нормально, значит вызывающий код уже совместим благодаря `any`-подобной типизации).

- [ ] **Step 5: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/frontend/src/api/client.ts
git commit -m "feat(web/frontend): repeat_weeks в типах CreateBookingData/ManualBookingData"
```

---

### Task 6: UI повтора в клиентской записи (`BookingPage.tsx`)

**Files:**
- Modify: `web/frontend/src/pages/client/BookingPage.tsx:38-59` (импорт иконок), `~284-294` (state), `~587-599` (payload), `~599-625` (обработка результата), `~628-639` (catch), `~768-773` (success-экран)

**Interfaces:**
- Consumes: `CreateBookingData.repeat_weeks` из Task 5.

- [ ] **Step 1: Добавить иконку `Repeat` в импорт**

Заменить строку `  Paperclip,` (последняя перед `} from 'lucide-react'` в первом блоке импорта, строка 58) на:

```typescript
  Paperclip,
  Repeat,
```

- [ ] **Step 2: Добавить state повтора**

Найти `const [comment, setComment] = useState('')` (строка 290) и сразу после добавить:

```typescript
  const [repeatEnabled, setRepeatEnabled] = useState(false)
  const [repeatWeeks, setRepeatWeeks] = useState(4)
  const [createdDates, setCreatedDates] = useState<string[]>([])
```

- [ ] **Step 3: Добавить UI переключателя после поля "Комментарий"**

Найти блок (строки ~1850-1862):

```tsx
            <div className="ds-field" style={{ marginBottom: 0 }}>
              <label className="ds-label">
                Комментарий{' '}
                <span style={{ fontWeight: 400 }}>(необязательно)</span>
              </label>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Пожелания к записи..."
                rows={3}
                className="ds-input"
              />
            </div>
          </div>
```

Заменить на (добавлен новый блок перед закрывающим `</div>` родителя):

```tsx
            <div className="ds-field" style={{ marginBottom: 0 }}>
              <label className="ds-label">
                Комментарий{' '}
                <span style={{ fontWeight: 400 }}>(необязательно)</span>
              </label>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Пожелания к записи..."
                rows={3}
                className="ds-input"
              />
            </div>

            <div className="ds-field" style={{ marginBottom: 0, marginTop: 13 }}>
              <button
                type="button"
                onClick={() => setRepeatEnabled((v) => !v)}
                className={`bk-option ${repeatEnabled ? 'is-selected' : ''}`}
                style={{ width: '100%' }}
              >
                <div style={{ flex: 1, textAlign: 'left', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Repeat size={16} />
                  <span className="bk-option-title">Повторять каждую неделю</span>
                </div>
                <div className={`bk-check ${repeatEnabled ? 'is-on' : ''}`}>
                  {repeatEnabled && <Check size={14} strokeWidth={3} />}
                </div>
              </button>
              {repeatEnabled && (
                <div className="bk-addon-hours" style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    onClick={() => setRepeatWeeks((w) => Math.max(2, w - 1))}
                    className="ds-icon-btn"
                  >
                    <Minus size={16} />
                  </button>
                  <span className="val">{repeatWeeks} нед</span>
                  <button
                    type="button"
                    onClick={() => setRepeatWeeks((w) => Math.min(8, w + 1))}
                    className="ds-icon-btn"
                  >
                    <Plus size={16} />
                  </button>
                </div>
              )}
              {repeatEnabled && (
                <p className="ds-hint">
                  Ещё {repeatWeeks - 1} {repeatWeeks - 1 === 1 ? 'такая же запись' : 'таких же записи'} каждую неделю после первой.
                </p>
              )}
            </div>
          </div>
```

- [ ] **Step 4: Прокинуть `repeat_weeks` в запрос**

Найти вызов `api.createBooking` (строки ~587-599):

```tsx
      const result = await api.createBooking({
        business_id: Number(businessId),
        service_ids: serviceIds,
        additional_service_ids: additionalServiceIds.length > 0 ? additionalServiceIds : undefined,
        additional_service_hours: Object.keys(additionalServiceHoursMap).length > 0 ? additionalServiceHoursMap : undefined,
        is_solo_booking: isVenue && isSoloPricing ? true : undefined,
        employee_id: isVenue ? selectedRoom?.id : selectedEmployee?.id,
        date: selectedDate,
        time: selectedTime,
        client_name: clientName,
        client_phone: clientPhone,
        notes: comment || undefined,
      })
```

Заменить на:

```tsx
      const result = await api.createBooking({
        business_id: Number(businessId),
        service_ids: serviceIds,
        additional_service_ids: additionalServiceIds.length > 0 ? additionalServiceIds : undefined,
        additional_service_hours: Object.keys(additionalServiceHoursMap).length > 0 ? additionalServiceHoursMap : undefined,
        is_solo_booking: isVenue && isSoloPricing ? true : undefined,
        employee_id: isVenue ? selectedRoom?.id : selectedEmployee?.id,
        date: selectedDate,
        time: selectedTime,
        client_name: clientName,
        client_phone: clientPhone,
        notes: comment || undefined,
        repeat_weeks: repeatEnabled ? repeatWeeks : undefined,
      })
```

- [ ] **Step 5: Собрать список дат созданной серии**

Найти блок (строки ~603-605):

```tsx
      if (firstResult) {
        setCreatedBookingId(firstResult.id)
        setCreatedNotifyToken(firstResult.notify_token ?? null)
```

Заменить на:

```tsx
      if (firstResult) {
        setCreatedBookingId(firstResult.id)
        setCreatedNotifyToken(firstResult.notify_token ?? null)
        if (Array.isArray(result) && repeatEnabled) {
          const uniqueDates = Array.from(new Set(result.map((b: any) => b.date).filter(Boolean))) as string[]
          setCreatedDates(uniqueDates)
        }
```

- [ ] **Step 6: Улучшить сообщение об ошибке конфликта серии**

Найти catch-блок (строки ~628-635):

```tsx
    } catch (err: any) {
      console.error('Booking creation error:', err)
      const msg = err?.message || ''
      if (msg.includes('409') || msg.includes('already taken')) {
        setBookingError('Это время уже занято. Выберите другое время.')
      } else {
        setBookingError('Не удалось создать запись. Попробуйте ещё раз.')
      }
    } finally {
```

Заменить на:

```tsx
    } catch (err: any) {
      console.error('Booking creation error:', err)
      const msg = err?.message || ''
      if (repeatEnabled) {
        try {
          const parsed = JSON.parse(msg)
          setBookingError(
            parsed?.detail
              ? `Не удалось создать серию — ${parsed.detail}`
              : 'Не удалось создать серию записей. Попробуйте другое время.'
          )
        } catch {
          setBookingError('Не удалось создать серию записей. Попробуйте другое время.')
        }
      } else if (msg.includes('409') || msg.includes('already taken')) {
        setBookingError('Это время уже занято. Выберите другое время.')
      } else {
        setBookingError('Не удалось создать запись. Попробуйте ещё раз.')
      }
    } finally {
```

- [ ] **Step 7: Показать список дат на экране успеха**

Найти блок (строки ~768-773):

```tsx
              <div className="bk-success-row">
                <span className="lbl">Дата и время</span>
                <span className="val">
                  {selectedDate && formatDateRu(selectedDate)}, {selectedTime}
                </span>
              </div>
```

Заменить на:

```tsx
              <div className="bk-success-row">
                <span className="lbl">Дата и время</span>
                <span className="val">
                  {selectedDate && formatDateRu(selectedDate)}, {selectedTime}
                </span>
              </div>
              {createdDates.length > 1 && (
                <div className="bk-success-row">
                  <span className="lbl">Повторится ещё</span>
                  <span className="val">
                    {createdDates.slice(1).map((d) => formatDateRu(d)).join(', ')}
                  </span>
                </div>
              )}
```

- [ ] **Step 8: Собрать и проверить типы**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo/web/frontend && npx vite build 2>&1 | tail -20 && npx tsc -b 2>&1 | tail -40`
Expected: `vite build` завершается `✓ built in ...`, `tsc -b` без вывода (0 ошибок).

- [ ] **Step 9: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/frontend/src/pages/client/BookingPage.tsx
git commit -m "feat(web/client): UI повтора записи на 2-8 недель в BookingPage"
```

---

### Task 7: UI повтора в ручной записи (`ManualBookingModal`)

**Files:**
- Modify: `web/frontend/src/pages/admin/SchedulePage.tsx:4-24` (импорт `Minus`), `~262-388` (`ManualBookingModal`)

**Interfaces:**
- Consumes: `ManualBookingData.repeat_weeks`, `api.createManualBooking(): Promise<AdminBooking[]>` из Task 5.

- [ ] **Step 1: Добавить иконку `Minus` в импорт**

Заменить строку `  Plus,` (строка 7 блока импорта lucide-react) на:

```typescript
  Plus,
  Minus,
```

- [ ] **Step 2: Заменить старую логику повтора (state + REPEATS + submit)**

Существующий код в `ManualBookingModal` уже содержит черновую (небезопасную) реализацию повтора — последовательные вызовы без атомарной проверки конфликтов. Заменить его целиком на новый атомарный вариант.

Найти:

```tsx
  const [serviceId, setServiceId] = useState<number | null>(null)
  const [employeeId, setEmployeeId] = useState<number | null>(null)
  const [time, setTime] = useState(selectedTime || '10:00')
  const [clientName, setClientName] = useState('')
  const [clientPhone, setClientPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [repeatExtra, setRepeatExtra] = useState(0) // сколько ДОПОЛНИТЕЛЬНЫХ недель
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const REPEATS = [
    { v: 0, l: 'Нет' },
    { v: 3, l: '4 нед' },
    { v: 7, l: '8 нед' },
    { v: 11, l: '12 нед' },
  ]
  const dateLabel = selectedDate.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })

  const handleSubmit = async () => {
    if (!serviceId || !clientName) return
    setSubmitting(true)
    setError('')
    try {
      for (let i = 0; i <= repeatExtra; i++) {
        const d = new Date(selectedDate)
        d.setDate(d.getDate() + 7 * i)
        await api.createManualBooking({
          business_id: businessId,
          service_id: serviceId,
          employee_id: employeeId ?? undefined,
          date: formatDateISO(d),
          time,
          client_name: clientName,
          client_phone: clientPhone || undefined,
          notes: notes || undefined,
        })
      }
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    } finally {
      setSubmitting(false)
    }
  }
```

Заменить на:

```tsx
  const [serviceId, setServiceId] = useState<number | null>(null)
  const [employeeId, setEmployeeId] = useState<number | null>(null)
  const [time, setTime] = useState(selectedTime || '10:00')
  const [clientName, setClientName] = useState('')
  const [clientPhone, setClientPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [repeatEnabled, setRepeatEnabled] = useState(false)
  const [repeatWeeks, setRepeatWeeks] = useState(4)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const dateLabel = selectedDate.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })

  const handleSubmit = async () => {
    if (!serviceId || !clientName) return
    setSubmitting(true)
    setError('')
    try {
      const created = await api.createManualBooking({
        business_id: businessId,
        service_id: serviceId,
        employee_id: employeeId ?? undefined,
        date: formatDateISO(selectedDate),
        time,
        client_name: clientName,
        client_phone: clientPhone || undefined,
        notes: notes || undefined,
        repeat_weeks: repeatEnabled ? repeatWeeks : undefined,
      })
      if (Array.isArray(created) && created.length > 1) {
        onCreated(`Создано ${created.length} записей`)
      } else {
        onCreated()
      }
    } catch (e) {
      let msg = e instanceof Error ? e.message : 'Ошибка'
      try {
        const parsed = JSON.parse(msg)
        if (parsed?.detail) msg = parsed.detail
      } catch {
        // не JSON — оставляем исходный текст
      }
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }
```

- [ ] **Step 3: Обновить сигнатуру `onCreated` в пропсах модалки**

Найти:

```tsx
function ManualBookingModal({
  businessId, services, employees, selectedDate, selectedTime, onClose, onCreated,
}: {
  businessId: number
  services: Service[]
  employees: Employee[]
  selectedDate: Date
  selectedTime?: string
  onClose: () => void
  onCreated: () => void
}) {
```

Заменить на:

```tsx
function ManualBookingModal({
  businessId, services, employees, selectedDate, selectedTime, onClose, onCreated,
}: {
  businessId: number
  services: Service[]
  employees: Employee[]
  selectedDate: Date
  selectedTime?: string
  onClose: () => void
  onCreated: (toastMessage?: string) => void
}) {
```

- [ ] **Step 4: Заменить UI повтора (сегменты → степпер)**

Найти:

```tsx
        <div className="sched-field">
          <label>Повторять еженедельно</label>
          <div className="sched-seg-full">
            {REPEATS.map((r) => (
              <button key={r.v} className={repeatExtra === r.v ? 'on' : ''} onClick={() => setRepeatExtra(r.v)}>{r.l}</button>
            ))}
          </div>
          {repeatExtra > 0 && (
            <p className="sched-hint">Будет создано {repeatExtra + 1} записей — то же время и день недели, {repeatExtra} недель подряд.</p>
          )}
        </div>
```

Заменить на:

```tsx
        <div className="sched-field">
          <label>Повторять еженедельно</label>
          <div className="sched-seg-full">
            <button className={!repeatEnabled ? 'on' : ''} onClick={() => setRepeatEnabled(false)}>Нет</button>
            <button className={repeatEnabled ? 'on' : ''} onClick={() => setRepeatEnabled(true)}>Повторять</button>
          </div>
          {repeatEnabled && (
            <>
              <div className="bk-addon-hours" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  onClick={() => setRepeatWeeks((w) => Math.max(2, w - 1))}
                  className="ds-icon-btn"
                >
                  <Minus size={16} />
                </button>
                <span className="val">{repeatWeeks} нед</span>
                <button
                  type="button"
                  onClick={() => setRepeatWeeks((w) => Math.min(8, w + 1))}
                  className="ds-icon-btn"
                >
                  <Plus size={16} />
                </button>
              </div>
              <p className="sched-hint">Будет создано {repeatWeeks} записей — то же время и день недели, {repeatWeeks} недель подряд.</p>
            </>
          )}
        </div>
```

- [ ] **Step 5: Обновить кнопку отправки**

Найти:

```tsx
        <button onClick={handleSubmit} disabled={submitting || !serviceId || !clientName} className="sched-submit">
          {submitting ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
          {submitting ? 'Записываем…' : repeatExtra > 0 ? `Записать · ${repeatExtra + 1}` : 'Записать'}
        </button>
```

Заменить на:

```tsx
        <button onClick={handleSubmit} disabled={submitting || !serviceId || !clientName} className="sched-submit">
          {submitting ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
          {submitting ? 'Записываем…' : repeatEnabled ? `Записать · ${repeatWeeks}` : 'Записать'}
        </button>
```

- [ ] **Step 6: Обновить место использования `<ManualBookingModal onCreated={...}>`**

`SchedulePage.tsx` не имеет собственной инфраструктуры тостов (проверено — только `alert()` используется в похожих местах этого файла для одноразовых сообщений), поэтому для сообщения о количестве созданных записей используем `alert()`, не создавая новую инфраструктуру ради этой фичи.

Найти:

```tsx
          onCreated={() => { setShowManualBooking(false); fetchBookings() }}
```

Заменить на:

```tsx
          onCreated={(toastMessage) => {
            setShowManualBooking(false)
            fetchBookings()
            if (toastMessage) alert(toastMessage)
          }}
```

- [ ] **Step 7: Собрать и проверить типы**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo/web/frontend && npx vite build 2>&1 | tail -20 && npx tsc -b 2>&1 | tail -40`
Expected: `vite build` — `✓ built in ...`; `tsc -b` — без вывода.

- [ ] **Step 8: Коммит**

```bash
cd /Users/glorygodsochiworshipteam/ServGo/repo
git add web/frontend/src/pages/admin/SchedulePage.tsx
git commit -m "feat(web/admin): атомарный повтор ручной записи (2-8 недель) в ManualBookingModal

Заменяет прежнюю небезопасную реализацию (N последовательных вызовов
без проверки конфликтов, риск частичного успеха) на один атомарный
вызов POST /admin/bookings с repeat_weeks."
```

---

### Task 8: Браузерная проверка (mock-харнесс)

**Files:** нет изменений кода — только проверка.

- [ ] **Step 1: Собрать прод-бандл**

Run: `cd /Users/glorygodsochiworshipteam/ServGo/repo/web/frontend && npx vite build 2>&1 | tail -10`
Expected: `✓ built in ...`

- [ ] **Step 2: Обновить mock.js для сценария конфликта**

В `mock_client.js` (сценарии клиентской записи, в scratchpad сессии) добавить обработку `POST /bookings` с `repeat_weeks` в теле: если тестовая дата равна специальному маркеру (например, второй occurrence совпадает с датой уже существующей тестовой брони `iso(0)` из `myBookings`), вернуть `409` с `{"detail": "Занято: ..."}`, иначе — вернуть массив из `repeat_weeks` (или 1) фейковых `Booking`-объектов с разными `date`.

- [ ] **Step 3: Проверить happy path в браузере**

Через Playwright: открыть `/book/:businessId` (либо `/admin` → расписание → ручная запись), включить "Повторять каждую неделю", выставить 4 недели, отправить форму. Проверить:
- Экран успеха (клиент) показывает основную дату + строку "Повторится ещё" с 3 дополнительными датами.
- В админке — после создания списка `fetchBookings()` подтягивает новые записи на расписании (визуально проверить, что 4 карточки появились в соответствующие недели при переключении на "Неделя").

- [ ] **Step 4: Проверить сценарий конфликта**

Настроить mock так, чтобы одна из 4 недель конфликтовала. Отправить форму. Проверить:
- Ошибка показывается в форме (`bk-error`/`sched-error`), с текстом, включающим дату конфликта.
- **Ничего не создалось** — при повторном открытии списка записей не появилось ни одной новой карточки (в mock — просто убедиться, что запрос вернул `409` и код не пытался создать частичный список).

- [ ] **Step 5: Проверить мобильную и десктопную раскладку, светлую и тёмную тему**

Как и для всех предыдущих фич в этом проекте — `browser_resize` на 390×844 и 1280×800, переключение темы, без визуальных регрессий/наложений в новом блоке переключателя.

---

### Task 9: Живая проверка на проде и деплой

**Files:** нет новых изменений кода — деплой уже закоммиченных Task 1-7.

- [ ] **Step 1: Задеплоить бэкенд**

По установленной в проекте процедуре (SFTP через paramiko, см. `scratchpad/deploy_dist.py`-подобные скрипты сессии): залить `web/api/booking_utils.py` (новый файл), `web/api/schemas.py`, `web/api/routes/client.py`, `web/api/routes/admin.py` на `/opt/booking_bot/...`, затем:

```bash
systemctl restart booking-web-api
```

Проверить сразу после: `systemctl is-active booking-web-api` → `active`, и `journalctl -u booking-web-api -n 20 --no-pager` — без трейсбеков при старте (см. пример проверки в `scratchpad/verify_restart.py` из этой же сессии).

- [ ] **Step 2: Задеплоить фронтенд**

Залить свежий `web/frontend/dist` (обычная процедура, без рестарта — статика).

- [ ] **Step 3: Живой smoke-тест на проде — прямой вызов реального кода роута (без HTTP/токенов)**

Вместо похода через HTTP + Telegram-авторизацию (нет простого способа получить реальный admin-токен из скрипта) — вызвать РЕАЛЬНУЮ функцию роута `create_manual_booking` напрямую на сервере, с реальной БД-сессией и реальным owner-пользователем. Это выполняет тот же код, что и продовый HTTP-эндпоинт (FastAPI `Depends(...)` — это просто дефолтные значения параметров, вызывать функцию напрямую с готовыми объектами полностью корректно).

Загрузить на сервер через paramiko SFTP (по аналогии со скриптами `check_pg_columns.py`/`check_pg_all.py` из этой сессии) и выполнить `./venv/bin/python3 /tmp/smoke_recurring.py` из `/opt/booking_bot`:

```python
import asyncio, sys
sys.path.insert(0, '/opt/booking_bot')
from datetime import date, timedelta
from sqlalchemy import select
from web.api.database import async_session
from web.api.routes.admin import create_manual_booking
from web.api.schemas import ManualBookingIn
from database.models import User, Business, Service

FAR_DATE = date.today() + timedelta(days=400)  # заведомо без реальных конфликтов

async def main():
    async with async_session() as db:
        biz = (await db.execute(select(Business).limit(1))).scalar_one()
        owner = (await db.execute(select(User).where(User.id == biz.owner_id))).scalar_one()
        svc = (await db.execute(select(Service).where(Service.business_id == biz.id).limit(1))).scalar_one()

        data = ManualBookingIn(
            business_id=biz.id, service_id=svc.id, date=FAR_DATE, time="09:00",
            client_name="ТЕСТ-повтор", repeat_weeks=3,
        )
        created = await create_manual_booking(data=data, user=owner, db=db)
        print("Создано:", [(b.id, b.date, b.time) for b in created])
        assert len(created) == 3, f"Ожидалось 3 записи, получено {len(created)}"

        # Повторный запрос на ТЕ ЖЕ даты — должен упасть с конфликтом, ничего не создав.
        try:
            await create_manual_booking(data=data, user=owner, db=db)
            print("ОШИБКА: второй запрос не упал с конфликтом!")
        except Exception as e:
            print("Конфликт корректно отклонён:", e)

asyncio.run(main())
```

Ожидаемый вывод: `Создано: [...]` с 3 датами (`FAR_DATE`, `+7`, `+14`), затем `Конфликт корректно отклонён: ...` с упоминанием всех 3 дат в тексте ошибки.

- [ ] **Step 4: Убрать тестовые записи**

Тем же скриптом (или отдельным) удалить/отменить 3 созданные тестовые записи, чтобы не засорять реальное расписание владельца — по `id`, напечатанным в Step 3 (`created[i].id`), выполнить `UPDATE bookings SET status='cancelled' WHERE id IN (...)` через тот же `async_session`, либо прямой SQL через уже использовавшийся в сессии `asyncpg`-скрипт.

- [ ] **Step 4: Финальная проверка логов**

`journalctl -u booking-web-api --since '5 minutes ago' --no-pager | grep -i error` — убедиться, что тестовые запросы не оставили новых трейсбеков.

- [ ] **Step 5: Обновить memory-заметку проекта**

Дописать в `booking-bot-deploy.md` (файл памяти проекта) короткую запись о фиче: где реализована атомарная проверка конфликтов (`compute_recurring_dates` + двухфазная схема в `client.py`/`admin.py`), и что `POST /admin/bookings` теперь возвращает список, а не одиночный объект (важно для будущих правок этого эндпоинта).
