from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tools import FakeToolGateway, ToolOperation, ToolRequest, ToolResult, ToolStatus


def request_data():
    return dict(operation=ToolOperation.RESEARCH, product_id=uuid4(),
                task_id=uuid4(), resource_id=uuid4())


@pytest.mark.parametrize("field", ["credentials", "api_key", "password", "kwargs", "payload", "approved"])
def test_credential_and_arbitrary_fields_rejected(field):
    with pytest.raises(ValidationError):
        ToolRequest(**request_data(), **{field: "not-accepted"})


def test_unknown_operation_and_non_uuid_references_rejected():
    for field, value in [("operation", "unknown"), ("resource_id", "password=not-accepted")]:
        data = request_data()
        data[field] = value
        with pytest.raises(ValidationError):
            ToolRequest(**data)


def test_request_and_result_frozen_and_result_extras_rejected():
    request = ToolRequest(**request_data())
    result = ToolResult(request=request, status=ToolStatus.EXECUTED)
    with pytest.raises(ValidationError):
        request.operation = ToolOperation.PUBLISH
    with pytest.raises(ValidationError):
        result.status = ToolStatus.APPROVAL_REQUIRED
    with pytest.raises(ValidationError):
        ToolResult(request=request, status=ToolStatus.EXECUTED, credentials={})


@pytest.mark.asyncio
async def test_fake_records_typed_request_without_external_actions():
    gateway = FakeToolGateway()
    request = ToolRequest(**request_data())
    result = await gateway.execute(request)
    assert result.status == ToolStatus.EXECUTED
    assert gateway.requests == [request]
    assert gateway.requests[0] is not request


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    ToolOperation.PUBLISH, ToolOperation.SEND_EMAIL, ToolOperation.MODIFY_WEBSITE,
    ToolOperation.LAUNCH_AD, ToolOperation.INCREASE_BUDGET, ToolOperation.CHANGE_PRICING,
    ToolOperation.CHANGE_BILLING, ToolOperation.CHANGE_CREDENTIALS,
    ToolOperation.DESTRUCTIVE_OPERATION,
])
async def test_direct_gateway_calls_cannot_bypass_approval(operation):
    gateway = FakeToolGateway()
    request = ToolRequest(**{**request_data(), "operation": operation})
    result = await gateway.execute(request)
    assert result.status == ToolStatus.APPROVAL_REQUIRED
    assert gateway.requests == []
