"""
Миграция: добавление поля role в таблицу employees
Запустить один раз: python migrate_role.py
"""
import asyncio
from sqlalchemy import text
from database.database import engine

async def migrate():
    async with engine.begin() as conn:
        # Проверяем существует ли колонка
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='employees' AND column_name='role'
        """))
        exists = result.fetchone()

        if exists:
            print("Колонка 'role' уже существует")
        else:
            await conn.execute(text(
                "ALTER TABLE employees ADD COLUMN role VARCHAR(20) DEFAULT 'master'"
            ))
            print("Колонка 'role' успешно добавлена!")

        # Показать текущих сотрудников
        result = await conn.execute(text("SELECT id, name, role, telegram_id FROM employees"))
        employees = result.fetchall()
        print(f"\nТекущие сотрудники ({len(employees)}):")
        for e in employees:
            print(f"  ID: {e[0]}, Имя: {e[1]}, Роль: {e[2]}, Telegram: {e[3]}")

if __name__ == "__main__":
    asyncio.run(migrate())
