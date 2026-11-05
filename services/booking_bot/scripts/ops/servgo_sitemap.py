#!/usr/bin/env python3
"""Генерация sitemap.xml для servgo.ru из живого списка бизнесов.

Запускается на сервере по cron (см. .github/workflows/servgo-sitemap-install.yml):
берёт активные бизнесы из локального API и атомарно перезаписывает
web/frontend/dist/sitemap.xml, который nginx отдаёт как статику.

Только стандартная библиотека — запускается системным python3.
Если API недоступен или ответ странный, существующий sitemap не трогаем.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import escape

SITE_URL = "https://servgo.ru"
DEFAULT_API_URL = "http://127.0.0.1:8000/businesses"
DEFAULT_OUT = "/opt/booking_bot/web/frontend/dist/sitemap.xml"


def fetch_business_ids(api_url: str, timeout: float = 15.0) -> list[int]:
    with urllib.request.urlopen(api_url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("API returned non-list payload")
    ids = sorted({int(item["id"]) for item in data if isinstance(item, dict) and str(item.get("id", "")).isdigit()})
    return ids


def build_sitemap(business_ids: list[int], today: str) -> str:
    rows = [(f"{SITE_URL}/", "daily", "1.0")]
    rows += [(f"{SITE_URL}/business/{bid}", "weekly", "0.8") for bid in business_ids]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, prio in rows:
        parts.append(
            f"  <url><loc>{escape(loc)}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        )
    parts.append("</urlset>")
    return "\n".join(parts) + "\n"


def write_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".sitemap.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc)
    try:
        ids = fetch_business_ids(args.api_url)
    except Exception as exc:  # noqa: BLE001 — любой сбой: оставляем прежний файл
        print(f"{stamp:%Y-%m-%dT%H:%M:%SZ} sitemap: API error, keeping previous file: {exc}", file=sys.stderr)
        return 1
    write_atomic(args.out, build_sitemap(ids, stamp.strftime("%Y-%m-%d")))
    print(f"{stamp:%Y-%m-%dT%H:%M:%SZ} sitemap: written {len(ids) + 1} urls to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
