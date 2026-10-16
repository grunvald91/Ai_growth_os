import json

from app.api.content_studio_guide import (HOW_STEPS, how_it_works_html, notes_html,
                                          stage_notes_from_content, where_html)
from uuid import uuid4


def test_stage_notes_show_only_known_fields_with_owner_wording():
    raw = json.dumps({"stage": "post_strategist", "output": {
        "audience_pain": "Не понимают, откуда приходят записи",
        "hook": "Перестаньте гадать",
        "evidence_ids": ["f1"], "internal_flag": "SECRET-TECH",
        "editor_notes": ["Убрали обещание", "Добавили оговорку"],
    }})
    notes = stage_notes_from_content(raw)
    labels = [label for label, _ in notes]
    assert labels == ["Что волнует аудиторию", "Зацепка", "Замечания редактора"]
    html = notes_html(notes)
    assert "SECRET-TECH" not in html and "evidence_ids" not in html
    assert "<li>Убрали обещание</li>" in html


def test_stage_notes_tolerate_garbage_and_escape_html():
    assert stage_notes_from_content("not json") == []
    assert stage_notes_from_content(json.dumps({"output": "x"})) == []
    notes = stage_notes_from_content(json.dumps({"output": {"hook": "<script>alert(1)</script>"}}))
    assert "<script>" not in notes_html(notes)


def test_home_guide_blocks_use_plain_russian_and_real_links():
    pid = uuid4()
    how, where = how_it_works_html(pid), where_html(pid)
    assert len(HOW_STEPS) == 5 and "от получаса до часа" in how
    for needle in ("Дать задание", "В работе", "Материалы", "Публикации", "Комментарии"):
        assert needle in where
    assert f"/studio/products/{pid}/work" in where
    for jargon in ("агент", "артефакт", "pipeline", "LLM"):
        assert jargon not in (how + where)
