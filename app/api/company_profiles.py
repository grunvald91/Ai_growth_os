from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.company_profile import load_company_profile, profile_snapshot
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Product

router = APIRouter()


class ReadinessGateRead(BaseModel):
    ready: bool
    missing_fields: list[str]
    warnings: list[str]


class CompanyProfileRead(BaseModel):
    profile: dict | None
    source: Literal["company-profile.json", "legacy_markdown", "missing"]
    readiness: dict[str, ReadinessGateRead]
    validation_errors: list[str]


@router.get("/products/{product_id}/company-profile", response_model=CompanyProfileRead)
def company_profile(product_id: UUID, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    return profile_snapshot(load_company_profile(get_settings().products_root, product.slug))
