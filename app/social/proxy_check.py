"""Fixed-host outbound HTTPS proxy diagnostics without user-provided URLs or tokens."""
import asyncio
from urllib.parse import urlsplit
import httpx

TARGETS = (
    ("telegram", "https://api.telegram.org/"),
    ("instagram", "https://graph.instagram.com/"),
    ("threads", "https://graph.threads.net/"),
    ("youtube", "https://www.googleapis.com/"),
    ("google_oauth", "https://oauth2.googleapis.com/"),
)


async def verify_proxy(settings, *, client_factory=None):
    """Tests HTTPS CONNECT + TLS + HTTP on the API server. Not an OAuth capability test."""
    proxy = settings.comment_platform_proxy_url
    parsed = urlsplit(proxy or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"configured": False, "targets": [], "note": "Укажите серверный COMMENT_PLATFORM_PROXY_URL"}
    async def test_one(name, url):
        try:
            if client_factory:
                client = client_factory(proxy)
            else:
                client = httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False, timeout=8)
            async with client as http:
                response = await http.get(url)
                code = response.status_code
            # 401/404 can come from a real API host and still confirm TLS and HTTPS connectivity.
            return {"platform": name, "reachable": 200 <= code < 500 and code != 407,
                    "http_status": code}
        except (httpx.HTTPError, ValueError):
            return {"platform": name, "reachable": False, "http_status": None}
    results = await asyncio.gather(*(test_one(name, url) for name, url in TARGETS))
    return {"configured": True, "targets": results,
            "note": "Это проверка маршрута из API-контейнера, а не выдачи прав соцсетью. Worker проверяется отдельно."}
