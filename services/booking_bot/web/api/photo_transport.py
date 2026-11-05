"""Isolated Telegram media HTTP client for the ServGo web API."""
import os

import httpx


def create_photo_client() -> httpx.AsyncClient:
    """Use the private relay only when explicitly enabled for web photo traffic."""
    proxy_url = os.getenv("WEB_TELEGRAM_PROXY")
    if proxy_url:
        return httpx.AsyncClient(proxy=proxy_url, timeout=15)
    return httpx.AsyncClient(timeout=15)
