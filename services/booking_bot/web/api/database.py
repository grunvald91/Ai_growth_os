"""
Database session factory.
Reuses the same SQLAlchemy models as the Telegram bot.
"""
import sys
import os

# Add project root so we can import database.models
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Re-export models for convenient access
from database.models import (  # noqa: F401
    Base, User, Business, Service, Employee, Booking, VenueTimePricing,
    BusinessOwner, Subscription, SubscriptionService, ClientSubscription, SubscriptionUsage,
    EmployeeService, EmployeeDayOff, PushSubscription,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost/booking_bot",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency that yields an async DB session."""
    async with async_session() as session:
        yield session
