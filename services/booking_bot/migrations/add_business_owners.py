"""
Миграция: Добавление таблицы business_owners и полей version для оптимистичной блокировки
"""
import asyncio
import logging
from sqlalchemy import text
from database.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def migrate():
    """Выполнить миграцию"""
    logger.info("Запуск миграции: добавление business_owners и полей version...")

    async with engine.begin() as conn:
        # 1. Создаём таблицу business_owners
        logger.info("Создание таблицы business_owners...")
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS business_owners (
                    id SERIAL PRIMARY KEY,
                    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role VARCHAR(20) DEFAULT 'owner',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    added_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    CONSTRAINT uix_business_user UNIQUE (business_id, user_id)
                );
            """))
            logger.info("Таблица business_owners создана")
        except Exception as e:
            logger.warning(f"Таблица business_owners: {e}")

        # 2. Создаём индексы для business_owners
        try:
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_business_owners_business_id ON business_owners(business_id);
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_business_owners_user_id ON business_owners(user_id);
            """))
            logger.info("Индексы для business_owners созданы")
        except Exception as e:
            logger.warning(f"Индексы business_owners: {e}")

        # 3. Мигрируем существующих владельцев из Business.owner_id в business_owners
        logger.info("Миграция существующих владельцев...")
        try:
            await conn.execute(text("""
                INSERT INTO business_owners (business_id, user_id, role, added_at)
                SELECT id, owner_id, 'owner', created_at
                FROM businesses
                WHERE owner_id IS NOT NULL
                ON CONFLICT (business_id, user_id) DO NOTHING;
            """))
            logger.info("Существующие владельцы перенесены в business_owners")
        except Exception as e:
            logger.warning(f"Миграция владельцев: {e}")

        # 4. Добавляем поле version в таблицу businesses
        logger.info("Добавление поля version в businesses...")
        try:
            await conn.execute(text("""
                ALTER TABLE businesses ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
            """))
            logger.info("Поле version добавлено в businesses")
        except Exception as e:
            logger.warning(f"Поле version в businesses: {e}")

        # 5. Добавляем поле version в таблицу services
        logger.info("Добавление поля version в services...")
        try:
            await conn.execute(text("""
                ALTER TABLE services ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
            """))
            logger.info("Поле version добавлено в services")
        except Exception as e:
            logger.warning(f"Поле version в services: {e}")

        # 6. Добавляем поле version в таблицу bookings
        logger.info("Добавление поля version в bookings...")
        try:
            await conn.execute(text("""
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
            """))
            logger.info("Поле version добавлено в bookings")
        except Exception as e:
            logger.warning(f"Поле version в bookings: {e}")

    logger.info("Миграция завершена!")


async def rollback():
    """Откат миграции (опционально)"""
    logger.info("Откат миграции...")

    async with engine.begin() as conn:
        # Удаляем таблицу business_owners
        try:
            await conn.execute(text("DROP TABLE IF EXISTS business_owners;"))
            logger.info("Таблица business_owners удалена")
        except Exception as e:
            logger.warning(f"Удаление business_owners: {e}")

        # Удаляем поля version
        for table in ['businesses', 'services', 'bookings']:
            try:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS version;"))
                logger.info(f"Поле version удалено из {table}")
            except Exception as e:
                logger.warning(f"Удаление version из {table}: {e}")

    logger.info("Откат завершён!")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
