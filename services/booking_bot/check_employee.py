"""Скрипт для проверки, есть ли сотрудник с указанным telegram_id"""
import asyncio
from database import async_session_maker
from database.crud import get_employees_by_telegram_id

TELEGRAM_ID = 437862772

async def check():
    async with async_session_maker() as session:
        employees = await get_employees_by_telegram_id(session, TELEGRAM_ID)

        if not employees:
            print(f"❌ Сотрудников с telegram_id={TELEGRAM_ID} не найдено")
            return

        print(f"✅ Найдено сотрудников: {len(employees)}\n")
        for emp in employees:
            print(f"  ID: {emp.id}")
            print(f"  Имя: {emp.name}")
            print(f"  Роль: {emp.role}")
            print(f"  Бизнес ID: {emp.business_id}")
            print(f"  Бизнес: {emp.business.name if emp.business else 'N/A'}")
            print(f"  Активен: {emp.is_active}")
            print()

if __name__ == "__main__":
    asyncio.run(check())
