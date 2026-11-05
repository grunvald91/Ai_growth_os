"""
Миграция: Добавление полей подписки и тарифов в таблицу users
"""
import asyncio
import logging
from sqlalchemy import text
from database.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def migrate():
    """Выполнить миграцию"""
    logger.info("Запуск миграции: добавление полей подписки в users...")

    async with engine.begin() as conn:
        # Добавляем новые поля в таблицу users
        fields = [
            ("tariff", "VARCHAR(20)", "'standard'"),
            ("max_businesses", "INTEGER", "1"),
            ("subscription_status", "VARCHAR(20)", "'none'"),
            ("subscription_expires", "TIMESTAMP", "NULL"),
            ("is_manual_owner", "BOOLEAN", "FALSE"),
        ]

        for field_name, field_type, default in fields:
            try:
                # PostgreSQL синтаксис
                await conn.execute(text(f"""
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS {field_name} {field_type} DEFAULT {default};
                """))
                logger.info(f"Добавлено поле {field_name}")
            except Exception as e:
                logger.warning(f"Поле {field_name}: {e}")

        # Устанавливаем существующих владельцев (у кого уже есть бизнесы) как manual owners
        try:
            await conn.execute(text("""
                UPDATE users
                SET is_manual_owner = TRUE,
                    subscription_status = 'active',
                    max_businesses = 0
                WHERE id IN (
                    SELECT DISTINCT owner_id FROM businesses
                );
            """))
            logger.info("Существующие владельцы бизнесов помечены как manual_owner")
        except Exception as e:
            logger.warning(f"Ошибка при обновлении существующих владельцев: {e}")

    logger.info("Миграция завершена!")


if __name__ == "__main__":
    asyncio.run(migrate())
