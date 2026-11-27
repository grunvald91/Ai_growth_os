"""A video or general content backlog must not starve SEO generation."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.content import worker
from app.models.domain import Artifact, Task, TaskStatus
from app.workflows.article_contracts import ArticleProductionInput
from test_seo_factory import env, seo_task  # noqa: F401


@pytest.mark.asyncio
async def test_general_and_seo_workers_select_disjoint_tasks(env, monkeypatch):
    factory, settings, product_id = env
    ran = []

    class Fake:
        async def run(self, data):
            ran.append(data.seo_brief is not None)
            return SimpleNamespace(status="failed", artifact_ids=[])

    monkeypatch.setattr(worker, "build_article_production", lambda *a, **kw: Fake())
    brief = dict(plan_item_id="a009", primary_query="запись клиентов", stage="awareness",
                 intent="informational", reader_problem="Мастер теряет клиентов.",
                 ai_questions=["как вести запись"], next_step="Онлайн-запись")
    with factory() as db:
        seo = seo_task(db, product_id, "a009", TaskStatus.QUEUED)
        db.scalar(select(Artifact).where(Artifact.task_id == seo.id)).content = (
            ArticleProductionInput(product_id=product_id, topic="SEO",
                                   channel="own_site", light=True, seo_brief=brief).model_dump_json())
        ordinary = Task(product_id=product_id, title="Ordinary", assigned_agent="content_factory",
                        status=TaskStatus.QUEUED, priority=200)
        db.add(ordinary)
        db.flush()
        db.add(Artifact(product_id=product_id, task_id=ordinary.id, kind="content_request",
                        title="Ordinary request",
                        content=ArticleProductionInput(product_id=product_id, topic="Ordinary").model_dump_json()))
        db.commit()
    assert await worker.run_content_once(factory, settings, seo_only=True) == 1
    assert await worker.run_content_once(factory, settings, seo_only=False) == 1
    assert ran == [True, False]


@pytest.mark.asyncio
async def test_round_robin_prevents_repair_or_plan_starvation(env, monkeypatch):
    import app.team_worker as team
    import app.seo.factory as factory_module
    import app.seo.autoschedule as schedule
    import app.seo.held_repair as repair_module
    factory, settings, _ = env
    calls = []

    async def articles(sf, cfg, *, seo_only):
        assert seo_only is True
        calls.append("articles")
        return 1

    async def planning(sf, cfg):
        calls.append("planning")
        return 1

    async def repair(sf, cfg):
        calls.append("repair")
        return 1

    monkeypatch.setattr(team, "SessionLocal", factory)
    monkeypatch.setattr(schedule, "enqueue_enabled_if_due", lambda *a, **kw: False)
    monkeypatch.setattr(worker, "run_content_once", articles)
    monkeypatch.setattr(factory_module, "run_factory_once", planning)
    monkeypatch.setattr(repair_module, "run_held_repair_once", repair)
    first = 0
    for _ in range(6):
        count, first = await team._seo_generation_once(settings, first=first)
        assert count == 1
    assert calls == ["articles", "planning", "repair"] * 2


@pytest.mark.asyncio
async def test_failure_in_one_phase_does_not_stop_other_seo_work(env, monkeypatch):
    import app.team_worker as team
    import app.seo.factory as factory_module
    import app.seo.autoschedule as schedule
    factory, settings, _ = env

    async def articles(*a, **kw):
        raise RuntimeError("simulated task failure")

    async def planning(*a, **kw):
        return 1

    monkeypatch.setattr(team, "SessionLocal", factory)
    monkeypatch.setattr(schedule, "enqueue_enabled_if_due", lambda *a, **kw: False)
    monkeypatch.setattr(worker, "run_content_once", articles)
    monkeypatch.setattr(factory_module, "run_factory_once", planning)
    assert await team._seo_generation_once(settings, first=0) == (1, 2)
