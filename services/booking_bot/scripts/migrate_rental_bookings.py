"""Add nullable per-booking rental quote fields without changing existing bookings.

Run on the ServGo host from /opt/booking_bot with ./venv/bin/python.
"""
import asyncio
from sqlalchemy import text
from database.database import engine


async def main():
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE bookings ADD COLUMN IF NOT EXISTS duration_minutes INTEGER'))
        await conn.execute(text('ALTER TABLE bookings ADD COLUMN IF NOT EXISTS estimated_price INTEGER'))
        await conn.execute(text('ALTER TABLE bookings ADD COLUMN IF NOT EXISTS price_requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE'))
    print('rental_booking_columns_ready')


if __name__ == '__main__':
    asyncio.run(main())
