"""Security contract for the opt-in canonical ServGo Growth gateway.

The gateway is not enabled by default and never accepts a cross-origin POST
merely because the Origin says 'servgo.ru'.
"""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.content_studio_auth import require_studio_csrf
from app.core.config import Settings


def request(*, host="servgo.ru", scheme="https", origin=None, referer=None, fetch_site=None, method="POST"):
    headers = [(b"host", host.encode("ascii"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if referer is not None:
        headers.append((b"referer", referer.encode("ascii")))
    if fetch_site is not None:
        headers.append((b"sec-fetch-site", fetch_site.encode("ascii")))
    return Request({
        "type": "http", "method": method, "scheme": scheme,
        "server": (host, 443), "path": "/studio/products/fake/create",
        "headers": headers, "query_string": b"",
    })


def settings(enabled=False):
    return Settings(
        app_env="production", studio_public_origin="https://growth.servgo.ru",
        studio_servgo_gateway_enabled=enabled, _env_file=None,
    )


def reject(req, config, status=403):
    with pytest.raises(HTTPException) as error:
        require_studio_csrf(req, config)
    assert error.value.status_code == status


def test_gateway_is_disabled_by_default():
    reject(request(origin="https://servgo.ru"), settings())
    assert require_studio_csrf(
        request(host="growth.servgo.ru", origin="https://growth.servgo.ru"), settings()
    ) is None


def test_canonical_same_origin_post_is_allowed_after_opt_in():
    config = settings(enabled=True)
    assert require_studio_csrf(request(origin="https://servgo.ru"), config) is None
    assert require_studio_csrf(request(
        origin="https://servgo.ru", fetch_site="same-origin"
    ), config) is None
    assert require_studio_csrf(request(
        origin=None, referer="https://servgo.ru/studio/products/fake", fetch_site="same-origin"
    ), config) is None


def test_old_growth_origin_keeps_working_on_old_host_during_rollout():
    config = settings(enabled=True)
    assert require_studio_csrf(
        request(host="growth.servgo.ru", origin="https://growth.servgo.ru"), config
    ) is None


@pytest.mark.parametrize("host,origin", [
    ("growth.servgo.ru", "https://servgo.ru"),
    ("servgo.ru", "https://growth.servgo.ru"),
    ("evil.example", "https://servgo.ru"),
    ("servgo.ru", "http://servgo.ru"),
    ("servgo.ru", "https://evil.example"),
    ("servgo.ru", "https://servgo.ru.evil.example"),
    ("servgo.ru", "https://servgo.ru:444"),
])
def test_cross_host_or_forged_origin_is_rejected_even_if_same_site(host, origin):
    reject(request(host=host, origin=origin, fetch_site="same-site"), settings(enabled=True))


def test_missing_origin_and_cross_site_rejected_even_through_same_origin_gateway():
    reject(request(origin=None), settings(enabled=True))
    reject(request(origin="https://servgo.ru", fetch_site="cross-site"), settings(enabled=True))
    reject(request(scheme="http", origin="https://servgo.ru"), settings(enabled=True))
    assert require_studio_csrf(request(origin=None, method="GET"), settings(enabled=True)) is None


def test_bad_or_missing_legacy_origin_fails_closed():
    invalid = Settings(
        app_env="production", studio_public_origin=None,
        studio_servgo_gateway_enabled=True, _env_file=None,
    )
    reject(request(origin="https://servgo.ru"), invalid, 503)
