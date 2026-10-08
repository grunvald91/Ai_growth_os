"""Credential-bearing ServGo adapter. Tokens never enter HTML, artifacts or prompts."""
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from app.core.config import get_settings


class ServGoAdapter:
    def __init__(self):
        settings = get_settings()
        self.base = (settings.servgo_api_base_url or "").rstrip("/")
        self.token = settings.servgo_platform_api_token or ""
        parsed = urlsplit(self.base)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not self.base or len(self.token) < 32 or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
            raise HTTPException(503, "Подключение ServGo не настроено администратором платформы")

    def request(self, method, path, data=None):
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                result = client.request(method, self.base + path,
                    headers={"Authorization": "Bearer " + self.token}, json=data)
            if result.status_code == 409:
                raise HTTPException(409, "Настройки изменены в ServGo. Обновите страницу")
            if not result.is_success:
                raise HTTPException(502, "ServGo отклонил запрос. Проверьте подключение и доступ к бизнесу")
            return result.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Не удалось связаться с ServGo") from None

    def businesses(self):
        return self.request("GET", "/platform/sales/businesses")

    def read(self, business_id):
        return self.request("GET", f"/platform/sales/businesses/{business_id}/settings")

    def update(self, business_id, data):
        return self.request("PUT", f"/platform/sales/businesses/{business_id}/settings", data)
