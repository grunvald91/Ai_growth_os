"""
Подключение к базе данных и создание сессий
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from bot.config import DATABASE_URL
from database.models import Base


# Создаем асинхронный движок
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Установите в True для отладки SQL запросов
    poolclass=NullPool,  # Отключаем пул соединений для простоты
)

# Создаем фабрику сессий
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    """
    Инициализация базы данных
    Создает все таблицы, если их нет
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Миграции для существующей БД (idempotent)
    # ВАЖНО: каждая миграция в отдельной транзакции,
    # т.к. в PostgreSQL ошибка в ALTER TABLE ломает всю транзакцию
    from sqlalchemy import text

    migrations = [
        "ALTER TABLE services ADD COLUMN is_additional BOOLEAN DEFAULT FALSE",
        "ALTER TABLE services ADD COLUMN is_hourly_price BOOLEAN DEFAULT FALSE",
        "ALTER TABLE employees ADD COLUMN capacity INTEGER",
        "ALTER TABLE employees ADD COLUMN area_sqm INTEGER",
        "ALTER TABLE employees ADD COLUMN description TEXT",
        "ALTER TABLE employees ADD COLUMN min_booking_minutes INTEGER",
        "ALTER TABLE venue_time_pricing ADD COLUMN day_type VARCHAR(20) DEFAULT 'all'",
        "ALTER TABLE employees ADD COLUMN solo_price_per_hour INTEGER",
        "ALTER TABLE services ADD COLUMN available_employee_ids TEXT",
        "ALTER TABLE services ADD COLUMN tariff_description TEXT",
        "ALTER TABLE employees ADD COLUMN photos TEXT",
        "ALTER TABLE employees ADD COLUMN video TEXT",
        "ALTER TABLE businesses ADD COLUMN video TEXT",
        "ALTER TABLE businesses ADD COLUMN cancellation_policy_photo TEXT",
        "ALTER TABLE businesses ADD COLUMN require_receipt_for_prepayment BOOLEAN DEFAULT FALSE",
        "ALTER TABLE services ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE bookings ADD COLUMN notify_token VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_bookings_notify_token ON bookings (notify_token)",
        "ALTER TABLE employees ADD COLUMN access_email VARCHAR(320)",
        "CREATE INDEX IF NOT EXISTS ix_employees_access_email ON employees (access_email)",
        "ALTER TABLE bookings ADD COLUMN finished_at TIMESTAMP",
    ]

    for migration in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(migration))
        except Exception:
            pass  # Столбец уже существует


async def get_session() -> AsyncSession:
    """
    Получить сессию базы данных
    """
    async with async_session_maker() as session:
        yield session
