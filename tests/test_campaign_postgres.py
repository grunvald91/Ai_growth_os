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
