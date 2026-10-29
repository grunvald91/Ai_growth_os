import asyncio
import httpx
import pytest
from app.seo.suggest import collect


@pytest.mark.asyncio
async def test_request_budget_and_both_engines():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=["query", ["real suggestion"]])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await collect(["запись клиентов"], client=client, max_requests=7)
    assert len(requests) == 7
    assert all(r.requests > 0 for r in result.values())


@pytest.mark.asyncio
async def test_total_deadline_cancels_slow_requests():
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await asyncio.wait_for(collect(["запись клиентов"], client=client,
                                               total_timeout=0.01), 1)
    assert not any(r.queries for r in result.values())


@pytest.mark.asyncio
async def test_saved_batches_are_not_requested_twice():
    snapshots = []
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=["query", ["real suggestion"]])
    def save(results):
        snapshots.append({engine: {"requests": value.requests, "failed": value.failed,
            "queries": list(value.queries), "completed": list(value.completed)}
            for engine, value in results.items()})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await collect(["запись клиентов"], client=client, max_requests=8, on_batch=save)
        assert len(calls) == 8
        restored = await collect(["запись клиентов"], client=client, max_requests=8, initial=snapshots[-1])
    assert len(calls) == 8
    assert sum(value.requests for value in restored.values()) == 8
