import base64
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.intake.contracts import IntakeError
from app.intake.service import CompanyIntakeService
from app.intake.sources import make_source, parse_file_async, MAX_FILE_BYTES

router = APIRouter()
ACTOR = "local_owner"


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "link", "identifier", "file"]
    text: str = Field(default="", max_length=12000)
    reference: str = Field(default="owner input", max_length=500)
    filename: str | None = Field(default=None, max_length=200)
    content_base64: str | None = Field(default=None, max_length=4 * ((MAX_FILE_BYTES + 2) // 3))


class UnknownAnswer(BaseModel):
    field_path: str


class ApplyInput(BaseModel):
    review_id: UUID
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError("Explicit true is required")
        return value


def get_intake(db: Session = Depends(get_db)):
    settings = get_settings()
    return CompanyIntakeService(db, products_root=settings.products_root, model=settings.llm_model,
                                provider_name=settings.llm_provider)


def intake_error(exc):
    if isinstance(exc, LookupError):
        raise HTTPException(404, "Company or intake session not found") from None
    raise HTTPException(409, str(exc)) from None


@router.post("/products/{product_id}/intake")
def start_intake(product_id: UUID, service=Depends(get_intake)):
    try:
        return service.start(product_id, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)


@router.get("/products/{product_id}/intake/{session_id}")
def get_session(product_id: UUID, session_id: UUID, service=Depends(get_intake)):
    try:
        return service.get(product_id, session_id, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)


@router.post("/products/{product_id}/intake/{session_id}/sources")
async def add_source(product_id: UUID, session_id: UUID, body: SourceInput, service=Depends(get_intake)):
    try:
        if body.kind == "text":
            return service.add_text(product_id, session_id, body.text, actor=ACTOR, reference=body.reference)
        if body.kind == "link":
            return await service.add_link(product_id, session_id, body.text, actor=ACTOR)
        if body.kind == "identifier":
            return await service.add_identifier(product_id, session_id, body.text, actor=ACTOR)
        if not body.filename:
            raise IntakeError("Укажите имя файла.")
        if body.content_base64 is None:
            return service.add_source(product_id, session_id,
                make_source("owner_file", body.reference, status="unsupported", filename=body.filename,
                            note="Содержимое файла не передано."), actor=ACTOR)
        try:
            raw = base64.b64decode(body.content_base64, validate=True)
        except ValueError:
            raise IntakeError("Не удалось прочитать данные файла.") from None
        source = await parse_file_async(body.filename, raw, body.reference)
        return service.add_source(product_id, session_id, source, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)


@router.post("/products/{product_id}/intake/{session_id}/draft")
async def build_draft(product_id: UUID, session_id: UUID, service=Depends(get_intake)):
    try:
        # Credentials remain inside the existing provider adapter; no model switch.
        if service.llm is not None:
            configured = service
        else:
            from app.core.composition import build_intake
            configured = build_intake(service.db, get_settings())
        return await configured.build(product_id, session_id, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)
    except ValueError:
        raise HTTPException(503, "Intake model is not configured") from None


@router.post("/products/{product_id}/intake/{session_id}/unknown")
def unknown_answer(product_id: UUID, session_id: UUID, body: UnknownAnswer, service=Depends(get_intake)):
    try:
        return service.answer_unknown(product_id, session_id, body.field_path, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)


@router.post("/products/{product_id}/intake/{session_id}/approve")
def approve_profile(product_id: UUID, session_id: UUID, body: ApplyInput, service=Depends(get_intake)):
    try:
        return service.approve(product_id, session_id, body.review_id, actor=ACTOR, confirmed=body.confirmed)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)


@router.post("/products/{product_id}/intake/{session_id}/cancel")
def cancel_intake(product_id: UUID, session_id: UUID, service=Depends(get_intake)):
    try:
        return service.cancel(product_id, session_id, actor=ACTOR)
    except (IntakeError, LookupError) as exc:
        intake_error(exc)
