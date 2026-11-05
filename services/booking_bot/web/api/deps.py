"""
Authentication dependencies for FastAPI.
"""

from datetime_utils import utc_now
import os
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, User, Business, BusinessOwner
from database.models import EmailIdentity, UserAccountMerge

SECRET_KEY = os.getenv("JWT_SECRET", "***REMOVED***")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30


def create_token(user_id: int, role: str = "client", *, email_version: int | None = None) -> str:
    """Create a JWT token for the given user."""
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            **({"ev": email_version} if email_version is not None else {}),
            "exp": utc_now() + timedelta(days=TOKEN_EXPIRE_DAYS),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


async def get_current_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode JWT and return the current User or raise 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    merged = await db.get(UserAccountMerge, user.id)
    if merged:
        user = await db.get(User, merged.target_user_id)
        if user is None:
            raise HTTPException(401, "User not found")
    if "ev" in payload:
        identity = await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == user.id))
        if identity is None or identity.session_version != payload["ev"]:
            raise HTTPException(401, "Session expired; log in again")
    return user


async def get_current_user_optional(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Like get_current_user but returns None instead of raising 401."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return await get_current_user(authorization, db)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


async def get_current_admin(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Ensure the current user owns at least one business (including co-ownership)."""
    result = await db.execute(
        select(Business).where(Business.owner_id == user.id).limit(1)
    )
    if result.scalar_one_or_none():
        return user
    # Check co-ownership via BusinessOwner junction table
    result2 = await db.execute(
        select(BusinessOwner).where(BusinessOwner.user_id == user.id).limit(1)
    )
    if result2.scalar_one_or_none():
        return user
    raise HTTPException(status_code=403, detail="Not a business owner")
