"""Watchdog observes evidence, persists alert claims and never starts inference."""
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import select

from app.models.domain import Artifact, ArtifactStatus, Publication, Task, TaskStatus
from app.seo.activation import SeoFactoryActivation, set_enabled
from app.seo.watchdog import KIND, acknowledge, inspect_and_claim, observe
from test_seo_factory import env  # noqa: F401

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


def enabled_settings(settings):
    return settings.model_copy(update={"seo_watchdog_enabled": True, "seo_autorun_enabled": True})


def test_previous_full_local_day_requires_consistent_seo_receipt(env):
    factory, defaults, company = env
    settings = enabled_settings(defaults)
    with factory() as db:
        from app.models.domain import Product
        product = db.get(Product, company)
        activation = db.get(SeoFactoryActivation, company)
        activation.changed_at = NOW - timedelta(days=3)
        db.commit()
        assert observe(db, product, activation, settings, NOW)["missed_daily_publication"] == 1
        job = Task(product_id=company, title="SEO-ARTICLE:example:a001",
                   assigned_agent="content_factory", status=TaskStatus.COMPLETED)
        db.add(job)
        db.flush()
        article = Artifact(product_id=company, task_id=job.id, kind="article",
            title="Article", content="{}", status=ArtifactStatus.PUBLISHED)
        db.add(article)
        db.flush()
        receipt = Publication(product_id=company, artifact_id=article.id, channel="own_site",
                              status="published", published_at=NOW - timedelta(days=1))
        db.add(receipt)
        db.commit()
        assert "missed_daily_publication" not in observe(db, product, activation, settings, NOW)
        receipt.status = "unpublished"
        db.commit()
        issues = observe(db, product, activation, settings, NOW)
        assert issues["missed_daily_publication"] == 1
        assert issues["publication_mismatch"] == 1


def test_claim_is_durable_and_alerts_throttled_across_sessions(env, monkeypatch):
    factory, defaults, company = env
    settings = enabled_settings(defaults)
    import app.seo.watchdog as watchdog
    monkeypatch.setattr(watchdog, "observe", lambda *a: {"publication_mismatch": 1})
    with factory() as db:
        assert inspect_and_claim(db, company, settings, now=NOW, can_notify=True) is None
    with factory() as db:
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(minutes=15),
                                 can_notify=True) is None
        claim = inspect_and_claim(db, company, settings, now=NOW + timedelta(minutes=30),
                                  can_notify=True)
        assert claim and claim["issues"] == {"publication_mismatch": 1}
    with factory() as db:
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=1),
                                 can_notify=True) is None
        acknowledge(db, claim, now=NOW + timedelta(minutes=31))
    with factory() as db:
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=23),
                                 can_notify=True) is None
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=25),
                                 can_notify=True)
        assert len(list(db.scalars(select(Artifact).where(Artifact.product_id == company,
                                                           Artifact.kind == KIND)))) == 1


def test_transient_shortage_has_day_grace_and_recovers_without_alert(env, monkeypatch):
    factory, defaults, company = env
    settings = enabled_settings(defaults)
    import app.seo.watchdog as watchdog
    issues = {"reserve_shortage": 7, "model_wait": 1}
    monkeypatch.setattr(watchdog, "observe", lambda *a: dict(issues))
    with factory() as db:
        assert inspect_and_claim(db, company, settings, now=NOW, can_notify=True) is None
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=23),
                                 can_notify=True) is None
        issues.clear()
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=24),
                                 can_notify=True) is None
        state = json.loads(db.scalar(select(Artifact).where(Artifact.kind == KIND)).content)
        assert not state["issues"] and not state["since"]
        issues["reserve_shortage"] = 1
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=25),
                                 can_notify=True) is None
        set_enabled(db, company, enabled=False, actor="test-owner")
        assert inspect_and_claim(db, company, settings, now=NOW + timedelta(hours=50),
                                 can_notify=True) is None


def test_overdue_model_check_is_not_reported_as_provider_outage(env):
    from app.models.domain import AgentRun, Product
    factory, defaults, company = env
    settings = enabled_settings(defaults)
    with factory() as db:
        product = db.get(Product, company)
        activation = db.get(SeoFactoryActivation, company)
        task = Task(product_id=company, title="SEO-ARTICLE:example:a001",
                    assigned_agent="content_factory", status=TaskStatus.QUEUED)
        db.add(task)
        db.flush()
        old = NOW - timedelta(hours=1)
        db.add(Artifact(product_id=company, task_id=task.id, kind="seo_model_wait",
                        title=old.isoformat(), content="{}"))
        db.commit()
        issues = observe(db, product, activation, settings, NOW)
        assert issues["model_check_overdue"] == 1
        assert "model_wait" not in issues
        db.add(AgentRun(task_id=task.id, agent_name="article_writer",
                        status="failed", started_at=old + timedelta(minutes=5)))
        db.commit()
        assert "model_check_overdue" not in observe(db, product, activation, settings, NOW)
        db.add(Artifact(product_id=company, task_id=task.id, kind="seo_model_wait",
                        title=(NOW + timedelta(minutes=5)).isoformat(), content="{}"))
        db.commit()
        issues = observe(db, product, activation, settings, NOW)
        assert issues["model_wait"] == 1
        assert "model_check_overdue" not in issues
