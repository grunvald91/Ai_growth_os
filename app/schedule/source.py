"""Where the dispatcher reads the booking schedule from.

The interface is small on purpose, so another booking system can be added later. The ServGo implementation
asks the read-only internal method of the booking platform. It never writes, and any failure means "no data",
never a guess.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Product
from app.sso import access
from app.sso.models import StudioBusinessLink

_logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 10.0  # free times for a week of several masters take longer than a yes/no check


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Booking(_Row):
    id: int
    date: date
    time: str
    duration_minutes: int | None = None
    status: str
    service: str
    resource: str | None = None
    client_name: str
    client_phone: str | None = None
    client_email: str | None = None
    price: int | None = None
    # Add-ons booked with a main service do not take the master's or hall's time.
    occupies_resource: bool = True


class Resource(_Row):
    id: int
    name: str


class FreeSlots(_Row):
    date: date
    resource_id: int | None = None
    resource: str | None = None
    times: list[str] = Field(default_factory=list)


class Schedule(_Row):
    today: date
    working_hours: str | None = None
    break_hours: str | None = None
    step_minutes: int | None = None
    resources: list[Resource] = Field(default_factory=list)
    bookings: list[Booking] = Field(default_factory=list)
    free_slots: list[FreeSlots] = Field(default_factory=list)


class ScheduleSource(Protocol):
    business_label: str | None

    def fetch(self, date_from: date, date_to: date, *, with_slots: bool = False) -> Schedule | None:
        """The schedule for the period, or None when it cannot be read right now."""


class ServGoSchedule:
    def __init__(self, settings, business_id: int, user_id: int | None = None, business_label: str | None = None):
        self._settings = settings
        self._business_id = business_id
        self._user_id = user_id
        self.business_label = business_label

    def fetch(self, date_from: date, date_to: date, *, with_slots: bool = False) -> Schedule | None:
        key = self._settings.servgo_internal_key or ""
        if len(key) < 32:
            return None
        params: dict[str, str | int | bool] = {"business_id": self._business_id, "date_from": date_from.isoformat(),
                                               "date_to": date_to.isoformat(), "with_slots": with_slots}
        if self._user_id is not None:
            params["user_id"] = self._user_id
        url = self._settings.servgo_api_url.rstrip("/") + "/internal/studio/schedule"
        try:
            with access.http_client(TIMEOUT_SECONDS) as client:
                response = client.get(url, params=params, headers={"X-Studio-Key": key})
            if response.status_code != 200:
                _logger.warning("studio_schedule_failed status=%s", response.status_code)
                return None
            return Schedule.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            _logger.warning("studio_schedule_failed %s", type(exc).__name__)
            return None


def linked_business_ids(db: Session, product_id) -> list[int]:
    return list(db.scalars(select(StudioBusinessLink.servgo_business_id).where(
        StudioBusinessLink.product_id == product_id).order_by(StudioBusinessLink.servgo_business_id)))


def source_for(db: Session, settings, product: Product, account: str) -> ScheduleSource | None:
    """The schedule of the booking business linked to this company, or None when there is no such link.

    A person who signed in from ServGo is re-checked there on every request; a person signed in with a studio
    login has no ServGo identity, and the link set by the studio operator decides.
    """
    if len(settings.servgo_internal_key or "") < 32:
        return None
    ids = linked_business_ids(db, product.id)
    if not ids:
        return None
    user_id = None
    prefix, _, rest = account.partition(":")
    if prefix == "servgo" and rest.isdigit():
        user_id = int(rest)
    label = f"заведение № {ids[0]}" if len(ids) > 1 else None
    return ServGoSchedule(settings, ids[0], user_id, label)
