"""Business/employee photos must use the web-only Telegram SOCKS transport."""
import ast
import os
from pathlib import Path
from unittest.mock import patch


def _handler(path: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def test_photo_client_routes_through_configured_web_proxy():
    from web.api.photo_transport import create_photo_client
    with patch.dict(os.environ, {"WEB_TELEGRAM_PROXY": "socks5://127.0.0.1:1089"}):
        with patch("web.api.photo_transport.httpx.AsyncClient") as constructor:
            create_photo_client()
    constructor.assert_called_once_with(proxy="socks5://127.0.0.1:1089", timeout=15)


def test_photo_client_without_proxy_preserves_direct_mode():
    from web.api.photo_transport import create_photo_client
    with patch.dict(os.environ, {}, clear=True):
        with patch("web.api.photo_transport.httpx.AsyncClient") as constructor:
            create_photo_client()
    constructor.assert_called_once_with(timeout=15)


def test_every_photo_handler_uses_web_photo_transport_and_no_literal_token():
    handlers = [
        ("web/api/routes/admin.py", "upload_business_photo", 2),
        ("web/api/routes/admin.py", "upload_employee_photo", 2),
        ("web/api/routes/client.py", "get_telegram_photo", 2),
    ]
    for path, name, required in handlers:
        node = _handler(path, name)
        calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
        factory_calls = [call for call in calls if isinstance(call.func, ast.Name) and call.func.id == "create_photo_client"]
        assert len(factory_calls) == required, name
        assert not any(isinstance(child, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "bot_token" for target in child.targets) and isinstance(child.value, ast.Constant) for child in ast.walk(node)), name
