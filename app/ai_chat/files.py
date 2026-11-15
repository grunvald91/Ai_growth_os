"""Validated file upload, extraction, and secure storage for ServGo AI Chat."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError

from app.intake.file_extract import extract_pdf, extract_spreadsheet, office_archive, xml_root
from app.privacy.gateway import PrivacyGateway

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB total per message (and per upload)
MAX_FILES_PER_MESSAGE = 5
MAX_EXTRACTED_TEXT_CHARS = 240_000

IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

DOCUMENT_MIME_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}

ALLOWED_EXTENSIONS = frozenset({*IMAGE_MIME_TYPES, *DOCUMENT_MIME_TYPES})
_PRIVACY = PrivacyGateway()


class ChatFileError(ValueError):
    """User-facing validation error for uploaded files."""


@dataclass(frozen=True)
class ParsedAttachment:
    filename: str
    extension: str
    mime_type: str
    byte_size: int
    sha256: str
    is_image: bool
    extracted_text: str | None
    extraction_status: str
    extraction_note: str | None
    width: int | None
    height: int | None
    normalized_bytes: bytes


def safe_filename(raw: str | None) -> str:
    name = PurePosixPath((raw or "file").replace("\\", "/")).name.strip()
    name = "".join(ch for ch in name if ch >= " " and ch not in {'"', "'", "<", ">", "/", "\\"})
    if not name or name in {".", ".."}:
        name = "file"
    return name[:200]


def storage_dir(products_root: Path) -> Path:
    root = Path(products_root) / "_ai_chat" / "files"
    if root.is_symlink():
        raise ChatFileError("Небезопасный каталог хранения файлов")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def write_attachment_bytes(products_root: Path, attachment_id: UUID, extension: str, data: bytes) -> str:
    base = storage_dir(products_root).resolve()
    rel = f"{UUID(str(attachment_id))}{extension}"
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or target.is_symlink():
        raise ChatFileError("Некорректный путь сохранения файла")
    fd, tmp_name = tempfile.mkstemp(prefix=".upload-", dir=base)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return rel


def resolve_attachment_path(products_root: Path, storage_path: str) -> Path:
    base = storage_dir(products_root).resolve()
    if not storage_path or "/" in storage_path or "\\" in storage_path or ".." in storage_path:
        raise LookupError("Некорректный путь файла")
    target = (base / storage_path).resolve()
    if not target.is_relative_to(base) or target.is_symlink() or not target.is_file():
        raise LookupError("Файл не найден")
    return target


def remove_attachment_file(products_root: Path, storage_path: str) -> None:
    try:
        path = resolve_attachment_path(products_root, storage_path)
    except (LookupError, ChatFileError):
        return
    path.unlink(missing_ok=True)


def _decode_text(data: bytes, suffix: str) -> tuple[str, str | None]:
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ChatFileError("Файл содержит двоичные данные и не является текстовым документом.")
    note: str | None = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ChatFileError("Не удалось декодировать текстовый файл UTF-16.") from exc
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            if suffix == ".json":
                raise ChatFileError("JSON-файл должен быть в кодировке UTF-8.") from None
            try:
                text = data.decode("cp1251")
                note = "Прочитана кодировка Windows-1251."
            except UnicodeDecodeError as exc:
                raise ChatFileError("Не удалось прочитать кодировку текстового файла.") from exc
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text[:4096]):
        raise ChatFileError("Файл содержит недопустимые управляющие символы.")
    return text, note


def _validate_and_extract_sync(filename: str, data: bytes) -> ParsedAttachment:
    clean_name = safe_filename(filename)
    ext = PurePosixPath(clean_name).suffix.casefold()
    if ext not in ALLOWED_EXTENSIONS:
        raise ChatFileError(
            f"Формат «{ext or 'без расширения'}» не поддерживается. "
            "Доступны: PDF, TXT, MD, JSON, CSV, DOCX, XLSX, XLS, JPG, PNG, WebP."
        )
    if not data:
        raise ChatFileError("Пустой файл нельзя прикрепить.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ChatFileError("Размер файла превышает 20 МБ.")

    digest = hashlib.sha256(data).hexdigest()

    if ext in IMAGE_MIME_TYPES:
        return _validate_image(clean_name, ext, data, digest)

    extracted_text = ""
    status = "ready"
    note: str | None = None

    if ext in {".txt", ".md"}:
        extracted_text, note = _decode_text(data, ext)
    elif ext == ".json":
        raw_text, note = _decode_text(data, ext)
        try:
            parsed = json.loads(raw_text)
        except ValueError as exc:
            raise ChatFileError("Файл не является корректным JSON.") from exc
        extracted_text = json.dumps(parsed, ensure_ascii=False, indent=2)
    elif ext == ".csv":
        raw_text, note = _decode_text(data, ext)
        try:
            dialect = csv.Sniffer().sniff(raw_text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows: list[str] = []
        total_len = 0
        for row in csv.reader(io.StringIO(raw_text), dialect):
            line = " | ".join(row)
            rows.append(line)
            total_len += len(line) + 1
            if total_len > MAX_EXTRACTED_TEXT_CHARS:
                status = "truncated"
                note = "Таблица CSV прочитана частично из-за ограничения размера."
                break
        extracted_text = "\n".join(rows)
    elif ext == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise ChatFileError("Файл не является действительным PDF-документом.")
        try:
            extracted_text, partial, pdf_note = extract_pdf(data)
        except ValueError as exc:
            if "Encrypted PDF" in str(exc):
                raise ChatFileError("PDF защищён паролем и не может быть прочитан.") from None
            raise ChatFileError("Не удалось прочитать PDF-файл.") from None
        except Exception as exc:
            raise ChatFileError("Не удалось прочитать PDF-файл.") from exc
        note = pdf_note or None
        if partial:
            status = "truncated"
        if not extracted_text.strip():
            status = "empty"
            note = (
                "Не удалось извлечь текст из PDF (возможно, это скан без текстового слоя)."
            )
    elif ext == ".docx":
        try:
            with office_archive(data) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ChatFileError("Файл не является действительным документом DOCX.")
                root = xml_root(archive.read("word/document.xml"))
        except ChatFileError:
            raise
        except Exception as exc:
            raise ChatFileError("Не удалось безопасно прочитать DOCX-документ.") from exc
        extracted_text = "\n".join(
            " ".join(n.text or "" for n in p.iter() if n.tag.endswith("}t"))
            for p in root.iter()
            if p.tag.endswith("}p")
        ).strip()
        if not extracted_text:
            extracted_text = " ".join(n.text or "" for n in root.iter() if n.tag.endswith("}t")).strip()
        if not extracted_text:
            status = "empty"
            note = "В документе DOCX не найден текст."
    elif ext in {".xlsx", ".xls"}:
        if ext == ".xls" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ChatFileError("Файл не является действительной таблицей XLS.")
        try:
            extracted_text, partial, xls_note = extract_spreadsheet(ext, data)
        except Exception as exc:
            raise ChatFileError("Не удалось безопасно прочитать таблицу.") from exc
        note = xls_note or None
        if partial:
            status = "truncated"
        if not extracted_text.strip():
            status = "empty"
            note = "В таблице не найдены заполненные ячейки."

    sanitized = _PRIVACY.sanitize_text(extracted_text or "")
    if len(sanitized) > MAX_EXTRACTED_TEXT_CHARS:
        sanitized = sanitized[:MAX_EXTRACTED_TEXT_CHARS]
        status = "truncated"
        note = ((note or "") + f" Текст сокращён до {MAX_EXTRACTED_TEXT_CHARS} символов.").strip()

    return ParsedAttachment(
        filename=clean_name,
        extension=ext,
        mime_type=DOCUMENT_MIME_TYPES[ext],
        byte_size=len(data),
        sha256=digest,
        is_image=False,
        extracted_text=sanitized,
        extraction_status=status,
        extraction_note=note,
        width=None,
        height=None,
        normalized_bytes=data,
    )


def _validate_image(clean_name: str, ext: str, data: bytes, digest: str) -> ParsedAttachment:
    if ext == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ChatFileError("Файл не является действительным изображением PNG.")
    if ext in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise ChatFileError("Файл не является действительным изображением JPEG.")
    if ext == ".webp" and (len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP"):
        raise ChatFileError("Файл не является действительным изображением WebP.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            expected_formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
            if img.format != expected_formats[ext]:
                raise ChatFileError("Содержимое изображения не соответствует его расширению.")
            if img.width < 1 or img.height < 1 or img.width * img.height > 40_000_000:
                raise ChatFileError("Разрешение изображения выходит за допустимые пределы.")
            img.load()
            width, height = img.size
    except ChatFileError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ChatFileError("Повреждённое или неподдерживаемое изображение.") from exc

    return ParsedAttachment(
        filename=clean_name,
        extension=ext,
        mime_type=IMAGE_MIME_TYPES[ext],
        byte_size=len(data),
        sha256=digest,
        is_image=True,
        extracted_text=None,
        extraction_status="ready",
        extraction_note=None,
        width=width,
        height=height,
        normalized_bytes=data,
    )


async def validate_and_extract_upload(filename: str, data: bytes) -> ParsedAttachment:
    """Validate format/magic bytes and extract document text off the event loop."""
    return await asyncio.to_thread(_validate_and_extract_sync, filename, data)


def validate_generated_image(raw: bytes) -> tuple[bytes, int, int, str]:
    """Validate and normalize a generated image from ClaudeHub as safe PNG."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ATTACHMENT_BYTES:
        raise ChatFileError("Размер сгенерированного изображения выходит за пределы.")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width < 64 or source.height < 64 or source.width * source.height > 20_000_000:
                raise ChatFileError("Размеры сгенерированного изображения некорректны.")
            source.load()
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            width, height = rgb.size
    except ChatFileError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ChatFileError("Провайдер вернул некорректное изображение.") from exc
    out = io.BytesIO()
    rgb.save(out, format="PNG", optimize=True)
    png_bytes = out.getvalue()
    if len(png_bytes) > MAX_ATTACHMENT_BYTES:
        raise ChatFileError("Сгенерированный PNG превышает допустимый размер.")
    digest = hashlib.sha256(png_bytes).hexdigest()
    return png_bytes, width, height, digest
