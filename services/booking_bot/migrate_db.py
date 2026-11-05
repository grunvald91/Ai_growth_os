"""
Скрипт миграции для добавления новых полей в БД
Запускайте этот скрипт после обновления моделей
"""
import asyncio
from sqlalchemy import text
from database import async_session_maker, engine


async def migrate():
    """Применить миграции"""
    print("Начало миграции базы данных...")

    async with engine.begin() as conn:
        # Проверяем и добавляем новые колонки в таблицу businesses
        print("Обновление таблицы businesses...")

        try:
            # Добавляем break_start
            await conn.execute(text(
                "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS break_start TIME"
            ))
            print("[OK] Добавлена колонка break_start")
        except Exception as e:
            print(f"  break_start: {e}")

        try:
            # Добавляем break_end
            await conn.execute(text(
                "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS break_end TIME"
            ))
            print("[OK] Добавлена колонка break_end")
        except Exception as e:
            print(f"  break_end: {e}")

        try:
            # Добавляем booking_interval_minutes
            await conn.execute(text(
                "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS booking_interval_minutes INTEGER DEFAULT 30"
            ))
            print("[OK] Добавлена колонка booking_interval_minutes")
        except Exception as e:
            print(f"  booking_interval_minutes: {e}")

        # Обновляем таблицу employees
        print("\nОбновление таблицы employees...")

        try:
            # Добавляем booking_interval_minutes
            await conn.execute(text(
                "ALTER TABLE employees ADD COLUMN IF NOT EXISTS booking_interval_minutes INTEGER"
            ))
            print("[OK] Добавлена колонка booking_interval_minutes")
        except Exception as e:
            print(f"  booking_interval_minutes: {e}")

    print("\n[SUCCESS] Миграция завершена успешно!")


if __name__ == "__main__":
    asyncio.run(migrate())
