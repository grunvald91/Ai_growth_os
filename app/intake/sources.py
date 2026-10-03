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
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from app.intake.contracts import IntakeError, IntakeSource
from app.privacy.gateway import PrivacyGateway

MAX_FILE_BYTES = 20_000_000
FILE_HELP = "TXT, MD, JSON, CSV, DOCX, PDF, XLSX, XLS, JPG, PNG, WEBP, TIFF и BMP — до 20 МБ. PDF-сканы и изображения: распознавание русского и английского текста."
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
        values["note"] = (values.get("note", "") + " Материал прочитан частично: сохранены первые 12 000 символов. Пришлите нужный раздел отдельно.").strip()
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
                           note="Файл слишком большой; предел — 20 МБ.", **metadata)
    try:
        note, partial = "", False
        if suffix in {".txt", ".md", ".json", ".csv"}:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeError:
                    if len(data) < 2 or suffix == ".json":
                        raise
                    text = data.decode("cp1251")
                    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                        raise ValueError("Binary input")
                    note = "Прочитана кодировка Windows-1251; проверьте текст."
            if suffix == ".json":
                text = json.dumps(json.loads(text), ensure_ascii=False)
            elif suffix == ".csv":
                try:
                    dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                parts, length = [], 0
                for row in csv.reader(io.StringIO(text), dialect):
                    line = " | ".join(row)
                    parts.append(line)
                    length += len(line) + 1
                    if length > MAX_TEXT:
                        partial = True
                        break
                text = "\n".join(parts)
        elif suffix == ".docx":
            from app.intake.file_extract import office_archive, xml_root
            with office_archive(data) as archive:
                root = xml_root(archive.read("word/document.xml"))
            text = "\n".join(" ".join(n.text or "" for n in paragraph.iter() if n.tag.endswith("}t"))
                             for paragraph in root.iter() if paragraph.tag.endswith("}p"))
            # Also read minimal WordprocessingML exports without paragraphs.
            if not text:
                text = " ".join(n.text or "" for n in root.iter() if n.tag.endswith("}t"))
        elif suffix == ".pdf":
            from app.intake.file_extract import extract_pdf
            text, partial, note = extract_pdf(data)
        elif suffix in {".xlsx", ".xls"}:
            from app.intake.file_extract import extract_spreadsheet
            text, partial, note = extract_spreadsheet(suffix, data)
        elif suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}:
            from app.intake.file_extract import ocr_image
            text = ocr_image(data)
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                partial = getattr(image, "n_frames", 1) > 1
            note = "Текст распознан автоматически; проверьте названия, числа и цены."
            if partial:
                note += " Прочитан только первый кадр многостраничного изображения; пришлите остальные страницы отдельно."
        else:
            return make_source("owner_file", reference, status="unsupported",
                               note="Формат пока не читается. Пришлите " + FILE_HELP, **metadata)
        if not text.strip():
            return make_source("owner_file", reference, status="unavailable",
                               note="Читаемый текст не найден. Пришлите другой файл или напишите данные сообщением.", **metadata)
        if partial and not note:
            note = "Материал прочитан частично; пришлите нужный раздел отдельно."
        return make_source("owner_file", reference, text,
                           status="truncated" if partial else "read", note=note, **metadata)
    except Exception:
        # Parsers process untrusted files; never expose their error messages or file contents.
        return make_source("owner_file", reference, status="unsupported",
                           note="Не удалось безопасно прочитать файл. Возможно, он повреждён, защищён паролем или требует другого формата.", **metadata)


async def parse_file_async(filename: str, data: bytes, reference: str) -> IntakeSource:
    """Keep OCR, PDF decoding and decompression off the API/bot event loop.

    A killable child process bounds memory, CPU and wall time independently of DB sessions.
    """
    if len(data) > MAX_FILE_BYTES:
        return parse_file(filename, data, reference)
    def run():
        try:
            result = subprocess.run([sys.executable, "-m", "app.intake.file_extract", filename[:200], reference[:500]],
                                    input=data, capture_output=True, timeout=90, check=True)
            return IntakeSource.model_validate_json(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return make_source("owner_file", reference, filename=clean_text(PurePosixPath(filename).name)[:200],
                               sha256=hashlib.sha256(data).hexdigest(), status="unavailable",
                               note="Файл не удалось обработать за отведённое время или не хватает ресурсов. Пришлите нужный раздел отдельно.")
    return await asyncio.to_thread(run)


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
