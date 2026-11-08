"""Regression checks for the paid SEO owner authorization boundaries."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.growth_live import _require_seo_owner_role
from app.dispatcher.outcome import Context
from app.dispatcher.executors import describe_seo_factory, confirm_seo_factory
from app.models.domain import Product


def request_for_role(role: str | None):
    request = Request({"type": "http", "method": "POST",
                       "path": "/studio/products/x/seo/queue", "headers": []})
    request.state.studio_sso_role = role
    return request


@pytest.mark.parametrize("role", ["viewer", "employee", "manager", "client"])
def test_non_owner_roles_cannot_trigger_paid_seo_controls(role):
    with pytest.raises(HTTPException) as error:
        _require_seo_owner_role(request_for_role(role))
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", [None, "owner", "co_owner"])
def test_legacy_owner_and_company_owners_have_existing_seo_permissions(role):
    _require_seo_owner_role(request_for_role(role))


@pytest.mark.asyncio
async def test_dispatcher_checks_seo_owner_at_description_and_confirmation():
    ctx = Context(
        db=None,
        product=Product(name="Company", slug="review-seo-roles", id=uuid4()),
        settings=SimpleNamespace(),
        account="staff",
        studio_role="viewer",
    )
    described = await describe_seo_factory(ctx, SimpleNamespace(horizon_days=7))
    confirmed = await confirm_seo_factory(ctx, {"horizon_days": 7}, "button")
    assert described.ok is False
    assert confirmed.ok is False
    assert "владелец" in described.text and "владелец" in confirmed.text
