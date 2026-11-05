"""
Миграция: Добавление поля ai_instruction в таблицу businesses
"""
import asyncio
import logging
from sqlalchemy import text
from database.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def migrate():
    logger.info("Запуск миграции: добавление поля ai_instruction в businesses...")

    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ai_instruction TEXT DEFAULT NULL;"
            ))
            logger.info("Добавлено поле ai_instruction")
        except Exception as e:
            logger.warning(f"Поле ai_instruction: {e}")

    logger.info("Миграция завершена!")


if __name__ == "__main__":
    asyncio.run(migrate())
