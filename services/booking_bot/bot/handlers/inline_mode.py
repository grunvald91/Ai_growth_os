"""
Inline-режим бота
Позволяет использовать бота через @username в любом чате:
  - "мои" / "записи" — список ближайших записей клиента
  - "визитка" — визитка бизнеса (для владельцев)
  - пустой запрос — подсказки по использованию
"""
import logging
from datetime import date, datetime

from aiogram import Router, Bot
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from database.database import async_session_maker
from database.crud import (
    get_user_by_telegram_id,
    get_user_bookings,
    get_user_businesses,
    get_business_services,
)
from bot.config import BOT_USERNAME

logger = logging.getLogger(__name__)
router = Router()

# Иконки для inline-результатов (Twemoji CDN)
_TWEMOJI = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72"
ICON_CALENDAR = f"{_TWEMOJI}/1f4c5.png"
ICON_BRIEFCASE = f"{_TWEMOJI}/1f4bc.png"
ICON_INFO = f"{_TWEMOJI}/2139.png"

# Иконка по типу бизнеса
BUSINESS_TYPE_ICONS = {
    "salon": f"{_TWEMOJI}/1f487.png",        # 💇
    "auto_service": f"{_TWEMOJI}/1f527.png",  # 🔧
    "psychologist": f"{_TWEMOJI}/1f9e0.png",  # 🧠
    "medicine": f"{_TWEMOJI}/1f3e5.png",      # 🏥
    "sport": f"{_TWEMOJI}/1f3cb.png",         # 🏋️
    "education": f"{_TWEMOJI}/1f4da.png",     # 📚
    "venue_rental": f"{_TWEMOJI}/1f3e2.png",  # 🏢
}

# Маршрутизация по первому слову запроса
INLINE_COMMANDS = {
    "мои": "bookings",
    "записи": "bookings",
    "визитка": "card",
}

STATUS_LABELS = {
    "confirmed": "✅ Подтверждена",
    "pending": "⏳ Ожидает",
    "pending_prepayment": "💳 Ожидает предоплату",
    "prepayment_sent": "💳 Предоплата отправлена",
    "prepayment_confirmed": "✅ Предоплата подтверждена",
    "completed": "✔️ Завершена",
    "cancelled": "❌ Отменена",
}

BUSINESS_TYPE_LABELS = {
    "salon": "💇 Салон красоты",
    "auto_service": "🔧 Автосервис",
    "psychologist": "🧠 Психолог",
    "medicine": "🏥 Медицина",
    "sport": "🏋️ Спорт",
    "education": "📚 Образование",
    "venue_rental": "🏢 Аренда помещений",
    "other": "📌 Другое",
}


@router.inline_query()
async def handle_inline_query(query: InlineQuery):
    """Единая точка входа для всех inline-запросов"""
    text = query.query.strip().lower()
    first_word = text.split()[0] if text else ""
    command = INLINE_COMMANDS.get(first_word)

    if command == "bookings":
        results = await _build_bookings_results(query.from_user.id)
    elif command == "card":
        results = await _build_card_results(query.from_user.id)
    else:
        results = _build_help_results()

    await query.answer(results, cache_time=5, is_personal=True)


# ──────────────────────────────────────────────
# Мои записи
# ──────────────────────────────────────────────

async def _build_bookings_results(telegram_id: int) -> list:
    """Формирует список ближайших записей пользователя"""
    async with async_session_maker() as session:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return [_article(
                id="no_user",
                title="Вы ещё не зарегистрированы",
                description="Напишите боту /start для начала",
                text="Я ещё не зарегистрирован в боте записи.",
            )]

        bookings = await get_user_bookings(session, user.id)

        # Оставляем только предстоящие и активные
        today = date.today()
        upcoming = [
            b for b in bookings
            if b.booking_date >= today
            and b.status not in ("cancelled", "completed")
        ]

        if not upcoming:
            return [_article(
                id="no_bookings",
                title="Нет предстоящих записей",
                description="У вас пока нет активных записей",
                text="У меня нет предстоящих записей.",
            )]

        results = []
        for i, b in enumerate(upcoming[:20]):  # Telegram лимит 50, берём 20
            dt = datetime.combine(b.booking_date, b.booking_time)
            date_str = dt.strftime("%d.%m.%Y")
            time_str = dt.strftime("%H:%M")
            status = STATUS_LABELS.get(b.status, b.status)

            biz_name = b.business.name if b.business else "—"
            svc_name = b.service.name if b.service else "—"
            emp_name = b.employee.name if b.employee else ""

            title = f"📅 {date_str} в {time_str} — {svc_name}"
            description = f"{biz_name}"
            if emp_name:
                description += f" | {emp_name}"
            description += f" | {status}"

            # Текст, который отправится в чат
            lines = [
                f"📅 Моя запись на {date_str} в {time_str}",
                f"📍 {biz_name}",
                f"💼 {svc_name}",
            ]
            if emp_name:
                lines.append(f"👤 {emp_name}")
            lines.append(f"Статус: {status}")

            # Кнопка «Записаться» ведёт в бота
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📅 Записаться",
                    url=f"https://t.me/{BOT_USERNAME}?start=book",
                )
            ]])

            results.append(InlineQueryResultArticle(
                id=f"booking_{b.id}_{i}",
                title=title,
                description=description,
                thumbnail_url=ICON_CALENDAR,
                thumbnail_width=72,
                thumbnail_height=72,
                input_message_content=InputTextMessageContent(
                    message_text="\n".join(lines),
                ),
                reply_markup=kb,
            ))

        return results


# ──────────────────────────────────────────────
# Визитка бизнеса
# ──────────────────────────────────────────────

async def _build_card_results(telegram_id: int) -> list:
    """Формирует визитки бизнесов пользователя-владельца"""
    async with async_session_maker() as session:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return [_article(
                id="no_user",
                title="Вы ещё не зарегистрированы",
                description="Напишите боту /start для начала",
                text="Я ещё не зарегистрирован в боте записи.",
            )]

        businesses = await get_user_businesses(session, user.id)
        if not businesses:
            return [_article(
                id="no_biz",
                title="У вас нет бизнесов",
                description="Создайте бизнес в режиме владельца",
                text="У меня пока нет зарегистрированных бизнесов.",
            )]

        results = []
        for biz in businesses:
            services = await get_business_services(session, biz.id)

            type_label = BUSINESS_TYPE_LABELS.get(biz.business_type, biz.business_type)

            # Формируем красивую визитку
            lines = [
                f"🏢 {biz.name}",
                f"{type_label}",
            ]

            if biz.description:
                lines.append(f"\n{biz.description}")

            if biz.address:
                lines.append(f"\n📍 {biz.address}")

            if biz.phone:
                lines.append(f"📞 {biz.phone}")

            # Режим работы
            if biz.working_hours_start and biz.working_hours_end:
                wh_start = biz.working_hours_start.strftime("%H:%M")
                wh_end = biz.working_hours_end.strftime("%H:%M")
                lines.append(f"🕐 {wh_start} — {wh_end}")

            # Услуги
            if services:
                lines.append("\n📋 Услуги:")
                for svc in services[:10]:
                    price_str = f" — {svc.price} ₽" if svc.price else ""
                    lines.append(f"  • {svc.name}{price_str}")
                if len(services) > 10:
                    lines.append(f"  ...и ещё {len(services) - 10}")

            # Кнопка записи
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📅 Записаться",
                    url=f"https://t.me/{BOT_USERNAME}?start=biz_{biz.id}",
                )
            ]])

            title = f"🏢 {biz.name}"
            desc = type_label
            if biz.address:
                desc += f" | {biz.address}"

            biz_icon = BUSINESS_TYPE_ICONS.get(biz.business_type, ICON_BRIEFCASE)

            results.append(InlineQueryResultArticle(
                id=f"card_{biz.id}",
                title=title,
                description=desc,
                thumbnail_url=biz_icon,
                thumbnail_width=72,
                thumbnail_height=72,
                input_message_content=InputTextMessageContent(
                    message_text="\n".join(lines),
                ),
                reply_markup=kb,
            ))

        return results


# ──────────────────────────────────────────────
# Подсказки (пустой запрос)
# ──────────────────────────────────────────────

def _build_help_results() -> list:
    """Подсказки при пустом inline-запросе — нажатие сразу подставляет нужный запрос"""
    return [
        InlineQueryResultArticle(
            id="help_bookings",
            title="📅 Мои записи",
            description="Нажмите — покажет ваши ближайшие записи",
            thumbnail_url=ICON_CALENDAR,
            thumbnail_width=72,
            thumbnail_height=72,
            input_message_content=InputTextMessageContent(
                message_text="📅 Мои записи — используйте inline-режим бота",
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📅 Показать мои записи",
                    switch_inline_query_current_chat="мои",
                )
            ]]),
        ),
        InlineQueryResultArticle(
            id="help_card",
            title="🏢 Визитка бизнеса",
            description="Нажмите — отправит карточку вашего бизнеса",
            thumbnail_url=ICON_BRIEFCASE,
            thumbnail_width=72,
            thumbnail_height=72,
            input_message_content=InputTextMessageContent(
                message_text="🏢 Визитка бизнеса — используйте inline-режим бота",
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🏢 Показать визитку",
                    switch_inline_query_current_chat="визитка",
                )
            ]]),
        ),
    ]


# ──────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────

def _article(*, id: str, title: str, description: str, text: str, thumb: str = ICON_INFO) -> InlineQueryResultArticle:
    """Быстрое создание InlineQueryResultArticle"""
    return InlineQueryResultArticle(
        id=id,
        title=title,
        description=description,
        thumbnail_url=thumb,
        thumbnail_width=72,
        thumbnail_height=72,
        input_message_content=InputTextMessageContent(message_text=text),
    )
