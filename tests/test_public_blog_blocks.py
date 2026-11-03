"""Public SEO rendering must keep numbered steps, paragraphs and source order."""
from app.api.public_blog import body_blocks, split_body


def test_inline_numbered_steps_become_readable_ordered_list():
    text = ("Выполните действия: 1. Выберите услугу. "
            "2. Найдите свободный слот. 3. Подтвердите запись.")
    blocks = body_blocks(text)
    assert blocks == [
        {"type": "paragraph", "text": "Выполните действия:"},
        {"type": "list", "ordered": True,
         "items": ["Выберите услугу.", "Найдите свободный слот.",
                   "Подтвердите запись."]},
    ]


def test_regular_paragraphs_and_marked_lists_preserve_order_and_type():
    body = "Вступление.\n- Цена\n- Длительность\nВывод.\n1. Сначала\n2. Затем\nФинал."
    assert body_blocks(body) == [
        {"type": "paragraph", "text": "Вступление."},
        {"type": "list", "ordered": False, "items": ["Цена", "Длительность"]},
        {"type": "paragraph", "text": "Вывод."},
        {"type": "list", "ordered": True, "items": ["Сначала", "Затем"]},
        {"type": "paragraph", "text": "Финал."},
    ]
    assert split_body(body) == (
        ["Вступление.", "Вывод.", "Финал."],
        ["Цена", "Длительность", "Сначала", "Затем"],
    )


def test_isolated_numbers_and_prices_remain_plain_text():
    text = "Условия: пункт 1. Начало в 10:00, цена 2500 руб. Пункт 3. не существует."
    assert body_blocks(text) == [{"type": "paragraph", "text": text}]


def test_escaped_text_is_not_interpreted_as_html():
    text = "1. <script>alert(1)</script> 2. Конец."
    items = body_blocks(text)[0]["items"]
    assert items[0] == "<script>alert(1)</script>"
    # Public clients HTML-escape each text field; parser itself never renders HTML.

def test_inline_dash_items_are_real_bullets():
    text = "Что помогает: — ответ клиенту; — понятное подтверждение; — напоминание."
    assert body_blocks(text) == [
        {"type": "paragraph", "text": "Что помогает:"},
        {"type": "list", "ordered": False,
         "items": ["ответ клиенту", "понятное подтверждение", "напоминание."]},
    ]


def test_legacy_single_line_prose_gets_readable_paragraphs_without_losing_sentences():
    text = " ".join(["Предложение номер %d сообщает важное условие клиенту." % i for i in range(25)])
    blocks = body_blocks(text)
    assert len(blocks) >= 2
    assert all(block["type"] == "paragraph" for block in blocks)
    assert " ".join(block["text"] for block in blocks) == text
