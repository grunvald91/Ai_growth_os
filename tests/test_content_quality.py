"""Offline quality-check tests: warnings are guidance, never performance grades."""
from app.content.quality import post_quality_warnings


def test_content_quality_flags_vague_opening_and_duplicate_paragraphs():
    para = ("Запись только через переписку заставляет клиента ждать ответа "
            "и несколько раз уточнять время, хотя проблему можно обсудить конкретно.")
    body = "В современном мире многие сталкиваются с задачами.\n\n" + para + "\n\n" + para
    notes = post_quality_warnings("Пост", body, "telegram")
    assert any("Начало слишком общее" in item for item in notes)
    assert any("Заголовок слишком общий" in item for item in notes)
    assert any("Повторяются" in item for item in notes)


def test_content_quality_does_not_require_jokes_or_invent_metrics():
    body = (
        "Перед консультацией полезно записать вопросы, которые вызывают напряжение.\n\n"
        "Если тема непростая, можно обсуждать её в удобном темпе. "
        "Не нужно обещать себе немедленный результат.\n\n"
        "Напишите, если хотите узнать условия встречи."
    )
    notes = post_quality_warnings("Как подготовиться к первой консультации", body, "telegram")
    assert notes == []


def test_content_quality_warns_on_absolute_result_promise():
    body = "Мы гарантируем рост и обещаем 100% результата.\n\nУзнайте подробности."
    notes = post_quality_warnings("Как проверить результат продвижения", body, "telegram")
    assert any("обещание результата" in item for item in notes)


def test_long_telegram_text_requires_mobile_readability_review():
    body = "Советы владельцу мастерской. " * 150
    notes = post_quality_warnings(
        "Как планировать работу маленькой мастерской", body, "telegram"
    )
    assert any("Для Telegram текст длинный" in item for item in notes)
    assert any("Нет разбивки на абзацы" in item for item in notes)
