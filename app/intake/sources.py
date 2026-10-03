"""Bounded parsers and a public-page adapter with pinned DNS and no redirects."""
import asyncio
import csv
import hashlib
import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from app.intake.contracts import IntakeError, IntakeSource
from app.privacy.gateway import PrivacyGateway

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TEXT = 12_000
MAX_WEB_BYTES = 1024 * 1024


def clean_text(text: str) -> str:
    safe = PrivacyGateway().sanitize_text(text)
    safe = re.sub(r"https?://[^/\s:@]+:[^/\s@]+@", "https://[REDACTED]@", safe)
    safe = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[CONTACT_EMAIL]", safe)
    safe = re.sub(r"(?<!\w)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)",
                  "[CONTACT_PHONE]", safe)
    return safe


def make_source(kind, reference, text="", **values) -> IntakeSource:
    safe = clean_text(text)
    status = values.pop("status", "read")
    if len(safe) > MAX_TEXT:
        status = "truncated"
    reference = clean_text(str(reference))[:500]
    digest = hashlib.sha256((kind + reference + safe).encode()).hexdigest()
    return IntakeSource(id=digest[:24], source_type=kind, reference=reference,
                        text=safe[:MAX_TEXT], status=status, **values)


def parse_file(filename: str, data: bytes, reference: str) -> IntakeSource:
    # Metadata is preserved even for unreadable formats; no contents are guessed.
    suffix = PurePosixPath(filename).suffix.casefold()
    metadata = {"filename": clean_text(PurePosixPath(filename).name)[:200],
                "sha256": hashlib.sha256(data).hexdigest()}
    if len(data) > MAX_FILE_BYTES:
        return make_source("owner_file", reference, status="unsupported",
                           note="Файл слишком большой; предел — 2 МиБ.", **metadata)
    try:
        if suffix in {".txt", ".md", ".json", ".csv"}:
            text = data.decode("utf-8-sig")
            if suffix == ".json":
                text = json.dumps(json.loads(text), ensure_ascii=False)
            elif suffix == ".csv":
                rows = csv.reader(io.StringIO(text))
                text = "\n".join(" | ".join(row) for _, row in zip(range(1000), rows))
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 500 or any("vba" in i.filename.casefold() for i in infos):
                    raise IntakeError("Unsafe office container")
                if sum(i.file_size for i in infos) > 8 * MAX_FILE_BYTES:
                    raise IntakeError("Oversized office extraction")
                xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise IntakeError("Unsafe XML")
            root = ElementTree.fromstring(xml)
            text = " ".join(node.text or "" for node in root.iter()
                            if node.tag.endswith("}t"))
        else:
            return make_source("owner_file", reference, status="unsupported",
                               note="Формат пока не читается. Пришлите TXT, MD, JSON, CSV или DOCX.",
                               **metadata)
        return make_source("owner_file", reference, text, **metadata)
    except (ValueError, UnicodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError,
            RecursionError, csv.Error):
        return make_source("owner_file", reference, status="unsupported",
                           note="Не удалось безопасно прочитать содержимое файла.", **metadata)


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def public_target(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise IntakeError("Требуется публичный адрес http/https без учётных данных.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise IntakeError("Нестандартные порты не поддерживаются.")
    answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    addresses = {item[4][0] for item in answers}
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise IntakeError("Локальные и частные адреса недоступны.")
    return parsed, port, sorted(addresses)[0]


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PublicWebsiteFetcher:
    async def fetch(self, url: str) -> IntakeSource:
        return await asyncio.to_thread(self._fetch, url)

    def _fetch(self, url: str) -> IntakeSource:
        connection = None
        try:
            parsed, port, address = public_target(url)
            connection = (_PinnedTLS(parsed.hostname, port, address) if parsed.scheme == "https"
                          else http.client.HTTPConnection(address, port, timeout=15))
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request("GET", path, headers={"Host": parsed.hostname,
                               "User-Agent": "AI-Growth-OS-Company-Intake/1.0",
                               "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise IntakeError("Страница недоступна; перенаправления не выполняются.")
            content_type = response.getheader("Content-Type", "").casefold()
            if not any(item in content_type for item in ("text/html", "text/plain")):
                raise IntakeError("Тип страницы не поддерживается.")
            raw = response.read(MAX_WEB_BYTES + 1)
            if len(raw) > MAX_WEB_BYTES:
                raise IntakeError("Страница слишком большая.")
            text = raw.decode("utf-8", errors="replace")
            if "text/html" in content_type:
                parser = _VisibleHTML()
                parser.feed(text)
                text = " ".join(parser.parts)
            return make_source("owned_website", url, text)
        except (OSError, ValueError, http.client.HTTPException):
            return make_source("owned_website", url, status="unavailable",
                               note="Не удалось безопасно прочитать страницу. Можно прислать её текст.")
        finally:
            if connection is not None:
                connection.close()
