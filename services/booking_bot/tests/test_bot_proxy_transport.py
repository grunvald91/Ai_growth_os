"""Bot API transport must use a SOCKS proxy only when explicitly configured."""
from aiogram.client.session.aiohttp import AiohttpSession

from bot.telegram_transport import create_bot


TOKEN = "123456:ABCdefGHIjklMNOpqrSTUvwxYZ"


def test_configured_proxy_is_used_for_bot_api_requests():
    bot = create_bot(TOKEN, "socks5://127.0.0.1:1089")
    assert isinstance(bot.session, AiohttpSession)
    assert bot.session.proxy == "socks5://127.0.0.1:1089"


def test_no_proxy_preserves_default_aiogram_transport():
    bot = create_bot(TOKEN, None)
    assert isinstance(bot.session, AiohttpSession)
    assert bot.session.proxy is None
