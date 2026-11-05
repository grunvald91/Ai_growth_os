"""
Migration script to add reminder_24h_sent and reminder_2h_sent fields to bookings table
"""
import asyncio
from sqlalchemy import text
from database import engine


async def add_fields():
    """Add reminder tracking columns to bookings table"""
    async with engine.begin() as conn:
        # Add reminder_24h_sent column
        await conn.execute(text(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN DEFAULT FALSE"
        ))

        # Add reminder_2h_sent column
        await conn.execute(text(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_2h_sent BOOLEAN DEFAULT FALSE"
        ))

        print("Successfully added reminder_24h_sent and reminder_2h_sent columns to bookings table")


if __name__ == "__main__":
    asyncio.run(add_fields())
