"""
Тестовый скрипт для проверки подключения к БД
"""
import asyncio
from database import init_db

async def main():
    print("Проверка подключения к базе данных...")
    try:
        await init_db()
        print("✅ Подключение успешно! Таблицы созданы.")
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")

if __name__ == "__main__":
    asyncio.run(main())
