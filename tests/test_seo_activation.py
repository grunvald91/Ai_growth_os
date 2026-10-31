"""Opt-in regression: demo companies never start SEO until explicit owner action."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product, Task, TaskStatus
from app.seo.activation import is_enabled, set_enabled
from app.seo.factory import queue_factory, run_factory_once
from app.seo.control import create_seo_run, SeoControlError
from app.core.config import Settings, get_settings


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        main = Product(name="Реальная компания", slug="real-company")
        demo = Product(name="Демо бизнес", slug="demo-company")
        db.add_all([main, demo])
        db.commit()
        yield db, main, demo
    engine.dispose()


def test_all_existing_and_demo_businesses_default_off_and_never_queue(env):
    db, real, demo = env
    assert not is_enabled(db, real.id)
    assert not is_enabled(db, demo.id)
    for company in (real, demo):
        with pytest.raises(ValueError, match="выключен"):
            queue_factory(db, company.id)
        db.rollback()
        with pytest.raises(SeoControlError, match="выключен"):
            create_seo_run(db, product_id=company.id)
        db.rollback()
    assert list(db.scalars(select(Task))) == []


def test_explicit_enable_is_company_scoped_and_does_not_start_anything(env):
    db, real, demo = env
    set_enabled(db, real.id, enabled=True, actor="owner")
    assert is_enabled(db, real.id)
    assert not is_enabled(db, demo.id)
    assert list(db.scalars(select(Task))) == []
    run = queue_factory(db, real.id)
    assert run.status == TaskStatus.QUEUED
    with pytest.raises(ValueError):
        queue_factory(db, demo.id)


def test_disable_cancels_only_queued_seo_jobs_and_keeps_other_tasks(env):
    db, real, demo = env
    set_enabled(db, real.id, enabled=True, actor="owner")
    queued = queue_factory(db, real.id)
    article = Task(product_id=real.id, title="SEO-ARTICLE:legacy", assigned_agent="content_factory",
                   status=TaskStatus.QUEUED)
    other = Task(product_id=real.id, title="Normal marketing", assigned_agent="content_factory",
                 status=TaskStatus.QUEUED)
    demo_task = Task(product_id=demo.id, title="Normal", assigned_agent="content_factory",
                     status=TaskStatus.QUEUED)
    db.add_all([article, other, demo_task])
    db.commit()
    set_enabled(db, real.id, enabled=False, actor="owner")
    db.expire_all()
    assert not is_enabled(db, real.id)
    assert db.get(Task, queued.id).status == TaskStatus.CANCELLED
    assert db.get(Task, article.id).status == TaskStatus.CANCELLED
    assert db.get(Task, other.id).status == TaskStatus.QUEUED
    assert db.get(Task, demo_task.id).status == TaskStatus.QUEUED
    with pytest.raises(ValueError):
        queue_factory(db, real.id)


@pytest.mark.asyncio
async def test_preexisting_demo_job_never_runs_or_spends_on_worker(env):
    db, real, demo = env
    stale = Task(product_id=demo.id, title="SEO-завод: старый", assigned_agent="seo_factory",
                 status=TaskStatus.QUEUED)
    db.add(stale)
    db.commit()
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert await run_factory_once(factory, Settings(_env_file=None)) == 0
    db.refresh(stale)
    assert stale.status == TaskStatus.QUEUED


def test_owner_toggle_is_post_only_and_queue_blocked_until_enable(env):
    db, real, demo = env
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="development", _env_file=None)
    try:
        with TestClient(app) as client:
            base = f"/studio/products/{real.id}/seo"
            page = client.get(base)
            assert page.status_code == 200
            assert 'Подключить SEO-завод' in page.text
            assert 'action="enable"' in page.text
            assert 'Новый запуск SEO-завода</a>' not in page.text
            assert client.get(base + "/activation").status_code == 405
            unapproved = client.post(base + "/queue", data={"focus": "парикмахеры"})
            assert unapproved.status_code == 422
            assert list(db.scalars(select(Task))) == []
            activated = client.post(base + "/activation", data={"action": "enable"}, follow_redirects=False)
            assert activated.status_code == 303
            assert is_enabled(db, real.id)
            assert not is_enabled(db, demo.id)
            assert list(db.scalars(select(Task))) == []
            assert "Отключить SEO-завод" in client.get(base).text
            client.post(base + "/activation", data={"action": "disable"}, follow_redirects=False)
            assert not is_enabled(db, real.id)
    finally:
        app.dependency_overrides.clear()
