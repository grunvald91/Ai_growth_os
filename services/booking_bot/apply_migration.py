"""
Скрипт для применения миграции новых полей предоплаты
"""
import asyncio
from sqlalchemy import text
from database.database import engine


async def apply_migration():
    """Применить миграцию для добавления полей предоплаты"""

    migrations = [
        # Поля для настройки предоплаты в бизнесе
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS prepayment_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS prepayment_percent INTEGER DEFAULT NULL",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_phone VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_details TEXT DEFAULT NULL",

        # Поля для хранения информации о предоплате в бронировании
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepayment_amount INTEGER DEFAULT NULL",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepayment_confirmed_at TIMESTAMP DEFAULT NULL",
    ]

    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
                print(f"OK: {sql[:60]}...")
            except Exception as e:
                print(f"SKIP (already exists or error): {sql[:40]}... - {e}")

    print("\nМиграция завершена!")


if __name__ == "__main__":
    asyncio.run(apply_migration())
