import json

import pytest

from app.content.whiteboard.adapters import parse_storyboard


def storyboard_text():
    return '\n\n'.join(
        f'Сцена {i}: Объяснение\n' + '\n'.join(
            f'Бит {j}: Подпись\nРеплика: Короткая реплика.\nИконка: Простой рисунок.'
            for j in (1, 2)) for i in (1, 2, 3))


def test_explicit_provider_blocks_convert_without_changing_text():
    plan = parse_storyboard(storyboard_text())
    assert len(plan.scenes) == 3
    assert all(len(s.beats) == 2 for s in plan.scenes)
    assert plan.scenes[0].beats[0].narration == 'Короткая реплика.'
    assert plan.scenes[2].beats[1].visual == 'Простой рисунок.'
    assert parse_storyboard(plan.model_dump_json()) == plan
    assert parse_storyboard('```json\n' + plan.model_dump_json() + '\n```') == plan


@pytest.mark.parametrize('change', [
    lambda s: s + '\nUnknown extra text',
    lambda s: s.replace('Сцена 2:', 'Сцена 3:', 1),
    lambda s: s.replace('Бит 2:', 'Бит 3:', 1),
    lambda s: s.replace('Иконка: Простой рисунок.', '', 1),
    lambda s: s.replace('Иконка: Простой рисунок.', 'Иконка: Простой рисунок.\nИконка: Другой рисунок.', 1),
])
def test_ambiguous_or_incomplete_text_rejected(change):
    with pytest.raises(ValueError):
        parse_storyboard(change(storyboard_text()))


def test_invalid_json_schema_is_not_reinterpreted_as_prose():
    with pytest.raises(ValueError):
        parse_storyboard(json.dumps({'scenes': [], 'unapproved_field': 'ignored?'}))
