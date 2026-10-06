import json
from types import SimpleNamespace

from app.content.presentation import material_markdown, plain_text, formatted_html


def material(kind, value):
    return SimpleNamespace(kind=kind, title='Название', content=json.dumps(value, ensure_ascii=False))


def test_structured_reader_article_is_prose_with_facts_and_cta():
    record = material('article', {
        'reader_article': {'title': 'Статья', 'lead': 'Введение',
            'fact_renderings': [{'text': 'Проверенный факт', 'source_id': 'PRIVATE_SOURCE'}],
            'sections': [{'heading': 'Раздел', 'body': 'Читательский текст'}],
            'closing': 'Завершение', 'editorial_notes': ['PRIVATE_NOTE']},
        'conversion': {'cta': 'Запишитесь на демо', 'measurement': {'internal': 'PRIVATE_METRIC'}},
    })
    copy = plain_text(material_markdown(record))
    assert all(part in copy for part in ['Статья', 'Введение', 'Проверенный факт',
        'Раздел', 'Читательский текст', 'Завершение', 'Запишитесь на демо'])
    assert 'PRIVATE_' not in copy and 'reader_article' not in copy


def test_video_copy_contains_full_storyboard_and_captions_without_prompts():
    record = material('video_script', {'article_id': 'INTERNAL_ID', 'production_brief': {
        'voiceover_or_dialogue_script': 'Речь ведущего', 'hook_variants': ['Первое начало', 'Второе начало'],
        'shots': [{'start_second': 0, 'end_second': 5, 'visual_description': 'Ведущий в кадре',
                   'on_screen_text': 'Надпись', 'camera_and_movement': 'Крупный план',
                   'generation_prompt': 'INTERNAL_PROMPT'}],
        'caption_variants': ['Описание первого варианта', 'Описание второго варианта'],
    }})
    copy = plain_text(material_markdown(record))
    assert 'Речь ведущего' in copy and 'Сцена 1 · 0–5 сек.' in copy
    assert 'Ведущий в кадре' in copy and 'Крупный план' in copy and 'Надпись' in copy
    assert 'Описание первого варианта' in copy
    assert 'INTERNAL_' not in copy and 'generation_prompt' not in copy


def test_plain_text_keeps_spaces_links_paragraphs_and_lists():
    copy = plain_text('# Заголовок\n\n**Два** **слова** и [сайт](https://example.com).\n\n3. Первый\n4. Второй')
    assert copy == 'Заголовок\n\nДва слова и сайт (https://example.com).\n\n3. Первый\n4. Второй'


def test_unknown_payload_and_technical_code_are_not_publication_copy():
    record = material('telegram_brief', {'hook': 'Начало', 'outline': ['Первое', 'Второе'],
        'measurement': {'internal_secret': 'INTERNAL_VALUE'}})
    assert 'Начало' in material_markdown(record)
    assert 'INTERNAL_VALUE' not in material_markdown(record)
    assert material_markdown(material('article', {'schema_version': '2A2', 'strategy': {'code': 'INTERNAL_VALUE'}})) == ''
    assert plain_text('Текст\n\n```json\n{"internal": true}\n```\n\nПродолжение') == 'Текст\n\nПродолжение'
    assert 'href="javascript:' not in formatted_html('[Нажми](javascript:alert(1))')
