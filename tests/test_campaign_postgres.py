"""PostgreSQL row-lock integration for campaign stop vs. concurrent workers.

Runs only with a disposable CAMPAIGN_PG_TEST_URL. It never connects to ServGo
production, creates external messages, or makes network calls to providers.
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.models.domain import Artifact, Experiment, Product, Prospect, Publication, ToolCall
from app.services.campaign_lifecycle import (
    command, closing_campaign_for_material, reconcile_pending,
)
from app.services.campaign_readiness import link_resource


@pytest.fixture(scope="module")
def pg_sessions():
    url = os.environ.get("CAMPAIGN_PG_TEST_URL")
    if not url:
        pytest.skip("PostgreSQL integration requires CAMPAIGN_PG_TEST_URL")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("A disposable psycopg PostgreSQL database is required")
    engine = create_engine(url, pool_pre_ping=True, pool_timeout=5)
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _company(session, *, slug):
    company = Product(name="PG concurrent campaign", slug=f"{slug}-{uuid4().hex[:12]}")
    session.add(company)
    session.flush()
    experiment = Experiment(
        product_id=company.id, experiment_key=f"EXP-PG-{uuid4().hex[:12]}",
        name="Concurrency test", hypothesis="Check real row locks", status="running",
    )
    session.add(experiment)
    session.commit()
    return company.id, experiment.id


def test_smtp_reservation_commits_before_stop_and_stop_waits(pg_sessions):
    Session = pg_sessions
    with Session() as setup:
        company_id, experiment_id = _company(setup, slug="pg-smtp")
        prospect = Prospect(product_id=company_id, experiment_id=experiment_id,
                            lead_key="pg-contact", business_name="Test contact")
        setup.add(prospect)
        setup.commit()
        prospect_id = prospect.id

    started = threading.Event()

    def stop_on_second_connection():
        with Session() as session:
            started.set()
            return command(session, product_id=company_id,
                           experiment_id=experiment_id, action="stop",
                           actor="pg-test:owner")

    with Session() as sender, ThreadPoolExecutor(max_workers=1) as pool:
        row = sender.scalar(select(Experiment).where(
            Experiment.id == experiment_id,
        ).with_for_update())
        assert row.status == "running"
        pending_stop = pool.submit(stop_on_second_connection)
        assert started.wait(3)
        assert not pending_stop.done()  # stop cannot pass an exclusive row lock
        sender.add(ToolCall(
            product_id=company_id, operation="send_outreach_message",
            tool_name="smtp", status="sending",
            request_data={"prospect_id": str(prospect_id)},
        ))
        sender.commit()
        status, blockers = pending_stop.result(timeout=10)
    assert status == "stopping"
    assert blockers.sending_emails == 1

    with Session() as session:
        call = session.scalar(select(ToolCall).where(
            ToolCall.product_id == company_id,
            ToolCall.operation == "send_outreach_message",
        ))
        call.status = "smtp_accepted"
        session.commit()
        assert reconcile_pending(session) == 1
        assert session.get(Experiment, experiment_id).status == "stopped"


def test_later_material_link_cannot_overwrite_concurrent_link(pg_sessions):
    Session = pg_sessions
    with Session() as setup:
        company_id, experiment_id = _company(setup, slug="pg-link")
        first = Artifact(product_id=company_id, kind="channel_text",
                         title="First post", content="{}")
        second = Artifact(product_id=company_id, kind="channel_text",
                          title="Second post", content="{}")
        setup.add_all([first, second])
        setup.commit()
        first_id, second_id = first.id, second.id

    waiting = threading.Event()
    def link_second():
        with Session() as session:
            waiting.set()
            link_resource(session, company_id, experiment_id, second_id,
                          relation="material")

    with Session() as owner, ThreadPoolExecutor(max_workers=1) as pool:
        owner.scalar(select(Experiment).where(
            Experiment.id == experiment_id,
        ).with_for_update())
        future = pool.submit(link_second)
        assert waiting.wait(3)
        link_resource(owner, company_id, experiment_id, first_id,
                      relation="material")
        future.result(timeout=10)

    with Session() as session:
        workspaces = list(session.scalars(select(Artifact).where(
            Artifact.product_id == company_id,
            Artifact.kind == "campaign_workspace",
        )))
        assert len(workspaces) == 1
        payload = json.loads(workspaces[0].content)
        assert set(payload["material_ids"]) == {str(first_id), str(second_id)}


def test_stop_serializes_with_telegram_send_lease(pg_sessions):
    Session = pg_sessions
    with Session() as setup:
        company_id, experiment_id = _company(setup, slug="pg-telegram")
        material = Artifact(product_id=company_id, kind="channel_text",
                            title="Scheduled marketing post",
                            content='{"channel":"telegram","body":"No external call"}')
        setup.add(material)
        setup.commit()
        material_id = material.id
        link_resource(setup, company_id, experiment_id, material_id,
                      relation="material")
        pub = Publication(product_id=company_id, artifact_id=material_id,
                          channel="telegram", status="scheduled")
        setup.add(pub)
        setup.commit()
        pub_id = pub.id

    started = threading.Event()
    def stop_from_owner():
        with Session() as session:
            started.set()
            return command(session, product_id=company_id,
                           experiment_id=experiment_id, action="stop",
                           actor="pg-test:owner")

    with Session() as sender, ThreadPoolExecutor(max_workers=1) as pool:
        # The real Telegram publisher calls this gate before its durable lease.
        assert closing_campaign_for_material(sender, company_id, material_id) is False
        future = pool.submit(stop_from_owner)
        assert started.wait(3)
        assert not future.done()
        claimed = sender.execute(update(Publication).where(
            Publication.id == pub_id, Publication.status == "scheduled",
        ).values(status="sending"))
        assert claimed.rowcount == 1
        sender.commit()
        status, blockers = future.result(timeout=10)

    assert status == "stopping"
    assert blockers.pending_publications == 1
    with Session() as session:
        assert session.get(Publication, pub_id).status == "sending"
        session.scalar(select(Experiment).where(
            Experiment.id == experiment_id,
        ).with_for_update())
        assert closing_campaign_for_material(session, company_id, material_id) is True
        session.rollback()
        pub = session.get(Publication, pub_id)
        pub.status = "delivery_unknown"
        session.commit()
        assert reconcile_pending(session) == 0
        pub.status = "published"
        session.commit()
        assert reconcile_pending(session) == 1
        assert session.get(Experiment, experiment_id).status == "stopped"


def test_preloaded_workspace_preserves_concurrent_material_links(pg_sessions):
    Session = pg_sessions
    with Session() as setup:
        company, campaign = _company(setup, slug="pg-stale-workspace")
        ids = []
        for name in ("A", "B", "C"):
            item = Artifact(product_id=company, kind="channel_text",
                            title=name, content="{}")
            setup.add(item)
            setup.flush()
            ids.append(item.id)
        setup.commit()
        link_resource(setup, company, campaign, ids[0], relation="material")

    with Session() as stale:
        cached = stale.scalar(select(Artifact).where(
            Artifact.product_id == company, Artifact.kind == "campaign_workspace",
        ))
        assert str(ids[0]) in cached.content
        with Session() as concurrent:
            link_resource(concurrent, company, campaign, ids[1], relation="material")
        link_resource(stale, company, campaign, ids[2], relation="material")

    with Session() as verify:
        workspace = verify.scalar(select(Artifact).where(
            Artifact.product_id == company, Artifact.kind == "campaign_workspace",
        ))
        assert set(json.loads(workspace.content)["material_ids"]) == {
            str(key) for key in ids
        }


def test_locked_campaign_does_not_stall_another_company_reconcile(pg_sessions, monkeypatch):
    from app.services import campaign_lifecycle
    monkeypatch.setattr(campaign_lifecycle, "_RECONCILE_CURSOR", None)
    Session = pg_sessions
    with Session() as setup:
        first_product, first = _company(setup, slug="pg-locked-tenant")
        second_product, second = _company(setup, slug="pg-free-tenant")
        setup.get(Experiment, first).status = "stopping"
        setup.get(Experiment, second).status = "finishing"
        setup.commit()

    with Session() as locked, ThreadPoolExecutor(max_workers=1) as pool:
        locked.scalar(select(Experiment).where(
            Experiment.id == first,
        ).with_for_update())
        def reconcile():
            with Session() as concurrent:
                return reconcile_pending(concurrent, limit=500)
        result = pool.submit(reconcile).result(timeout=8)
        assert result >= 1
        with Session() as verify:
            assert verify.get(Experiment, second).status == "completed"
            assert verify.get(Experiment, first).status == "stopping"
        locked.rollback()


def test_unrelated_campaign_lock_does_not_block_material_gate(pg_sessions):
    Session = pg_sessions
    with Session() as setup:
        company, related = _company(setup, slug="pg-material-gate")
        unrelated = Experiment(product_id=company, experiment_key="EXP-PG-OTHER-" + uuid4().hex[:12],
                               name="Unrelated", hypothesis="Check", status="running")
        source = Artifact(product_id=company, kind="channel_text",
                          title="Linked material", content="{}")
        setup.add_all([unrelated, source])
        setup.commit()
        link_resource(setup, company, related, source.id, relation="material")
        unrelated_id, material_id = unrelated.id, source.id

    with Session() as locked, ThreadPoolExecutor(max_workers=1) as pool:
        locked.scalar(select(Experiment).where(
            Experiment.id == unrelated_id,
        ).with_for_update())
        def gate():
            from sqlalchemy import text as sql_text
            with Session() as independent:
                independent.execute(sql_text("SET LOCAL lock_timeout = '500ms'"))
                return closing_campaign_for_material(independent, company, material_id)
        assert pool.submit(gate).result(timeout=5) is False
        locked.rollback()


def test_actual_schedule_does_not_deadlock_with_stop(pg_sessions, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import app.content.telegram_publishing as telegram
    Session = pg_sessions
    with Session() as setup:
        company, campaign = _company(setup, slug="pg-actual-schedule")
        source = Artifact(product_id=company, kind="channel_text",
                          title="Campaign-linked post",
                          content=json.dumps({"channel": "telegram", "body": "A verified post for subscribers"}),
                          status="approved")
        channel = Artifact(product_id=company, kind="telegram_channel_connection",
                           title="Connected", content=json.dumps({"chat_id": -10012345}),
                           status="approved")
        setup.add_all([source, channel])
        setup.commit()
        post_id, channel_id = source.id, channel.id
        link_resource(setup, company, campaign, post_id, relation="material")
    entered = threading.Event()
    release = threading.Event()
    real_gate = telegram.closing_campaign_for_material

    def pause_after_product_and_artifact_locks(db, product_id, artifact_id, **kwargs):
        entered.set()
        assert release.wait(8)
        return real_gate(db, product_id, artifact_id, **kwargs)

    monkeypatch.setattr(telegram, "closing_campaign_for_material", pause_after_product_and_artifact_locks)

    def schedule():
        with Session() as session:
            try:
                telegram.schedule_post(
                    session, post_id, channel_id,
                    datetime.now(timezone.utc) + timedelta(hours=2),
                )
                return "scheduled"
            except ValueError:
                session.rollback()
                return "blocked_after_stop"

    def stop():
        with Session() as session:
            return command(session, product_id=company, experiment_id=campaign,
                           action="stop", actor="pg-audit")

    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled = pool.submit(schedule)
        assert entered.wait(5)
        stopped = pool.submit(stop)
        try:
            assert stopped.result(timeout=5)[0] == "stopped"
        finally:
            release.set()
        assert scheduled.result(timeout=8) == "blocked_after_stop"
