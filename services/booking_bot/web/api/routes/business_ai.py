"""Owner-scoped, provider-agnostic facts for ServGo Business AI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from ..business_ai import build_business_agent_brief
from ..database import get_db
from ..deps import get_current_admin
from .admin import analytics_overview

router = APIRouter()


@router.get("/analytics/{business_id}/agent-brief")
async def business_agent_brief(
    business_id: int,
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Only existing business owners can read the verified analytics."""
    overview = await analytics_overview(business_id=business_id, days=days, user=user, db=db)
    return build_business_agent_brief(business_id=business_id, overview=overview)
