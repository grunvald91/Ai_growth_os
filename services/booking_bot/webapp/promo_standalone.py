"""Independent HTTP runtime for promo.servgo.ru.

Reuse the existing application and its landing/lead routes without starting
Telegram polling or the reminder scheduler. This process binds ONLY to loopback;
nginx is the sole public entry point.
"""
from __future__ import annotations

import os
from aiohttp import web

from webapp.server import create_webapp


def make_app() -> web.Application:
    app = create_webapp()

    async def ready(_request: web.Request) -> web.Response:
        return web.Response(text="ok", headers={"Cache-Control": "no-store"})

    # The legacy catch-all route does not match names starting with '_'.
    app.router.add_get("/_promo_health", ready)
    return app


def main() -> None:
    host = os.getenv("PROMO_WEBAPP_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "::1"):
        raise RuntimeError("The promo HTTP server must only bind to loopback")
    port = int(os.getenv("PROMO_WEBAPP_PORT", "18080"))
    if not (1024 <= port <= 65535):
        raise ValueError("Invalid promo HTTP port")
    web.run_app(make_app(), host=host, port=port, access_log=None)


if __name__ == "__main__":
    main()
