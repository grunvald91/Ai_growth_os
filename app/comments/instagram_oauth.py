"""Instagram Unified Login (Meta business integration) and secure OAuth state.

Credentials are never returned to browsers. All Meta requests use the configured
foreign proxy without an implicit direct fallback.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx

from app.comments.gateway import PlatformError

MAX_STATE_AGE_SECONDS = 600
GRAPH_HOST = "https://graph.facebook.com"


def configured(settings) -> bool:
    return bool(settings.comment_meta_app_id and settings.comment_meta_app_secret
                and settings.comment_meta_login_config_id and settings.comment_meta_public_base
                and settings.comment_platform_proxy_url)


def valid_public_base(settings) -> str:
    base = (settings.comment_meta_public_base or "").rstrip("/")
    url = urlsplit(base)
    if (not url.hostname or url.username or url.password or url.query
            or url.fragment or url.path not in ("", "/") or url.scheme not in ("https", "http")):
        raise ValueError("Укажите публичный адрес Meta OAuth без пути и параметров")
    if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Meta OAuth требует HTTPS-адрес")
    return base


def redirect_uri(settings, product_id=None) -> str:
    # Single registered redirect for every company in the SaaS.
    return f"{valid_public_base(settings)}/studio/integrations/instagram/oauth/callback"


def state_product_id(state: str) -> str | None:
    """Read untrusted routing hint only; verify_state MUST validate its HMAC."""
    if len(state) > 1500:
        return None
    try:
        encoded = state.split(".", 1)[0]
        data = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        candidate = json.loads(data).get("p")
        return candidate if isinstance(candidate, str) and len(candidate) == 36 else None
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return None


def new_state(settings, product_id, login: str) -> tuple[str, str]:
    if not settings.comment_meta_app_secret:
        raise ValueError("Meta App Secret не настроен")
    nonce = secrets.token_urlsafe(32)
    data = json.dumps({"p": str(product_id), "l": login, "n": nonce,
                       "t": int(time.time())}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    signature = hmac.new(settings.comment_meta_app_secret.encode(),
                         encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", nonce


def verify_state(settings, state: str, nonce: str | None,
                 product_id, login: str) -> bool:
    if not settings.comment_meta_app_secret or not nonce or len(state) > 1500:
        return False
    encoded, separator, received = state.partition(".")
    if not separator or len(received) != 64:
        return False
    expected = hmac.new(settings.comment_meta_app_secret.encode(),
                        encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return False
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(decoded)
        age = int(time.time()) - value["t"]
        return (0 <= age <= MAX_STATE_AGE_SECONDS
                and value["p"] == str(product_id)
                and value["l"] == login
                and hmac.compare_digest(value["n"], nonce))
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return False


class MetaUnifiedClient:
    def __init__(self, settings, *, client=None):
        self.settings, self.client = settings, client
        self.version = settings.comment_meta_graph_version

    def authorization_url(self, uri: str, state: str) -> str:
        if not configured(self.settings):
            raise ValueError("Подключение Instagram через Meta пока не настроено")
        return "https://www.facebook.com/" + self.version + "/dialog/oauth?" + urlencode({
            "client_id": self.settings.comment_meta_app_id,
            "config_id": self.settings.comment_meta_login_config_id,
            "redirect_uri": uri,
            "response_type": "code",
            "override_default_response_type": "true",
            "state": state,
        })

    async def _request(self, method: str, path: str, *, params=None, data=None, token=None):
        if not self.settings.comment_platform_proxy_url:
            raise PlatformError("Не настроен зарубежный proxy для Meta")
        if not self.version.startswith("v") or not self.version[1:].replace(".", "").isdigit():
            raise ValueError("Неверная версия Meta Graph API")
        url = f"{GRAPH_HOST}/{self.version}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            if self.client:
                response = await self.client.request(method, url, params=params, data=data, headers=headers)
            else:
                async with httpx.AsyncClient(
                    proxy=self.settings.comment_platform_proxy_url,
                    timeout=20, follow_redirects=False, trust_env=False
                ) as client:
                    response = await client.request(method, url, params=params, data=data, headers=headers)
            if response.status_code in {401, 403}:
                raise PlatformError("Meta отклонила права доступа. Переподключите Instagram")
            if response.status_code >= 300:
                raise PlatformError("Meta отклонила OAuth или Graph API запрос")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                raise PlatformError("Meta вернула ошибку API")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, PlatformError):
                raise
            raise PlatformError("Не удалось безопасно подключиться к Meta") from None

    async def exchange_code(self, code: str, uri: str) -> str:
        if not code or len(code) > 2500 or not configured(self.settings):
            raise ValueError("Meta OAuth не настроен или код недействителен")
        # The Graph API expects the Meta app secret on the server, never in the UI.
        data = await self._request("GET", "oauth/access_token", params={
            "client_id": self.settings.comment_meta_app_id,
            "client_secret": self.settings.comment_meta_app_secret,
            "redirect_uri": uri, "code": code,
        })
        token = data.get("access_token")
        if not isinstance(token, str) or not (10 <= len(token) <= 8000):
            raise PlatformError("Meta не предоставила токен доступа")
        return token

    async def accounts(self, token: str) -> list[dict]:
        accounts, after = [], None
        seen = set()
        for _ in range(5):
            params = {"fields": "id,username", "limit": 100}
            if after:
                params["after"] = after
            page = await self._request("GET", "me/assigned_instagram_accounts",
                                       params=params, token=token)
            for item in page.get("data", []):
                identifier = str(item.get("id", ""))
                if identifier.isdigit() and identifier not in seen:
                    accounts.append({"id": identifier,
                                     "username": str(item.get("username") or identifier)[:200]})
                    seen.add(identifier)
            next_page = page.get("paging") or {}
            after = (next_page.get("cursors") or {}).get("after") if next_page.get("next") else None
            if not after:
                break
        return accounts

    async def verify_account(self, token: str, account_id: str) -> str:
        accounts = await self.accounts(token)
        name = next((item["username"] for item in accounts
                     if item["id"] == str(account_id)), None)
        if name is None:
            raise PlatformError("Этот Instagram-аккаунт не предоставлен приложению")
        # Verify Meta permits reading media and its comments, not just the
        # basic profile. Actual public REPLY permission still needs live E2E.
        await self._request("GET", f"{account_id}/media",
                            params={"fields": "id,comments.limit(1){id,text}",
                                    "limit": 1}, token=token)
        return name


def prune_expired_pending_oauth(session_factory, settings, *, current=None, limit=100):
    """Remove encrypted tokens of abandoned Meta authorizations.

    No LLM, external Meta call, or user-visible side effects. A background
    worker calls this periodically even when comment polling is disabled.
    """
    from sqlalchemy import select
    from app.comments.secrets import CommentSecrets
    from app.models.domain import Artifact, ArtifactStatus

    current = time.time() if current is None else current
    secrets_store = CommentSecrets(settings.products_root)
    removed = 0
    with session_factory() as db:
        pending = list(db.scalars(
            select(Artifact).where(
                Artifact.kind == "comment_instagram_oauth_pending",
                Artifact.status == ArtifactStatus.DRAFT,
            ).order_by(Artifact.created_at.asc(), Artifact.id.asc()).limit(limit)
        ))
        for artifact in pending:
            try:
                expires_at = float(json.loads(artifact.content)["expires_at"])
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                expires_at = 0
            if expires_at >= current:
                continue
            secrets_store.delete(artifact.id)
            artifact.status = ArtifactStatus.REJECTED
            removed += 1
        if removed:
            db.commit()
    return removed
