"""
Главный файл бота
Точка входа для запуска
"""
import asyncio
import logging
import os
from aiogram import Dispatcher
from aiogram.types import Update
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN, WEBAPP_HOST, WEBAPP_PORT, WEBAPP_URL
from bot.telegram_transport import create_bot
from bot.handlers import common, client, admin, master, admin_management, administrator, subscription_admin, subscription_client, inline_mode, account_link
from database import init_db
from bot.services.scheduler import ReminderScheduler


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def _build_storage():
    """Создать FSM-storage: Redis если доступен, иначе MemoryStorage."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis
        redis = Redis.from_url(redis_url)
        await redis.ping()
        logger.info("FSM storage: Redis at %s", redis_url)
        return RedisStorage(redis=redis)
    except Exception as e:
        logger.warning("FSM storage: Redis unavailable (%s), falling back to MemoryStorage", e)
        return MemoryStorage()


async def run_webapp_server():
    """Запуск веб-сервера для Mini App"""
    if not WEBAPP_URL:
        logger.warning("WEBAPP_URL не указан, веб-сервер не запускается")
        return None

    try:
        from webapp.server import run_webapp
        runner = await run_webapp(WEBAPP_HOST, WEBAPP_PORT)
        logger.info(f"WebApp сервер запущен на {WEBAPP_HOST}:{WEBAPP_PORT}")
        return runner
    except Exception as e:
        logger.error(f"Ошибка запуска WebApp сервера: {e}")
        return None


async def main():
    """Основная функция запуска бота"""
    logger.info("Запуск бота...")

    # Инициализация бота и диспетчера
    bot = create_bot(BOT_TOKEN, os.getenv("BOT_PROXY_URL"))
    storage = await _build_storage()
    dp = Dispatcher(storage=storage)

    # Регистрация роутеров (порядок важен!)
    dp.include_router(account_link.router)
    dp.include_router(common.router)
    dp.include_router(admin_management.router)
    dp.include_router(administrator.router)  # Администратор бизнеса (новая роль)
    dp.include_router(master.router)
    dp.include_router(subscription_admin.router)  # Абонементы (владелец)
    dp.include_router(subscription_client.router)  # Абонементы (клиент)
    dp.include_router(client.router)
    dp.include_router(admin.router)
    dp.include_router(inline_mode.router)  # Inline-режим (@bot запрос)

    # Инициализация базы данных
    logger.info("Инициализация базы данных...")
    await init_db()
    logger.info("База данных готова")

    # Запуск планировщика уведомлений
    scheduler = ReminderScheduler(bot)
    scheduler.start()
    logger.info("Планировщик уведомлений запущен")

    # Запуск веб-сервера для Mini App
    webapp_runner = await run_webapp_server()

    # Логирование всех обновлений (outer middleware)
    @dp.update.outer_middleware()
    async def log_all_updates(handler, event: Update, data):
        if event.message:
            text = event.message.text or ""
            safe_text = "/start account_[redacted]" if text.startswith("/start account_") else text
            logger.info("[DEBUG] Message: text='%s', from=%s", safe_text, event.message.from_user.id)
        elif event.callback_query:
            logger.info(f"[DEBUG] Callback: data='{event.callback_query.data}', from={event.callback_query.from_user.id}")
        else:
            logger.info(f"[DEBUG] Other update type: {event.model_dump_json()[:500]}")
        result = await handler(event, data)
        return result

    # Запуск бота
    logger.info("Бот запущен и готов к работе!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        if webapp_runner:
            await webapp_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
