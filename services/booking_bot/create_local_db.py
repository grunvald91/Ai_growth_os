#!/usr/bin/env python3
"""Create the local development database if it is missing."""
import asyncio
import os

import asyncpg

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


async def create_database():
    try:
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = int(os.getenv("DB_PORT", "5432"))
        db_user = os.getenv("DB_USER", "postgres")
        db_password = os.getenv("DB_PASSWORD")
        dev_database = os.getenv("BOOKING_DEV_DB_NAME", "booking_bot_dev")

        if not db_password:
            raise RuntimeError("DB_PASSWORD is required in .env or environment variables.")

        conn = await asyncpg.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database="postgres",
        )

        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            dev_database,
        )

        if exists:
            print(f"Database '{dev_database}' already exists")
        else:
            await conn.execute(f'CREATE DATABASE "{dev_database}"')
            print(f"Database '{dev_database}' created successfully")

        await conn.close()
    except Exception as exc:
        print(f"Failed to create local database: {exc}")
        print("\nCheck:")
        print("1. PostgreSQL is running")
        print("2. DB_PASSWORD is set in .env or the shell")
        print("3. The postgres user exists")


if __name__ == "__main__":
    asyncio.run(create_database())
