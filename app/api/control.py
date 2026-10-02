from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.control.contracts import ControlInboxItem, ControlStatus, ProductControlStatus
from app.db.session import get_db
from app.services.control import ControlService


router = APIRouter()


@router.get("/control/status", response_model=ControlStatus)
def control_status(db: Session = Depends(get_db)) -> ControlStatus:
    return ControlService(db).all_status()


@router.get(
    "/products/{product_id}/control/status",
    response_model=ProductControlStatus,
)
def product_control_status(
    product_id: UUID,
    db: Session = Depends(get_db),
) -> ProductControlStatus:
    try:
        return ControlService(db).product_status(product_id)
    except LookupError:
        raise HTTPException(404, "Product not found") from None


@router.get(
    "/products/{product_id}/control/inbox",
    response_model=list[ControlInboxItem],
)
def product_control_inbox(
    product_id: UUID,
    db: Session = Depends(get_db),
) -> list[ControlInboxItem]:
    try:
        return ControlService(db).inbox(product_id)
    except LookupError:
        raise HTTPException(404, "Product not found") from None
