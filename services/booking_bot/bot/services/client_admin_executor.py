"""Executor for client-side AI admin tools."""
import datetime
import logging
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from database.database import async_session_maker
from database.models import Business, Booking, User, Service, Employee, EmployeeService
from database.crud import get_business_additional_services, create_booking
from bot.utils.business_types import is_venue_rental_type

logger = logging.getLogger(__name__)


async def execute_client_tool(
    tool_name: str,
    args: dict,
    client_id: int,          # User.id
    client_telegram_id: int, # для отправки фото/сообщений
    bot=None,
) -> str:
    async with async_session_maker() as session:
        try:
            result = await _exec(session, tool_name, args, client_id, client_telegram_id, bot)
            await session.commit()
            return result
        except Exception as exc:
            await session.rollback()
            logger.error("Client tool %s failed: %s", tool_name, exc, exc_info=True)
            return f"❌ Ошибка: {exc}"


async def _exec(s, name: str, args: dict, client_id: int, client_tg_id: int, bot) -> str:
    # Cast all ID fields to int (AI may return them as strings)
    def _int(v):
        return int(v) if v is not None else None

    if name == "list_businesses":
        return await _list_businesses(s)
    if name == "get_services":
        return await _get_services(s, _int(args["business_id"]))
    if name == "get_additional_services":
        return await _get_additional_services(s, _int(args["business_id"]))
    if name == "get_available_slots":
        return await _get_slots(s, _int(args["business_id"]), _int(args["service_id"]),
                                args["date"], _int(args.get("employee_id")))
    if name == "get_my_bookings":
        return await _get_my_bookings(s, client_id, args.get("status", "upcoming"))
    if name == "create_booking":
        for k in ("business_id", "service_id", "employee_id"):
            if k in args and args[k] is not None:
                args[k] = _int(args[k])
        if "additional_service_ids" in args and args["additional_service_ids"]:
            args["additional_service_ids"] = [_int(x) for x in args["additional_service_ids"]]
        return await _create_booking(s, args, client_id, client_tg_id, bot)
    if name == "confirm_payment_sent":
        return await _confirm_payment_sent(s, _int(args["booking_id"]), client_id, bot)
    if name == "cancel_my_booking":
        return await _cancel(s, _int(args["booking_id"]), client_id, args.get("reason"), bot)
    if name == "reschedule_my_booking":
        return await _reschedule(s, _int(args["booking_id"]), client_id,
                                 args["new_date"], args["new_time"], bot)
    return f"❓ Неизвестная функция: {name}"


# ─── READ-ONLY ─────────────────────────────────────────────────────────────────

async def _list_businesses(s) -> str:
    res = await s.execute(select(Business).where(Business.is_active.is_(True)))
    businesses = res.scalars().all()
    if not businesses:
        return "Нет доступных заведений"
    lines = ["📍 Доступные заведения:\n"]
    for b in businesses:
        line = f"• {b.name}"
        if b.address:
            line += f" — {b.address}"
        lines.append(line)
    return "\n".join(lines)


async def _get_services(s, business_id: int) -> str:
    res = await s.execute(
        select(Service).where(and_(
            Service.business_id == business_id,
            Service.is_active.is_(True),
            Service.is_additional.is_(False),
        )).order_by(Service.sort_order)
    )
    services = res.scalars().all()
    if not services:
        return "Услуг не найдено"
    lines = ["📋 Услуги:\n"]
    for svc in services:
        price_info = f", {svc.price} руб." if svc.price else ""
        lines.append(f"• #{svc.id} {svc.name} ({svc.duration_minutes} мин{price_info})")
    return "\n".join(lines)


async def _get_additional_services(s, business_id: int) -> str:
    res = await s.execute(select(Business).where(Business.id == business_id))
    biz = res.scalar_one_or_none()
    if not biz:
        return "❌ Заведение не найдено"
    if not is_venue_rental_type(biz.business_type):
        return "ℹ️ Дополнительные услуги доступны только для аренды помещений"
    additional = await get_business_additional_services(s, business_id)
    if not additional:
        return "Дополнительных услуг нет"
    lines = ["🛎 Дополнительные услуги:\n"]
    for svc in additional:
        if svc.is_hourly_price:
            price_info = f", {svc.price} руб./час" if svc.price else ""
        else:
            price_info = f", {svc.price} руб." if svc.price else ""
        lines.append(f"• #{svc.id} {svc.name} ({svc.duration_minutes} мин{price_info})")
    return "\n".join(lines)


async def _get_slots(s, business_id: int, service_id: int,
                     date_str: str, employee_id: Optional[int]) -> str:
    from datetime import timedelta

    target_date = datetime.date.fromisoformat(date_str)
    res = await s.execute(select(Business).where(Business.id == business_id))
    business = res.scalar_one_or_none()
    if not business:
        return "❌ Заведение не найдено"

    res = await s.execute(select(Service).where(Service.id == service_id))
    service = res.scalar_one_or_none()
    if not service:
        return "❌ Услуга не найдена"

    interval = business.booking_interval_minutes or 30
    duration = service.duration_minutes or 30
    start_t = business.working_hours_start or datetime.time(9, 0)
    end_t = business.working_hours_end or datetime.time(21, 0)

    if employee_id:
        emp_ids = [employee_id]
    else:
        res = await s.execute(
            select(EmployeeService.employee_id)
            .where(EmployeeService.service_id == service_id)
        )
        emp_ids = [r[0] for r in res.fetchall()]
        if not emp_ids:
            res = await s.execute(
                select(Employee.id).where(and_(
                    Employee.business_id == business_id,
                    Employee.is_active.is_(True),
                ))
            )
            emp_ids = [r[0] for r in res.fetchall()]

    res = await s.execute(
        select(Booking).options(selectinload(Booking.service)).where(and_(
            Booking.business_id == business_id,
            Booking.booking_date == target_date,
            Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
        ))
    )
    bookings = res.scalars().all()

    def is_employee_free(eid: int, slot_start: datetime.datetime) -> bool:
        slot_end = slot_start + timedelta(minutes=duration)
        for bk in bookings:
            if bk.employee_id != eid:
                continue
            bk_start = datetime.datetime.combine(target_date, bk.booking_time)
            bk_dur = bk.service.duration_minutes if bk.service else 30
            bk_end = bk_start + timedelta(minutes=bk_dur)
            if not (slot_end <= bk_start or slot_start >= bk_end):
                return False
        return True

    now = datetime.datetime.now()
    current = datetime.datetime.combine(target_date, start_t)
    end_dt = datetime.datetime.combine(target_date, end_t)

    if target_date == now.date():
        min_start = now + timedelta(hours=1)
        if current < min_start:
            mins = (min_start.minute // interval + 1) * interval
            if mins >= 60:
                current = min_start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                current = min_start.replace(minute=mins, second=0, microsecond=0)

    available = []
    while current + timedelta(minutes=duration) <= end_dt:
        in_break = False
        if business.break_start and business.break_end:
            if business.break_start <= current.time() < business.break_end:
                in_break = True
        if not in_break:
            if not emp_ids or any(is_employee_free(eid, current) for eid in emp_ids):
                available.append(current.strftime("%H:%M"))
        current += timedelta(minutes=interval)

    if not available:
        return f"😔 На {date_str} нет свободных слотов. Попробуйте другую дату."
    return f"✅ Свободное время на {date_str}:\n" + ", ".join(available)


async def _get_my_bookings(s, client_id: int, status_filter: str) -> str:
    q = (
        select(Booking)
        .options(selectinload(Booking.service), selectinload(Booking.business))
        .where(Booking.client_id == client_id)
    )
    today = datetime.date.today()
    if status_filter == "upcoming":
        q = q.where(and_(
            Booking.booking_date >= today,
            Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
        ))
    elif status_filter == "cancelled":
        q = q.where(Booking.status == "cancelled")
    q = q.order_by(Booking.booking_date, Booking.booking_time).limit(15)
    rows = (await s.execute(q)).scalars().all()
    if not rows:
        return "📭 Записей не найдено"
    icons = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌",
             "completed": "✔️", "pending_prepayment": "💳", "prepayment_sent": "📤"}
    lines = ["📋 Ваши записи:\n"]
    for b in rows:
        svc = b.service.name if b.service else "—"
        biz = b.business.name if b.business else "—"
        status_label = {
            "pending_prepayment": " (ожидает оплаты)",
            "prepayment_sent": " (оплата на проверке)",
        }.get(b.status, "")
        lines.append(
            f"{icons.get(b.status, '❓')} #{b.id} | {b.booking_date} "
            f"{b.booking_time.strftime('%H:%M')} | {biz} — {svc}{status_label}"
        )
    return "\n".join(lines)


# ─── MUTATIONS ─────────────────────────────────────────────────────────────────

async def _create_booking(s, args: dict, client_id: int,
                          client_tg_id: int, bot) -> str:
    from bot.services.notifications import (
        notify_master_new_booking, notify_owner_new_booking, notify_administrator_new_booking,
    )

    biz_id = args["business_id"]
    svc_id = args["service_id"]
    book_date = datetime.date.fromisoformat(args["date"])
    book_time = datetime.datetime.strptime(args["time"], "%H:%M").time()

    res = await s.execute(select(Business).where(Business.id == biz_id))
    business = res.scalar_one_or_none()
    if not business:
        return "❌ Заведение не найдено"

    res = await s.execute(select(Service).where(Service.id == svc_id))
    service = res.scalar_one_or_none()
    if not service:
        return "❌ Услуга не найдена"

    # Определить нужна ли предоплата
    need_prepayment = (
        business.prepayment_enabled
        and business.payment_phone
        and service.price
        and service.price > 0
    )
    initial_status = "pending_prepayment" if need_prepayment else "confirmed"

    additional_ids = args.get("additional_service_ids") or []
    additional_services = {}
    if additional_ids:
        add_svc_result = await s.execute(select(Service).where(Service.id.in_(additional_ids)))
        for add_svc in add_svc_result.scalars().all():
            additional_services[add_svc.id] = add_svc

    prepayment_amount = None
    if need_prepayment:
        prepayment_percent = business.prepayment_percent or 100
        main_duration_hours = (service.duration_minutes or 60) / 60
        addon_price_total = 0
        for i in additional_ids:
            if i in additional_services:
                add_svc = additional_services[i]
                if getattr(add_svc, 'is_hourly_price', False):
                    addon_price_total += int((add_svc.price or 0) * main_duration_hours)
                else:
                    addon_price_total += add_svc.price or 0
        total_price = (service.price or 0) + addon_price_total
        prepayment_amount = int(total_price * prepayment_percent / 100)

    # Создать основную запись и записи для доп. услуг одной серией
    # (проверка конфликтов и защита от дублей — внутри create_booking).
    # Если конфликт возникнет на любом шаге — откатываем всю серию, а не
    # только текущий вызов (create_booking коммитит на каждом вызове).
    created_bookings = []
    try:
        booking = await create_booking(
            s,
            business_id=biz_id,
            client_id=client_id,
            service_id=svc_id,
            booking_date=book_date,
            booking_time=book_time,
            employee_id=args.get("employee_id"),
            notes=args.get("notes"),
            status=initial_status,
            prepayment_amount=prepayment_amount,
        )
        created_bookings.append(booking)

        # Создать записи для доп. услуг (если есть)
        for add_id in additional_ids:
            if add_id in additional_services:
                extra_booking = await create_booking(
                    s,
                    business_id=biz_id,
                    client_id=client_id,
                    service_id=add_id,
                    booking_date=book_date,
                    booking_time=book_time,
                    employee_id=args.get("employee_id"),
                    status=initial_status,
                    prepayment_amount=prepayment_amount,
                )
                created_bookings.append(extra_booking)
    except ValueError as e:
        if created_bookings:
            from sqlalchemy import delete as sa_delete
            await s.execute(
                sa_delete(Booking).where(Booking.id.in_([b.id for b in created_bookings]))
            )
            await s.commit()
        return f"❌ {e}"

    biz_name = args.get("business_name") or business.name
    svc_name = args.get("service_name") or service.name

    if need_prepayment:
        response = (
            f"✅ Запись создана!\n"
            f"📍 {biz_name}\n"
            f"📅 {args['date']} в {args['time']}\n"
            f"💆 {svc_name}\n"
            f"#{booking.id}\n\n"
            f"💳 Требуется предоплата: {prepayment_amount} руб."
            + (f" ({business.prepayment_percent or 100}% от {total_price} руб.)" if (business.prepayment_percent or 100) < 100 else "")
            + f"\n📱 Перевести на номер: {business.payment_phone}"
        )
        if business.payment_details:
            response += f"\n📝 Реквизиты: {business.payment_details}"
        response += (
            f"\n\nПосле перевода скажите мне «оплатил» или «отправил оплату» "
            f"и я уведомлю заведение для проверки."
        )

        # Отправить фото условий отмены если есть
        if bot and business.cancellation_policy_photo and client_tg_id:
            try:
                await bot.send_photo(
                    client_tg_id,
                    photo=business.cancellation_policy_photo,
                    caption="📋 Условия отмены бронирования"
                )
            except Exception:
                pass

        return response

    # Без предоплаты — сразу подтверждено
    # Eagerly load relationships for notification functions
    await s.refresh(booking, attribute_names=["client", "employee", "service", "business"])
    # Уведомить мастера, владельца, администратора
    if bot:
        for notify_fn in (notify_master_new_booking, notify_owner_new_booking,
                          notify_administrator_new_booking):
            try:
                await notify_fn(bot, booking)
            except Exception as e:
                logger.warning("Notify failed (%s): %s", notify_fn.__name__, e)

    # Показать условия отмены если есть
    cancel_note = ""
    if bot and business.cancellation_policy_photo and client_tg_id:
        try:
            await bot.send_photo(
                client_tg_id,
                photo=business.cancellation_policy_photo,
                caption="📋 Условия отмены бронирования"
            )
        except Exception:
            pass

    return (
        f"✅ Запись подтверждена!\n"
        f"📍 {biz_name}\n"
        f"📅 {args['date']} в {args['time']}\n"
        f"💆 {svc_name}\n"
        f"#{booking.id}"
    )


async def _confirm_payment_sent(s, booking_id: int, client_id: int, bot) -> str:
    res = await s.execute(
        select(Booking)
        .options(selectinload(Booking.business), selectinload(Booking.service),
                 selectinload(Booking.client))
        .where(and_(Booking.id == booking_id, Booking.client_id == client_id))
    )
    bk = res.scalar_one_or_none()
    if not bk:
        return "❌ Запись не найдена"
    if bk.status not in ("pending_prepayment",):
        return f"ℹ️ Статус записи: {bk.status}. Подтверждение уже было отправлено или запись не требует оплаты."

    bk.status = "prepayment_sent"

    # Уведомить владельца
    if bot and bk.business:
        try:
            res2 = await s.execute(select(User).where(User.id == bk.business.owner_id))
            owner = res2.scalar_one_or_none()
            if owner and owner.telegram_id:
                client_name = bk.client.full_name or bk.client.username or "Клиент"
                svc_name = bk.service.name if bk.service else "услуга"
                await bot.send_message(
                    owner.telegram_id,
                    f"💳 Клиент отправил предоплату!\n\n"
                    f"👤 {client_name}\n"
                    f"📅 {bk.booking_date} в {bk.booking_time.strftime('%H:%M')}\n"
                    f"💆 {svc_name}\n"
                    f"💰 {bk.prepayment_amount} руб.\n\n"
                    f"Пожалуйста, проверьте оплату и подтвердите запись."
                )
        except Exception as e:
            logger.warning("Failed to notify owner about payment: %s", e)

    return (
        f"✅ Отлично! Уведомили заведение о вашей оплате по записи #{booking_id}.\n"
        f"Ожидайте подтверждения от заведения."
    )


async def _cancel(s, booking_id: int, client_id: int, reason: Optional[str], bot) -> str:
    from bot.services.notifications import notify_all_owners_cancelled_booking

    res = await s.execute(
        select(Booking).options(selectinload(Booking.business)).where(
            and_(Booking.id == booking_id, Booking.client_id == client_id)
        )
    )
    bk = res.scalar_one_or_none()
    if not bk:
        return "❌ Запись не найдена или не принадлежит вам"
    if bk.status == "cancelled":
        return "ℹ️ Запись уже отменена"
    bk.status = "cancelled"
    if reason:
        bk.notes = (bk.notes or "") + f"\nОтменено клиентом: {reason}"

    if bot:
        try:
            await notify_all_owners_cancelled_booking(bot, bk)
        except Exception as e:
            logger.warning("Failed to notify about cancellation: %s", e)

    return f"✅ Запись #{booking_id} отменена"


async def _reschedule(s, booking_id: int, client_id: int,
                      new_date: str, new_time: str, bot) -> str:
    from bot.services.notifications import notify_owner_rescheduled_booking

    res = await s.execute(
        select(Booking).where(and_(Booking.id == booking_id, Booking.client_id == client_id))
    )
    bk = res.scalar_one_or_none()
    if not bk:
        return "❌ Запись не найдена или не принадлежит вам"

    old_date = bk.booking_date
    old_time = bk.booking_time
    bk.booking_date = datetime.date.fromisoformat(new_date)
    bk.booking_time = datetime.datetime.strptime(new_time, "%H:%M").time()

    if bot:
        try:
            await notify_owner_rescheduled_booking(bot, bk, old_date, old_time)
        except Exception as e:
            logger.warning("Failed to notify owner about reschedule: %s", e)

    return f"✅ Запись #{booking_id} перенесена на {new_date} в {new_time}"


# ─── CONTEXT HELPER ────────────────────────────────────────────────────────────

async def get_client_context(telegram_id: int):
    """Returns (user_id, context_packet) for AI prompt."""
    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = res.scalar_one_or_none()
        if not user:
            return None, {}

        res = await session.execute(
            select(Business).where(Business.is_active.is_(True))
        )
        bizs = res.scalars().all()

        from database.crud import get_business_additional_services
        from bot.utils.business_types import is_venue_rental_type

        biz_has_additional = {}
        for b in bizs:
            if is_venue_rental_type(b.business_type):
                add_svcs = await get_business_additional_services(session, b.id)
                biz_has_additional[b.id] = len(add_svcs) > 0
            else:
                biz_has_additional[b.id] = False

        # Services per business
        biz_services = {}
        for b in bizs:
            svc_res = await session.execute(
                select(Service).where(
                    Service.business_id == b.id,
                    Service.is_active.is_(True),
                ).order_by(Service.sort_order)
            )
            biz_services[b.id] = [
                {"id": s.id, "name": s.name, "price": s.price,
                 "duration": s.duration_minutes, "is_additional": s.is_additional or False}
                for s in svc_res.scalars().all()
            ]

        # Employees per business
        biz_employees = {}
        for b in bizs:
            emp_res = await session.execute(
                select(Employee).where(
                    Employee.business_id == b.id,
                    Employee.is_active.is_(True),
                )
            )
            biz_employees[b.id] = [
                {"id": e.id, "name": e.name, "role": e.role or "master",
                 "specialization": e.specialization or ""}
                for e in emp_res.scalars().all()
            ]

        # AI instructions
        ai_instructions = {}
        for b in bizs:
            if getattr(b, "ai_instruction", None) and b.ai_instruction.strip():
                ai_instructions["business"] = b.ai_instruction.strip()

        svc_ai = []
        emp_ai = []
        for b in bizs:
            svc_res2 = await session.execute(
                select(Service).where(
                    Service.business_id == b.id,
                    Service.is_active.is_(True),
                )
            )
            for s in svc_res2.scalars().all():
                if getattr(s, "ai_instruction", None) and s.ai_instruction.strip():
                    svc_ai.append({"name": s.name, "instruction": s.ai_instruction.strip()})

            emp_res2 = await session.execute(
                select(Employee).where(
                    Employee.business_id == b.id,
                    Employee.is_active.is_(True),
                )
            )
            for e in emp_res2.scalars().all():
                if getattr(e, "ai_instruction", None) and e.ai_instruction.strip():
                    emp_ai.append({"name": e.name, "instruction": e.ai_instruction.strip()})

        if svc_ai:
            ai_instructions["services"] = svc_ai
        if emp_ai:
            ai_instructions["employees"] = emp_ai

        # Build business dicts
        businesses = []
        for b in bizs:
            wh = ""
            if b.working_hours_start:
                wh = f"{b.working_hours_start.strftime('%H:%M')}\u2013{b.working_hours_end.strftime('%H:%M')}"
            businesses.append({
                "id": b.id, "name": b.name, "address": b.address or "",
                "working_hours": wh,
                "business_type": b.business_type or "",
                "prepayment_enabled": bool(b.prepayment_enabled and b.payment_phone),
                "prepayment_percent": b.prepayment_percent,
                "has_cancellation_policy": bool(b.cancellation_policy_photo),
                "has_additional_services": biz_has_additional.get(b.id, False),
                "services": biz_services.get(b.id, []),
                "employees": biz_employees.get(b.id, []),
            })

        # Client bookings
        today = datetime.date.today()
        res = await session.execute(
            select(Booking)
            .options(selectinload(Booking.service), selectinload(Booking.business))
            .where(and_(
                Booking.client_id == user.id,
                Booking.booking_date >= today,
                Booking.status.in_(["pending", "confirmed", "pending_prepayment", "prepayment_sent"]),
            ))
            .order_by(Booking.booking_date, Booking.booking_time)
            .limit(10)
        )
        client_bookings = [
            {
                "id": bk.id,
                "business_name": bk.business.name if bk.business else "\u2014",
                "service_name": bk.service.name if bk.service else "\u2014",
                "date": str(bk.booking_date),
                "time": bk.booking_time.strftime("%H:%M"),
                "status": bk.status,
            }
            for bk in res.scalars().all()
        ]

        return user.id, {
            "businesses": businesses,
            "client_bookings": client_bookings,
            "ai_instructions": ai_instructions,
        }
