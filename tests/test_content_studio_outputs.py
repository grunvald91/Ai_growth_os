"""Full agent outputs and the video-script viewer are read from stored records, escaped and never cut."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_outputs import (parse_stage, stage_output_html, video_brief_of,
                                            video_viewer_html)
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus

T0 = datetime(2026, 10, 16, 6, 0, 0, tzinfo=timezone.utc)

BRIEF = {
    "asset_type": "short_video", "target_platform": "youtube_shorts", "aspect_ratio": "9:16",
    "duration_seconds": 30, "objective": "Показать, как записаться за минуту",
    "hook_variants": ["Хватит терять клиентов <b>сегодня</b>", "Запись без звонков"],
    "creative_concept": "Мастер и телефон", "visual_style": "Тёплый свет",
    "voiceover_or_dialogue_script": "Первая фраза озвучки.\nВторая фраза озвучки.",
    "shots": [
        {"number": 1, "start_second": 0, "end_second": 10, "purpose": "Зацепка", "shot_type": "крупный план",
         "visual_description": "Руки держат телефон", "camera_and_movement": "Наезд", "action": "Нажимает кнопку",
         "spoken_text": "Клиенты пишут ночью", "on_screen_text": "ЗАПИСЬ 24/7", "transition": "Резкий",
         "sound_or_music_note": "Тик-так", "generation_prompt": "PROMPT-ONE-SECRET-TEXT", "avoid_in_generation": "логотипы"},
        {"number": 2, "start_second": 10, "end_second": 30, "purpose": "Решение", "shot_type": "средний",
         "visual_description": "Экран записи", "camera_and_movement": "Статика", "action": "Выбирает время",
         "on_screen_text": "Готово", "transition": "Плавный", "sound_or_music_note": "Лёгкий бит",
         "generation_prompt": "PROMPT-TWO"},
    ],
    "caption_variants": ["Подпись один", "Подпись два"], "music_direction": "Лёгкий бит",
    "editing_rhythm": "Быстро", "cover_frame": "Кадр с кнопкой", "cta_frame": "Записаться",
    "export_spec": "1080x1920", "production_notes": ["Снять вертикально"],
}


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="Мастерская", slug="outputs-test")
        db.add(product)
        db.commit()
        app.dependency_overrides[get_db] = lambda: (yield db)
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, agent_critic_enabled=True)
        with TestClient(app) as client:
            yield client, db, product
        app.dependency_overrides.clear()
    engine.dispose()


def _stage(db, product, task, agent, content, start_min):
    run = AgentRun(task_id=task.id, agent_name=agent, status="completed", model="m",
                   input_references=[], output_references=[],
                   started_at=T0 + timedelta(minutes=start_min),
                   finished_at=T0 + timedelta(minutes=start_min + 1))
    db.add(run)
    db.flush()
    art = Artifact(product_id=product.id, task_id=task.id, agent_run_id=run.id,
                   kind="content_stage_output", title=agent, status="draft",
                   content=json.dumps(content, ensure_ascii=False))
    db.add(art)
    db.flush()
    run.output_references = [{"kind": "artifact", "id": str(art.id)}]
    return run


def _task(db, product):
    task = Task(product_id=product.id, title="CONTENT: тема", assigned_agent="content_factory",
                status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    return task


def test_task_page_shows_full_untruncated_output_of_every_agent(studio):
    client, db, product = studio
    task = _task(db, product)
    long_text = "Очень длинный абзац статьи. " * 80  # ~2200 знаков, раньше резалось на 420
    _stage(db, product, task, "article_writer", {"stage": "article_writer", "output": {
        "sections": [{"heading": "Первый раздел", "body": long_text, "kind": "advice"},
                     {"heading": "Второй раздел", "body": "Короткий", "kind": "hypothesis"}],
        "proof_needed_notes": ["Проверить цифру"]}}, 0)
    # креативный агент пишет вывод без обёртки output
    _stage(db, product, task, "creative_strategist", {
        "audience_pain": "Боль аудитории из креатива", "hook": "Зацепка из креатива",
        "evidence_ids": ["f1"], "internal_flag": "VISIBLE-LABEL"}, 2)
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert long_text.strip() in page.replace("&amp;", "&")
    assert "Первый раздел" in page and "Совет" in page and "Гипотеза" in page
    assert "Проверить цифру" in page
    assert "Боль аудитории из креатива" in page  # раньше было «не сохранился»
    assert "не сохранился отдельно" not in page
    assert "Раскрыть все выводы" in page


def test_model_text_is_escaped_and_fallback_is_flagged(studio):
    client, db, product = studio
    task = _task(db, product)
    _stage(db, product, task, "seo_editor", {"stage": "seo_editor", "fallback_used": True,
           "fallback_reason": "timeout", "output": {"hook": "<script>alert(1)</script>"}}, 0)
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "запасной вариант" in page and "timeout" in page


def test_video_viewer_has_voice_timeline_shots_and_collapsed_prompts():
    assert video_brief_of({"production_brief": BRIEF}) is BRIEF
    html = video_viewer_html(BRIEF, "t")
    assert "Текст озвучки" in html and "Первая фраза озвучки." in html
    assert 'class="sg-vid-line"' in html and 'href="#t-s2"' in html
    assert "style=\"flex:10\"" in html and "style=\"flex:20\"" in html  # ширина по длительности кадра
    assert "0–10 сек" in html and "10–30 сек" in html
    assert "Клиенты пишут ночью" in html and "ЗАПИСЬ 24/7" in html
    assert '<details class="sg-o-prompt"><summary>Промпт для генерации кадра' in html
    assert "Хватит терять клиентов &lt;b&gt;сегодня&lt;/b&gt;" in html and "<b>сегодня</b>" not in html
    assert "Подпись два" in html and "Скопировать весь сценарий" in html
    assert "YouTube Shorts" in html and "30 сек" in html


def test_creative_frames_use_the_same_viewer_and_static_image_does_not():
    draft = {"format": "story", "platform": "instagram_stories", "title": "Истории", "hook": "Зацепка",
             "cta": "Записаться", "voiceover_script": "Голос", "caption": "Подпись под историей", "cover_text": "Обложка",
             "visual_style": "Светлый", "production_notes": [],
             "frames": [{"number": 1, "start_second": 0, "end_second": 5, "purpose": "Старт", "visual_prompt": "Кадр один",
                         "on_screen_text": "Привет", "action_or_animation": "Появляется текст", "spoken_text": "Привет!"}]}
    assert video_brief_of(draft) is draft
    html = stage_output_html({"output": draft, "fallback": False, "reason": ""}, "c")
    assert "Кадр один" in html and "Привет!" in html and "Истории" in html
    still = dict(draft, format="static_image")
    assert video_brief_of(still) is None


def test_parse_stage_accepts_both_formats_and_rejects_garbage():
    assert parse_stage("не json") is None
    assert parse_stage(json.dumps({"output": "x"})) is None
    assert parse_stage(json.dumps({"stage": "a", "output": {"hook": "x"}}))["stage"] == "a"
    assert parse_stage(json.dumps({"hook": "x"}))["output"] == {"hook": "x"}


def test_material_page_and_dialog_use_the_viewer(studio):
    client, db, product = studio
    art = Artifact(product_id=product.id, kind="video_script", title="Сценарий Shorts", status="draft",
                   content=json.dumps({"production_brief": BRIEF}, ensure_ascii=False))
    db.add(art)
    db.commit()
    page = client.get(f"/studio/products/{product.id}/materials/{art.id}").text
    assert 'class="sg-vid-line"' in page and "ЗАПИСЬ 24/7" in page and "Текст озвучки" in page
    assert 'id="material-copy"' in page
