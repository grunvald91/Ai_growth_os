"""Explicit Telegram Bot API transport configuration."""
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


def create_bot(token: str, proxy_url: str | None = None) -> Bot:
    """Use the private Bot API SOCKS tunnel when one is configured."""
    if proxy_url:
        return Bot(token=token, session=AiohttpSession(proxy=proxy_url))
    return Bot(token=token)
