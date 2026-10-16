"""Process transparency: agent count, critic findings and the line-by-line log are read from real records."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus

T0 = datetime(2026, 10, 16, 6, 0, 0, tzinfo=timezone.utc)  # 09:00 МСК


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="Мастерская кофе", slug="coffee-process")
        db.add(product)
        db.commit()
        app.dependency_overrides[get_db] = lambda: (yield db)

        def overrides(critic: bool):
            app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, agent_critic_enabled=critic)

        with TestClient(app) as client:
            yield client, db, product, overrides
        app.dependency_overrides.clear()
    engine.dispose()


def _run(db, task, name, status, start_min, dur_min=0):
    run = AgentRun(task_id=task.id, agent_name=name, status=status, model="claude-test",
                   input_references=[], output_references=[])
    if status in ("running", "completed"):
        run.started_at = T0 + timedelta(minutes=start_min)
    if status == "completed":
        run.finished_at = run.started_at + timedelta(minutes=dur_min)
    db.add(run)
    db.flush()
    return run


def _call(db, product, run, stage, at_min, tin=1000, tout=2000):
    db.add(CostRecord(product_id=product.id, agent_run_id=run.id, category="llm", provider="test",
                      amount=0, input_tokens=tin, output_tokens=tout,
                      incurred_at=T0 + timedelta(minutes=at_min),
                      details={"llm_stage": stage, "model": "claude-test"}))


def _review(db, product, task, run, status, findings=(), decisions=(), at_min=3):
    art = Artifact(product_id=product.id, task_id=task.id, agent_run_id=run.id,
                   kind="agent_quality_review", title="AI reviewer decisions and usage", status="draft",
                   content=json.dumps({"status": status, "findings": list(findings),
                                       "decisions": list(decisions)}, ensure_ascii=False))
    db.add(art)
    db.flush()
    art.created_at = T0 + timedelta(minutes=at_min)
    return art


def _task(db, product):
    task = Task(product_id=product.id, title="CONTENT: тема", assigned_agent="content_factory",
                status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    task.created_at = T0 - timedelta(minutes=1)
    return task


def test_task_page_counts_agents_and_lists_every_step_in_order(studio):
    client, db, product, overrides = studio
    overrides(False)
    task = _task(db, product)
    a = _run(db, task, "content_strategist", "completed", 0, 4)
    b = _run(db, task, "article_writer", "completed", 4, 6)
    c = _run(db, task, "human_editor", "running", 10)
    _run(db, task, "repurposing_editor", "queued", 0)
    _call(db, product, a, "author", 3, 1200, 800)
    _call(db, product, b, "author", 9, 2500, 4100)
    db.commit()

    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "<b>4</b><span>агента в задании" in page
    assert "готово 2" in page and "работает 1" in page and "ждут 1" in page
    assert "<b>2</b><span>обращения к модели" in page
    assert "Журнал по строкам" in page
    # хронология: старт стратега раньше старта автора, старт автора раньше старта редактора
    i1 = page.index("Стратег начал: Изучение темы")
    i2 = page.index("Автор статьи начал: Написание статьи")
    i3 = page.index("Редактор для читателя начал: Редактура для читателя")
    assert i1 < i2 < i3
    assert "09:00:00" in page and "09:04:00" in page  # время московское


def test_critic_off_is_said_plainly_and_not_shown_as_no_findings(studio):
    client, db, product, overrides = studio
    overrides(False)
    task = _task(db, product)
    _run(db, task, "content_strategist", "completed", 0, 3)
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "Выключен" in page and "Сейчас критик выключен" in page
    assert "Серьёзных замечаний нет" not in page


def test_critic_findings_and_author_decisions_are_shown(studio):
    client, db, product, overrides = studio
    overrides(True)
    task = _task(db, product)
    run = _run(db, task, "article_writer", "completed", 0, 6)
    _call(db, product, run, "author", 2)
    _call(db, product, run, "critic", 3)
    _call(db, product, run, "revision", 5)
    _review(db, product, task, run, "revised", at_min=5,
            findings=[
                {"id": "f1", "severity": "major", "problem": "Обещан результат без оснований <b>x</b>",
                 "suggestion": "Заменить на осторожную формулировку"},
                {"id": "f2", "severity": "critical", "problem": "Нет призыва к действию", "suggestion": "Добавить один призыв"},
                {"id": "f3", "severity": "minor", "problem": "Длинное вступление", "suggestion": ""},
            ],
            decisions=[{"id": "f1", "decision": "accept", "rationale": "Это действительно риск"},
                       {"id": "f2", "decision": "reject", "rationale": "Призыв уже есть в конце"}])
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "Нашёл 2 замечания. Автор принял 1, отклонил 1. Текст обновлён" in page
    assert "Автор принял." in page and "Автор отклонил." in page
    assert "Призыв уже есть в конце" in page
    assert "Заменить на осторожную формулировку" in page
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page  # текст модели экранируется
    assert "<b>1</b><span>проверка критиком" in page
    assert "Критик перечитал работу автора" in page and "Автор переработал текст по замечаниям" in page


def test_critic_no_issues_and_enabled_but_missing_are_distinct(studio):
    client, db, product, overrides = studio
    overrides(True)
    task = _task(db, product)
    checked = _run(db, task, "content_strategist", "completed", 0, 3)
    unchecked = _run(db, task, "seo_editor", "completed", 3, 3)
    _review(db, product, task, checked, "no_material_issues")
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "Серьёзных замечаний нет" in page
    assert "на этом шаге не запускался" in page and unchecked.id


def test_failed_provider_attempt_and_failed_step_are_visible(studio):
    client, db, product, overrides = studio
    overrides(True)
    task = _task(db, product)
    run = _run(db, task, "post_strategist", "failed", 0)
    run.started_at = T0
    _review(db, product, task, run, "provider_timeout")
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert "модель не успела ответить" in page
    assert "остановился с ошибкой" in page


def test_work_list_and_home_show_team_numbers(studio):
    client, db, product, overrides = studio
    overrides(False)
    task = _task(db, product)
    task.status = TaskStatus.RUNNING
    _run(db, task, "content_strategist", "completed", 0, 5)
    _run(db, task, "article_writer", "running", 5)
    db.commit()
    work = client.get(f"/studio/products/{product.id}/work").text
    assert "Агентов: 2 · шагов 1 из 2 · сейчас: Написание статьи" in work
    home = client.get(f"/studio/products/{product.id}").text
    assert "Что уже сделала команда" in home
    assert "шаг выполнено агентами" in home or "шага выполнено агентами" in home


def test_handoff_finish_of_previous_step_precedes_start_of_next_at_same_second(studio):
    client, db, product, overrides = studio
    overrides(False)
    task = _task(db, product)
    _run(db, task, "content_strategist", "completed", 0, 5)
    _run(db, task, "seo_editor", "completed", 5, 5)  # стартует в ту же секунду, когда закончил стратег
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert page.index("Стратег закончил: Изучение темы") < page.index("SEO-редактор начал: Поисковые запросы")
    assert "заняло 5 мин<" in page.replace("</small>", "<")
