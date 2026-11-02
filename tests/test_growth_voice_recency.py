"""Newest ready/work items must appear nearest the bottom dispatcher composer."""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.content_studio_activity import notice_items
from app.dispatcher import executors
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus


@pytest.mark.asyncio
async def test_work_items_are_chronological_from_top_to_bottom(db):
    product = Product(name="Chronological", slug="chronological")
    db.add(product)
    db.flush()
    now = datetime.now(timezone.utc)
    older = Task(product_id=product.id, title="POST: Старая тема",
                 assigned_agent="content_factory", status=TaskStatus.RUNNING,
                 created_at=now - timedelta(hours=2))
    newer = Task(product_id=product.id, title="POST: Новая тема",
                 assigned_agent="content_factory", status=TaskStatus.RUNNING,
                 created_at=now - timedelta(hours=1))
    db.add_all([newer, older])
    db.commit()
    result = await executors.show_work(executors.Context(db=db, product=product,
                                                        settings=object(), account="owner"), None)
    table = next(block for block in result.blocks if block["type"] == "table")
    links = next(block for block in result.blocks if block["type"] == "links")
    assert "Старая тема" in table["rows"][0][0]
    assert "Новая тема" in table["rows"][-1][0]
    assert "Старая тема" in links["items"][0]["label"]
    assert "Новая тема" in links["items"][-1]["label"]


@pytest.mark.asyncio
async def test_ready_and_failed_events_are_globally_chronological(db):
    product = Product(name="Ready timeline", slug="ready-timeline")
    db.add(product)
    db.flush()
    now = datetime.now(timezone.utc)
    old = Artifact(product_id=product.id, kind="article", title="Старый материал",
                   status=ArtifactStatus.DRAFT, content='{"body":"Текст"}',
                   created_at=now - timedelta(hours=3))
    middle = Task(product_id=product.id, title="POST: Неудача",
                  assigned_agent="content_factory", status=TaskStatus.FAILED,
                  created_at=now - timedelta(hours=2))
    recent = Artifact(product_id=product.id, kind="article", title="Свежий материал",
                      status=ArtifactStatus.AWAITING_APPROVAL, content='{"body":"Текст"}',
                      created_at=now - timedelta(hours=1))
    db.add_all([recent, middle, old])
    db.commit()
    notices = notice_items(db, product.id)
    ids = [note["id"] for note in notices]
    assert ids.index(str(recent.id)) < ids.index(str(middle.id)) < ids.index(str(old.id))
    result = await executors.show_review(executors.Context(db=db, product=product,
                                                          settings=object(), account="owner"), None)
    # The dispatcher now uses a compact resource list plus a typed approval
    # card for the newest material, not an additional wall of duplicate links.
    resources = next(block for block in result.blocks if block["type"] == "resource_list")["items"]
    assert "Старый материал" in resources[0]["title"]
    assert "Неудача" in resources[-1]["title"]
    assert result.blocks[-2]["type"] == "material"
    assert "Свежий материал" in str(result.blocks[-2])
    assert result.blocks[-1]["type"] == "chips"
