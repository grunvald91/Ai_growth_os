"""Fixed-target proxy diagnostic tests: no arbitrary URL fetching or secrets."""
from types import SimpleNamespace
import pytest
import httpx
from app.social.proxy_check import verify_proxy


@pytest.mark.asyncio
async def test_proxy_check_uses_only_fixed_hosts():
    urls = []
    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url):
            urls.append(url)
            return SimpleNamespace(status_code=404)
    settings = SimpleNamespace(comment_platform_proxy_url="http://proxy.internal:8891")
    report = await verify_proxy(settings, client_factory=lambda proxy: FakeClient())
    assert report["configured"]
    assert len(report["targets"]) == 5
    assert all(row["reachable"] for row in report["targets"])
    assert all(url.startswith("https://") for url in urls)
    assert set(urls) == {
        "https://api.telegram.org/", "https://graph.instagram.com/",
        "https://graph.threads.net/", "https://www.googleapis.com/",
        "https://oauth2.googleapis.com/",
    }


@pytest.mark.asyncio
async def test_absent_proxy_does_not_make_any_request():
    report = await verify_proxy(SimpleNamespace(comment_platform_proxy_url=None),
                                client_factory=lambda proxy: (_ for _ in ()).throw(AssertionError()))
    assert report["configured"] is False
    assert report["targets"] == []


@pytest.mark.asyncio
async def test_proxy_auth_failure_is_not_passed_as_success():
    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url):
            return SimpleNamespace(status_code=407)
    settings = SimpleNamespace(comment_platform_proxy_url="http://proxy.internal:8891")
    report = await verify_proxy(settings, client_factory=lambda proxy: FakeClient())
    assert all(not row["reachable"] for row in report["targets"])
