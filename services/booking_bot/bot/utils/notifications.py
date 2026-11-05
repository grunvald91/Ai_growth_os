"""
Система уведомлений и напоминаний
"""
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot

from database import async_session_maker
from database.crud import get_pending_reminders, mark_reminder_sent
from bot.utils.validators import format_date_with_weekday, format_phone


async def send_reminder(bot: Bot, booking):
    """Отправить напоминание о записи"""
    try:
        # Формируем кликабельный телефон (Telegram автоматически делает его ссылкой)
        phone_text = ""
        if booking.business.phone:
            formatted_phone = format_phone(booking.business.phone)
            tel_link = formatted_phone if formatted_phone.startswith('+') else '+7' + formatted_phone[1:]
            phone_text = f'📞 Телефон: <a href="tel:{tel_link}">{formatted_phone}</a>'

        # Формируем кликабельный адрес (ссылка на Яндекс.Карты)
        address_text = ""
        if booking.business.address:
            # Кодируем адрес для URL
            from urllib.parse import quote
            encoded_address = quote(booking.business.address)
            yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
            address_text = f'📍 Адрес: <a href="{yandex_maps_url}">{booking.business.address}</a> (открыть на карте)'

        # Формируем текст напоминания
        reminder_text = f"""
🔔 <b>Напоминание о записи</b>

Завтра у вас запись:
📅 Дата: {format_date_with_weekday(booking.booking_date)}
🕐 Время: {booking.booking_time.strftime('%H:%M')}
🏢 Заведение: {booking.business.name}
💼 Услуга: {booking.service.name}

{address_text}
{phone_text}

Ждем вас!
"""

        # Отправляем клиенту
        await bot.send_message(
            chat_id=booking.client.telegram_id,
            text=reminder_text,
            parse_mode="HTML",
            disable_web_page_preview=True  # Отключаем превью ссылок
        )

        # Отмечаем, что напоминание отправлено
        async with async_session_maker() as session:
            await mark_reminder_sent(session, booking.id)

    except Exception as e:
        print(f"Ошибка отправки напоминания для записи {booking.id}: {e}")


async def check_and_send_reminders(bot: Bot):
    """Проверить и отправить все необходимые напоминания"""
    async with async_session_maker() as session:
        # Получаем записи, для которых нужно отправить напоминание
        bookings = await get_pending_reminders(session)

        tomorrow = datetime.now().date() + timedelta(days=1)

        # Фильтруем записи на завтра
        tomorrow_bookings = [b for b in bookings if b.booking_date == tomorrow]

        for booking in tomorrow_bookings:
            await send_reminder(bot, booking)
            await asyncio.sleep(0.5)  # Небольшая задержка между отправками


async def notification_scheduler(bot: Bot):
    """
    Планировщик для периодической проверки и отправки уведомлений
    Запускается в фоне
    """
    while True:
        try:
            # Проверяем каждый час
            await check_and_send_reminders(bot)
            await asyncio.sleep(3600)  # 1 час
        except Exception as e:
            print(f"Ошибка в планировщике уведомлений: {e}")
            await asyncio.sleep(300)  # При ошибке ждем 5 минут и пробуем снова
