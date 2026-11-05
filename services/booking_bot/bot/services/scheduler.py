"""
Планировщик задач для автоматической отправки напоминаний
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from aiogram import Bot
from bot.services.notifications import check_and_send_reminders, auto_complete_bookings, check_and_send_subscription_reminders, expire_unpaid_bookings

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """Планировщик напоминаний"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """Запуск планировщика"""
        # Проверяем напоминания и завершаем записи каждые 5 минут
        self.scheduler.add_job(
            self._check_reminders,
            trigger=IntervalTrigger(minutes=5),
            id='check_reminders',
            name='Check and send reminders',
            replace_existing=True
        )

        # Отменяем неоплаченные брони каждые 2 минуты
        self.scheduler.add_job(
            self._expire_unpaid_bookings,
            trigger=IntervalTrigger(minutes=2),
            id='expire_unpaid_bookings',
            name='Expire unpaid bookings',
            replace_existing=True
        )

        # Проверяем истекающие абонементы каждый час
        self.scheduler.add_job(
            self._check_subscription_expiry,
            trigger=IntervalTrigger(hours=1),
            id='check_subscription_expiry',
            name='Check subscription expiry',
            replace_existing=True
        )

        self.scheduler.start()
        logger.info("Reminder scheduler started")

    async def _check_reminders(self):
        """Обертка для проверки напоминаний и автозавершения записей"""
        try:
            await check_and_send_reminders(self.bot)
            await auto_complete_bookings()
        except Exception as e:
            logger.error(f"Error in reminder scheduler: {e}")

    async def _expire_unpaid_bookings(self):
        """Автоотмена неоплаченных броней старше 10 минут"""
        try:
            await expire_unpaid_bookings(self.bot)
        except Exception as e:
            logger.error(f"Error in expire_unpaid_bookings: {e}")

    async def _check_subscription_expiry(self):
        """Проверка истекающих абонементов"""
        try:
            await check_and_send_subscription_reminders(self.bot)
        except Exception as e:
            logger.error(f"Error in subscription expiry check: {e}")

    def shutdown(self):
        """Остановка планировщика"""
        self.scheduler.shutdown()
        logger.info("Reminder scheduler stopped")
