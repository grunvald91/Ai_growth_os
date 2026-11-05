"""
Скрипт для обновления структуры базы данных
Добавляет новую таблицу employee_days_off
"""
import asyncio
from database import init_db, async_session_maker
from database.models import EmployeeDayOff

async def update_database():
    """Обновить структуру БД"""
    print("Обновление базы данных...")
    await init_db()
    print("✅ База данных обновлена!")
    print("   Добавлена таблица: employee_days_off")

if __name__ == "__main__":
    asyncio.run(update_database())
