"""
Migration script to add photos and directions fields to businesses table
"""
import asyncio
from sqlalchemy import text
from database import engine, async_session_maker


async def add_fields():
    """Add photos and directions columns to businesses table"""
    async with engine.begin() as conn:
        # Add photos column
        await conn.execute(text(
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS photos TEXT"
        ))

        # Add directions column
        await conn.execute(text(
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS directions TEXT"
        ))

        print("Successfully added photos and directions columns to businesses table")


if __name__ == "__main__":
    asyncio.run(add_fields())
