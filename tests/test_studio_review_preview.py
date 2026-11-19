"""Regression checks for compact Studio material previews."""
from types import SimpleNamespace

from app.api.content_studio_ui import _review_preview


def test_review_preview_drops_repeated_markdown_heading_and_normalizes_lines():
    title = "ИИ для продвижения бизнеса: задачи и критерии"
    article = f"# {title}\n\nВладелец тратит время.\n\nВторая мысль без новой строки."
    preview = _review_preview(SimpleNamespace(title=title), {"body": article})
    assert preview == "Владелец тратит время. Вторая мысль без новой строки."
    assert not preview.startswith("#")
    assert "\\n" not in preview


def test_review_preview_preserves_short_article_without_duplicate_title():
    title = "Простое видео для владельца"
    preview = _review_preview(SimpleNamespace(title=title), {"body": "Польза для клиента.\nЗатем решение."})
    assert preview == "Польза для клиента. Затем решение."
