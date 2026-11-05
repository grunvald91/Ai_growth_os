"""Выполняет tool-вызовы: пишет в БД, отправляет Telegram-сообщения."""
import datetime
import json
import logging
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.database import async_session_maker
from database.models import Business, Booking, User, Service, Employee
from database.crud import create_booking, get_or_create_guest_user

logger = logging.getLogger(__name__)


async def execute_tool(
    tool_name:   str,
    args:        dict,
    business_id: int,
    bot=None,
    file_id:     Optional[str] = None,
) -> str:
    # Allow tool_args to override business_id (multi-business owners)
    effective_biz_id = int(args.get('business_id') or business_id or 0)
    async with async_session_maker() as session:
        try:
            result = await _exec(session, tool_name, args, effective_biz_id, bot, file_id)
            await session.commit()
            return result
        except Exception as exc:
            await session.rollback()
            logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            return "\u274c \u041e\u0448\u0438\u0431\u043a\u0430: " + str(exc)


async def _exec(
    s: AsyncSession, name: str, args: dict,
    biz_id: int, bot, file_id: Optional[str],
) -> str:

    if name == "update_name":
        await _upd(s, biz_id, name=args["new_name"])
        return "\u2705 \u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435: \u00ab" + args["new_name"] + "\u00bb"

    if name == "update_description":
        await _upd(s, biz_id, description=args["new_description"])
        return "\u2705 \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e"

    if name == "update_address":
        await _upd(s, biz_id, address=args["new_address"])
        return "\u2705 \u0410\u0434\u0440\u0435\u0441: " + args["new_address"]

    if name == "update_phone":
        await _upd(s, biz_id, phone=args["new_phone"])
        return "\u2705 \u0422\u0435\u043b\u0435\u0444\u043e\u043d: " + args["new_phone"]

    if name == "update_working_hours":
        await _upd(s, biz_id,
                   working_hours_start=_t(args["start"]),
                   working_hours_end=_t(args["end"]))
        return "\u2705 \u0427\u0430\u0441\u044b \u0440\u0430\u0431\u043e\u0442\u044b: " + args["start"] + "\u2013" + args["end"]

    if name == "update_break_time":
        if args.get("start") and args.get("end"):
            await _upd(s, biz_id, break_start=_t(args["start"]), break_end=_t(args["end"]))
            return "\u2705 \u041f\u0435\u0440\u0435\u0440\u044b\u0432: " + args["start"] + "\u2013" + args["end"]
        await _upd(s, biz_id, break_start=None, break_end=None)
        return "\u2705 \u041f\u0435\u0440\u0435\u0440\u044b\u0432 \u0443\u0431\u0440\u0430\u043d"

    if name == "update_directions":
        await _upd(s, biz_id, directions=args["text"])
        return "\u2705 \u0421\u0445\u0435\u043c\u0430 \u043f\u0440\u043e\u0435\u0437\u0434\u0430 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0430"

    if name == "delete_all_photos":
        await _upd(s, biz_id, photos="[]")
        return "\u2705 \u0412\u0441\u0435 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u0443\u0434\u0430\u043b\u0435\u043d\u044b"

    if name == "add_photo":
        if not file_id:
            return "\u274c \u0424\u043e\u0442\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \u0432 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438"
        b = await _biz(s, biz_id)
        try:
            photos = json.loads(b.photos) if b.photos else []
        except Exception:
            photos = []
        photos.append(file_id)
        await _upd(s, biz_id, photos=json.dumps(photos))
        return "\u2705 \u0424\u043e\u0442\u043e \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e (\u0432\u0441\u0435\u0433\u043e: " + str(len(photos)) + ")"

    if name == "update_video":
        if not file_id:
            return "\u274c \u0412\u0438\u0434\u0435\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \u0432 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438"
        await _upd(s, biz_id, video=file_id)
        return "\u2705 \u0412\u0438\u0434\u0435\u043e \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e"

    if name == "update_cancellation_photo":
        if not file_id:
            return "\u274c \u0424\u043e\u0442\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \u0432 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438"
        await _upd(s, biz_id, cancellation_policy_photo=file_id)
        return "\u2705 \u0424\u043e\u0442\u043e \u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0438 \u043e\u0442\u043c\u0435\u043d\u044b \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e"

    if name == "toggle_prepayment":
        await _upd(s, biz_id, prepayment_enabled=args["enabled"])
        stato = "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u0430" if args["enabled"] else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u0430"
        return "\u2705 \u041f\u0440\u0435\u0434\u043e\u043f\u043b\u0430\u0442\u0430 " + stato

    if name == "update_prepayment_settings":
        upd = {"prepayment_percent": args["percent"]}
        if args.get("phone"):   upd["payment_phone"]   = args["phone"]
        if args.get("details"): upd["payment_details"] = args["details"]
        await _upd(s, biz_id, **upd)
        return "\u2705 \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043f\u0440\u0435\u0434\u043e\u043f\u043b\u0430\u0442\u044b \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u044b"

    if name == "update_ai_instruction":
        await _upd(s, biz_id, ai_instruction=args["instruction"])
        return "\u2705 \u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f AI \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0430"

    if name == "get_current_settings":
        return _fmt(await _biz(s, biz_id))

    if name == "get_business_services":
        return await _get_services(s, biz_id)

    if name == "get_bookings":
        return await _bookings(s, biz_id, args)

    if name == "cancel_booking":
        bk = await _booking(s, args["booking_id"], biz_id)
        if not bk: return "\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
        bk.status = "cancelled"
        if args.get("reason"):
            bk.notes = (bk.notes or "") + "\n\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: " + args["reason"]
        msg = "\u274c \u0412\u0430\u0448\u0430 \u0437\u0430\u043f\u0438\u0441\u044c \u043d\u0430 " + str(bk.booking_date) + " \u0432 " + bk.booking_time.strftime("%H:%M") + " \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u0430."
        if args.get("reason"):
            msg += "\n\u041f\u0440\u0438\u0447\u0438\u043d\u0430: " + args["reason"]
        await _notify(bot, bk.client.telegram_id, msg)
        return "\u2705 \u0417\u0430\u043f\u0438\u0441\u044c #" + str(args["booking_id"]) + " \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u0430"

    if name == "reschedule_booking":
        bk = await _booking(s, args["booking_id"], biz_id)
        if not bk: return "\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
        bk.booking_date = datetime.date.fromisoformat(args["new_date"])
        bk.booking_time = _t(args["new_time"])
        await _notify(bot, bk.client.telegram_id,
                      "\ud83d\udcc5 \u0412\u0430\u0448\u0430 \u0437\u0430\u043f\u0438\u0441\u044c \u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0435\u043d\u0430 \u043d\u0430 " + args["new_date"] + " \u0432 " + args["new_time"])
        return "\u2705 \u0417\u0430\u043f\u0438\u0441\u044c #" + str(args["booking_id"]) + " \u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0435\u043d\u0430"

    if name == "add_booking_note":
        bk = await _booking(s, args["booking_id"], biz_id)
        if not bk: return "\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
        bk.notes = args["note"]
        return "\u2705 \u0417\u0430\u043c\u0435\u0442\u043a\u0430 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0430 \u043a #" + str(args["booking_id"])

    if name == "confirm_booking":
        bk = await _booking(s, args["booking_id"], biz_id)
        if not bk: return "\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
        bk.status = "confirmed"
        return "\u2705 \u0417\u0430\u043f\u0438\u0441\u044c #" + str(args["booking_id"]) + " \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430"

    if name == "create_booking_for_client":
        return await _create_booking_for_client(s, biz_id, args, bot)

    if name == "send_message_to_client":
        bk = await _booking(s, args["booking_id"], biz_id)
        if not bk: return "\u274c \u0417\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
        if not bot: return "\u274c \u0411\u043e\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"
        if bk.client.telegram_id <= 0: return "\u274c \u0423 \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u043d\u0435\u0442 Telegram"
        await bot.send_message(bk.client.telegram_id, args["text"])
        return "\u2705 \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e " + (bk.client.full_name or "\u043a\u043b\u0438\u0435\u043d\u0442\u0443")

    if name == "send_broadcast":
        return await _broadcast(s, biz_id, args["text"], bot)

    return "\u2753 \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u0444\u0443\u043d\u043a\u0446\u0438\u044f: " + name


# --- HELPERS ------------------------------------------------------------------

def _t(hm: str):
    return datetime.datetime.strptime(hm, "%H:%M").time()


async def _upd(s: AsyncSession, biz_id: int, **fields) -> None:
    res = await s.execute(select(Business).where(Business.id == biz_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise ValueError("\u0411\u0438\u0437\u043d\u0435\u0441 " + str(biz_id) + " \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
    for k, v in fields.items():
        setattr(biz, k, v)


async def _biz(s: AsyncSession, biz_id: int) -> Business:
    res = await s.execute(select(Business).where(Business.id == biz_id))
    return res.scalar_one_or_none()


async def _booking(s: AsyncSession, booking_id: int, biz_id: int):
    res = await s.execute(
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.service))
        .where(and_(Booking.id == booking_id, Booking.business_id == biz_id))
    )
    return res.scalar_one_or_none()


async def _notify(bot, telegram_id: int, text: str) -> None:
    if bot and telegram_id and telegram_id > 0:
        try:
            await bot.send_message(telegram_id, text)
        except Exception:
            pass


async def _get_services(s: AsyncSession, biz_id: int) -> str:
    svc_res = await s.execute(
        select(Service).where(and_(Service.business_id == biz_id, Service.is_active == True))
    )
    services = svc_res.scalars().all()

    emp_res = await s.execute(
        select(Employee).where(and_(Employee.business_id == biz_id, Employee.is_active == True))
    )
    employees = emp_res.scalars().all()

    if not services:
        return "\u0423 \u0431\u0438\u0437\u043d\u0435\u0441\u0430 \u043d\u0435\u0442 \u0443\u0441\u043b\u0443\u0433"

    lines = ["\u0423\u0441\u043b\u0443\u0433\u0438 \u0431\u0438\u0437\u043d\u0435\u0441\u0430 #" + str(biz_id) + ":"]
    for svc in services:
        price_str = " - " + str(svc.price) + " \u0440\u0443\u0431." if svc.price else ""
        dur_str = " (" + str(svc.duration_minutes) + " \u043c\u0438\u043d)" if svc.duration_minutes else ""
        lines.append("  ID=" + str(svc.id) + ": " + svc.name + price_str + dur_str)

    if employees:
        lines.append("\n\u0421\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u0438:")
        for emp in employees:
            lines.append("  ID=" + str(emp.id) + ": " + emp.name + " (" + emp.role + ")")

    return "\n".join(lines)


async def _create_booking_for_client(s: AsyncSession, biz_id: int, args: dict, bot) -> str:
    from bot.services.notifications import (
        notify_master_new_booking, notify_owner_new_booking, notify_administrator_new_booking,
    )

    svc_id = args["service_id"]
    book_date = datetime.date.fromisoformat(args["date"])
    book_time = _t(args["time"])
    client_name = args["client_name"]
    client_phone = args.get("client_phone") or ""

    biz = await _biz(s, biz_id)
    if not biz:
        return "\u274c \u0411\u0438\u0437\u043d\u0435\u0441 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"

    svc_res = await s.execute(select(Service).where(Service.id == svc_id))
    service = svc_res.scalar_one_or_none()
    if not service:
        return "\u274c \u0423\u0441\u043b\u0443\u0433\u0430 #" + str(svc_id) + " \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"

    # Find or create guest client by phone (гостевой профиль, привязан к телефону)
    client = await get_or_create_guest_user(s, client_name, client_phone or None)

    employee_id = args.get("employee_id")
    # Проверка конфликтов и защита от дублей — внутри create_booking
    booking = await create_booking(
        s,
        business_id=biz_id,
        client_id=client.id,
        service_id=svc_id,
        booking_date=book_date,
        booking_time=book_time,
        employee_id=employee_id,
        notes=args.get("notes"),
        status="confirmed",
    )

    if bot:
        for notify_fn in (notify_master_new_booking, notify_owner_new_booking,
                          notify_administrator_new_booking):
            try:
                await notify_fn(bot, booking)
            except Exception as e:
                logger.warning("Notify failed (%s): %s", notify_fn.__name__, e)

        if client.telegram_id and client.telegram_id > 0:
            try:
                await bot.send_message(
                    client.telegram_id,
                    "\u2705 \u0412\u0430\u0441 \u0437\u0430\u043f\u0438\u0441\u0430\u043b\u0438!\n" +
                    biz.name + "\n" +
                    str(args["date"]) + " \u0432 " + str(args["time"]) + "\n" +
                    service.name + "\n#" + str(booking.id)
                )
            except Exception:
                pass

    phone_str = " (" + client_phone + ")" if client_phone else ""
    return (
        "\u2705 \u0417\u0430\u043f\u0438\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u043d\u0430!\n" +
        "\u0418\u043c\u044f: " + client_name + phone_str + "\n" +
        "\u0411\u0438\u0437\u043d\u0435\u0441: " + biz.name + "\n" +
        "\u0414\u0430\u0442\u0430: " + str(args["date"]) + " \u0432 " + str(args["time"]) + "\n" +
        "\u0423\u0441\u043b\u0443\u0433\u0430: " + service.name + "\n" +
        "#" + str(booking.id)
    )


async def _bookings(s: AsyncSession, biz_id: int, args: dict) -> str:
    q = (
        select(Booking)
        .options(selectinload(Booking.client), selectinload(Booking.service))
        .where(Booking.business_id == biz_id)
    )
    if args.get("date"):
        q = q.where(Booking.booking_date == datetime.date.fromisoformat(args["date"]))
    if args.get("status") and args["status"] != "all":
        q = q.where(Booking.status == args["status"])
    q = q.order_by(Booking.booking_date, Booking.booking_time).limit(20)
    rows = (await s.execute(q)).scalars().all()
    if not rows:
        return "\ud83d\udced \u0417\u0430\u043f\u0438\u0441\u0435\u0439 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e"
    icons = {"pending": "\u23f3", "confirmed": "\u2705", "cancelled": "\u274c", "completed": "\u2714\ufe0f"}
    lines_out = ["\ud83d\udccb \u0417\u0430\u043f\u0438\u0441\u0435\u0439: " + str(len(rows)) + "\n"]
    for b in rows:
        cname = b.client.full_name or b.client.username or "\u0411\u0435\u0437 \u0438\u043c\u0435\u043d\u0438"
        if args.get("client_name") and args["client_name"].lower() not in cname.lower():
            continue
        svc = b.service.name if b.service else "-"
        lines_out.append(
            icons.get(b.status, "?") + " #" + str(b.id) + " | " +
            str(b.booking_date) + " " + b.booking_time.strftime("%H:%M") +
            " | " + cname + " | " + svc
        )
    return "\n".join(lines_out)


async def _broadcast(s: AsyncSession, biz_id: int, text: str, bot) -> str:
    if not bot: return "\u274c \u0411\u043e\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"
    res = await s.execute(
        select(User)
        .join(Booking, Booking.client_id == User.id)
        .where(and_(Booking.business_id == biz_id, User.telegram_id > 0))
        .distinct()
    )
    clients = res.scalars().all()
    if not clients: return "\u274c \u041d\u0435\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432 \u0434\u043b\u044f \u0440\u0430\u0441\u0441\u044b\u043b\u043a\u0438"
    sent = failed = 0
    for c in clients:
        try:
            await bot.send_message(c.telegram_id, text)
            sent += 1
        except Exception:
            failed += 1
    result = "\u2705 \u0420\u0430\u0441\u0441\u044b\u043b\u043a\u0430: " + str(sent) + " \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u0435\u0439"
    if failed:
        result += ", " + str(failed) + " \u043e\u0448\u0438\u0431\u043e\u043a"
    return result


def _fmt(b: Business) -> str:
    if not b: return "\u274c \u0411\u0438\u0437\u043d\u0435\u0441 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"
    try:
        photos = len(json.loads(b.photos)) if b.photos else 0
    except Exception:
        photos = 0
    video_str = "\u0435\u0441\u0442\u044c" if b.video else "\u043d\u0435\u0442"
    dir_str = "\u0435\u0441\u0442\u044c" if b.directions else "\u043d\u0435\u0442"
    pay_str = "\u0432\u043a\u043b " + str(b.prepayment_percent) + "%" if b.prepayment_enabled else "\u0432\u044b\u043a\u043b"
    return (
        "\ud83d\udccb \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u00ab" + b.name + "\u00bb\n\n" +
        "\ud83d\udcdd " + (b.description or "-") + "\n" +
        "\ud83d\udccd " + (b.address or "-") + "\n" +
        "\ud83d\udcde " + (b.phone or "-") + "\n" +
        "\u23f0 " + str(b.working_hours_start) + "\u2013" + str(b.working_hours_end) + "\n" +
        "\u2615 \u041f\u0435\u0440\u0435\u0440\u044b\u0432: " + (str(b.break_start) or "-") + "\n" +
        "\ud83d\udcf7 \u0424\u043e\u0442\u043e: " + str(photos) + "\n" +
        "\ud83c\udfa5 \u0412\u0438\u0434\u0435\u043e: " + video_str + "\n" +
        "\ud83d\uddfa \u041f\u0440\u043e\u0435\u0437\u0434: " + dir_str + "\n" +
        "\ud83d\udcb3 \u041f\u0440\u0435\u0434\u043e\u043f\u043b\u0430\u0442\u0430: " + pay_str
    )
