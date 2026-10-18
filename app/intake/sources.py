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
from urllib.parse import urljoin, urlsplit
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
            result = subprocess.run([sys.executable, "-m", "app.intake.file_extract", filename, reference[:500]],
                                    input=data, capture_output=True, timeout=90, check=True)
            return IntakeSource.model_validate_json(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return make_source("owner_file", reference, filename=clean_text(PurePosixPath(filename).name)[:200],
                               sha256=hashlib.sha256(data).hexdigest(), status="unavailable",
                               note="Файл не удалось обработать за отведённое время или не хватает ресурсов. Пришлите нужный раздел отдельно.")
    return await asyncio.to_thread(run)


class _VisibleHTML(HTMLParser):
    """Visible text plus the parts of a page that describe the business to machines.

    Links, JSON-LD and meta descriptions are collected so a crawl can find service,
    price and contact pages and still learn something from script-rendered sites.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.links: list[tuple[str, str]] = []
        self.meta: list[str] = []
        self.json_ld: list[str] = []
        self.title = ""
        self._in_title = False
        self._in_json_ld = False
        self._json_buffer: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self.script_count = 0

    def handle_starttag(self, tag, attrs):
        values = {key.casefold(): (value or "") for key, value in attrs}
        if tag == "script":
            self.script_count += 1
            if values.get("type", "").casefold() == "application/ld+json":
                self._in_json_ld = True
                self._json_buffer = []
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (values.get("name") or values.get("property") or "").casefold()
            if key in {"description", "og:title", "og:description", "og:site_name", "keywords"} and values.get("content"):
                self.meta.append(values["content"].strip())
        elif tag == "a" and values.get("href"):
            self._link_href, self._link_text = values["href"], []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._json_buffer))
        if tag in {"script", "style", "noscript", "template"} and self.hidden:
            self.hidden -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link_href is not None:
            self.links.append((self._link_href, " ".join("".join(self._link_text).split())))
            self._link_href = None

    def handle_data(self, text):
        if self._in_json_ld:
            self._json_buffer.append(text)
            return
        if self._in_title:
            self.title += text
        if not self.hidden:
            self.parts.append(text)
            if self._link_href is not None:
                self._link_text.append(text)

    def visible_text(self) -> str:
        return " ".join(" ".join(self.parts).split())


# Values worth keeping from schema.org markup; everything else is noise for a profile.
_JSON_LD_KEYS = {"@type", "name", "legalName", "alternateName", "description", "slogan", "telephone",
                 "email", "address", "streetAddress", "addressLocality", "addressRegion", "addressCountry",
                 "areaServed", "priceRange", "openingHours", "openingHoursSpecification", "dayOfWeek",
                 "opens", "closes", "makesOffer", "hasOfferCatalog", "itemListElement", "itemOffered",
                 "offers", "price", "priceCurrency", "aggregateRating", "ratingValue", "reviewCount",
                 "foundingDate", "knowsLanguage", "serviceType", "category", "brand"}


def _json_ld_lines(raw_blocks: list[str], limit: int = 3000) -> list[str]:
    lines: list[str] = []

    def walk(value, prefix=""):
        if sum(len(line) for line in lines) > limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _JSON_LD_KEYS:
                    walk(item, f"{prefix}{key}." if isinstance(item, (dict, list)) else f"{prefix}{key}")
        elif isinstance(value, list):
            for item in value[:30]:
                walk(item, prefix)
        elif isinstance(value, (str, int, float)) and str(value).strip():
            lines.append(f"{prefix.rstrip('.')}: {' '.join(str(value).split())[:300]}")

    for block in raw_blocks[:10]:
        try:
            walk(json.loads(block))
        except (ValueError, RecursionError):
            continue
    return lines


_SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".rar", ".doc",
                  ".docx", ".xls", ".xlsx", ".mp4", ".mov", ".mp3", ".css", ".js", ".xml", ".json")
# A page that names services, prices, the company, contacts or reviews tells the profile the most.
_PRIORITY_WORDS = (
    ("услуг", 5), ("service", 5), ("прайс", 6), ("price", 6), ("цен", 6), ("стоимост", 6), ("тариф", 5),
    ("каталог", 4), ("catalog", 4), ("меню", 4), ("menu", 3), ("о-нас", 4), ("о нас", 4), ("about", 4),
    ("компани", 3), ("company", 3), ("контакт", 4), ("contact", 4), ("отзыв", 4), ("review", 4),
    ("доставк", 3), ("delivery", 3), ("оплат", 2), ("команд", 2), ("team", 2), ("мастер", 2),
    ("faq", 2), ("вопрос", 2), ("акци", 2), ("portfolio", 2), ("портфолио", 2), ("работы", 2),
)
_NEGATIVE_WORDS = ("login", "signin", "logout", "cart", "корзин", "basket", "account", "lk", "policy",
                   "privacy", "политик", "соглашени", "cookie", "oferta", "оферт", "tag", "page=", "sort=",
                   "?", "blog/", "news/", "новост")


def _site_key(host: str) -> str:
    host = host.casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def rank_internal_links(base_url: str, links: list[tuple[str, str]]) -> list[str]:
    """Same-site page links, most informative first, without assets or account pages."""
    base = urlsplit(base_url)
    site = _site_key(base.hostname or "")
    scored: dict[str, tuple[int, int]] = {}
    for order, (href, text) in enumerate(links):
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:", "viber:", "tg:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"} or _site_key(parsed.hostname or "") != site:
            continue
        path = parsed.path or "/"
        if path.casefold().endswith(_SKIP_SUFFIXES):
            continue
        # Fragments and queries usually point at the same page; keep the canonical path only.
        canonical = f"{parsed.scheme}://{parsed.netloc}{path}"
        if canonical.rstrip("/") == f"{base.scheme}://{base.netloc}{base.path or '/'}".rstrip("/"):
            continue
        haystack = (path + " " + text).casefold()
        score = sum(weight for word, weight in _PRIORITY_WORDS if word in haystack)
        if any(word in (path + "?" * bool(parsed.query)).casefold() for word in _NEGATIVE_WORDS):
            score -= 5
        if score < 0:
            continue
        previous = scored.get(canonical)
        if previous is None or score > previous[0]:
            scored[canonical] = (score, order if previous is None else previous[1])
    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[1][1]))
    return [url for url, _ in ranked]


def page_text(parser: _VisibleHTML) -> str:
    sections = []
    title = " ".join(parser.title.split())
    if title:
        sections.append("Заголовок страницы: " + title)
    meta = list(dict.fromkeys(item for item in parser.meta if item))
    if meta:
        sections.append("Описание страницы: " + " | ".join(meta)[:1500])
    structured = _json_ld_lines(parser.json_ld)
    if structured:
        sections.append("Структурированные данные страницы:\n" + "\n".join(structured))
    visible = parser.visible_text()
    if visible:
        sections.append("Текст страницы:\n" + visible)
    return "\n\n".join(sections)


def looks_script_rendered(parser: _VisibleHTML) -> bool:
    return len(parser.visible_text()) < 300 and parser.script_count >= 3


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
    MAX_PAGES = 8
    CRAWL_SECONDS = 75
    JS_NOTE = ("Сайт показывает текст через скрипты, поэтому без браузера виден только минимум. "
               "Пришлите текст нужных страниц сообщением или прайс файлом.")

    async def fetch(self, url: str) -> IntakeSource:
        return await asyncio.to_thread(self._fetch, url)

    async def crawl(self, url: str, *, max_pages: int | None = None, text_budget: int = 48_000) -> list[IntakeSource]:
        """Read the start page and the most informative same-site pages.

        Every page is a separate source so evidence stays traceable to its URL.
        """
        return await asyncio.to_thread(self._crawl, url, max_pages or self.MAX_PAGES, text_budget)

    def _crawl(self, url: str, max_pages: int, text_budget: int) -> list[IntakeSource]:
        import time
        deadline = time.monotonic() + self.CRAWL_SECONDS
        try:
            final_url, parser = self._get_page(url)
        except (OSError, ValueError, http.client.HTTPException):
            return [make_source("owned_website", url, status="unavailable",
                                note="Не удалось безопасно прочитать страницу. Можно прислать её текст.")]
        sources = [self._page_source(url, parser, text_budget)]
        seen_texts = {hashlib.sha256(parser.visible_text().encode()).hexdigest()}
        used = len(sources[0].text)
        candidates = rank_internal_links(final_url, parser.links)
        for candidate in candidates:
            if len(sources) >= max_pages or text_budget - used < 300 or time.monotonic() > deadline:
                break
            try:
                _, page = self._get_page(candidate)
            except (OSError, ValueError, http.client.HTTPException):
                continue  # One broken subpage must not hide the rest of the site.
            digest = hashlib.sha256(page.visible_text().encode()).hexdigest()
            if digest in seen_texts or len(page.visible_text()) < 40:
                continue
            seen_texts.add(digest)
            source = self._page_source(candidate, page, min(MAX_TEXT, text_budget - used))
            sources.append(source)
            used += len(source.text)
        return sources

    def _page_source(self, reference: str, parser: _VisibleHTML, budget: int) -> IntakeSource:
        text = page_text(parser)
        note = self.JS_NOTE if looks_script_rendered(parser) else ""
        limit = max(1, min(MAX_TEXT, budget))
        if len(text) > limit:
            note = (note + " Страница длинная: прочитаны первые " + f"{limit:,}".replace(",", " ")
                    + " символов.").strip()
            return make_source("owned_website", reference, text[:limit], status="truncated", note=note)
        if not text.strip():
            return make_source("owned_website", reference, status="unavailable",
                               note=note or "На странице не найден читаемый текст.")
        return make_source("owned_website", reference, text, note=note)

    def _fetch(self, url: str) -> IntakeSource:
        try:
            _, parser = self._get_page(url)
        except (OSError, ValueError, http.client.HTTPException):
            return make_source("owned_website", url, status="unavailable",
                               note="Не удалось безопасно прочитать страницу. Можно прислать её текст.")
        return self._page_source(url, parser, MAX_TEXT)

    def _get_page(self, url: str) -> tuple[str, _VisibleHTML]:
        final_url, content_type, text = self._get(url)
        parser = _VisibleHTML()
        if "text/html" in content_type:
            parser.feed(text)
        else:
            parser.parts.append(text)
        if not parser.visible_text() and not parser.meta and not parser.json_ld:
            if parser.script_count >= 3:
                return final_url, parser  # Script-rendered page: the caller explains the gap.
            raise IntakeError("На странице не найден читаемый текст.")
        return final_url, parser

    def _get(self, url: str) -> tuple[str, str, str]:
        current_url = url
        for _ in range(6):
            connection = None
            try:
                parsed, port, address = public_target(current_url)
                connection = (_PinnedTLS(parsed.hostname, port, address) if parsed.scheme == "https"
                              else http.client.HTTPConnection(address, port, timeout=15))
                path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                connection.request("GET", path, headers={"Host": parsed.hostname,
                                   "User-Agent": "Mozilla/5.0 (compatible; AI-Growth-OS-Company-Intake/1.1)",
                                   "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                                   "Accept-Language": "ru,en;q=0.8",
                                   "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise IntakeError("Страница вернула перенаправление без адреса.")
                    current_url = urljoin(current_url, location)
                    # The next loop calls public_target again, so every redirect target
                    # is independently DNS-resolved and rejected if it is private/local.
                    continue
                if response.status != 200:
                    raise IntakeError(f"Страница недоступна (HTTP {response.status}).")
                content_type = response.getheader("Content-Type", "").casefold()
                if not any(item in content_type for item in ("text/html", "text/plain")):
                    raise IntakeError("Тип страницы не поддерживается.")
                raw = response.read(MAX_WEB_BYTES + 1)
                if len(raw) > MAX_WEB_BYTES:
                    raise IntakeError("Страница слишком большая.")
                charset_match = re.search("charset=([A-Za-z0-9._-]+)", content_type)
                charset = charset_match.group(1) if charset_match else "utf-8"
                try:
                    text = raw.decode(charset, errors="replace")
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
                return current_url, content_type, text
            finally:
                if connection is not None:
                    connection.close()
        raise IntakeError("Слишком много перенаправлений.")
