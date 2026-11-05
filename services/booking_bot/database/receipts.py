"""Private, durable proof storage shared by web and Telegram submissions."""
from database.models import BookingReceipt

MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def receipt_mime(content):
    if not content or len(content) > MAX_RECEIPT_BYTES:
        raise ValueError("Приложите непустой чек размером до 10 МБ")
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Чек должен быть файлом PDF, JPEG, PNG или WebP")


def save_receipt(db, booking, content):
    proof = BookingReceipt(booking_id=booking.id, mime_type=receipt_mime(content),
                           content=content, size=len(content))
    db.add(proof)
    return proof
