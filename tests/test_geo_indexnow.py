"""Offline IndexNow outbox contract: no network unless owner enables key."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.seo.indexnow_outbox import deliver_one, enqueue_published_article


class FakeResponse:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


class FakeIndexNow:
    def __init__(self, key, status=202, verified=True):
        self.key = key
        self.status = status
        self.verified = verified
        self.calls = []

    def get(self, url):
        self.calls.append(("GET", url))
        return FakeResponse(200 if self.verified else 404,
                            self.key if self.verified else "")

    def post(self, url, json):
        self.calls.append(("POST", url, json))
        return FakeResponse(self.status)


@pytest.fixture
def scenario():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        servgo = Product(name="ServGo", slug="servgo")
        tenant = Product(name="Local Company", slug="local-company")
        db.add_all([servgo, tenant])
        db.flush()

        def new_published(product, slug):
            article = Artifact(
                product_id=product.id, kind="article",
                title="Published article", content="{}",
                status=ArtifactStatus.PUBLISHED,
            )
            db.add(article)
            db.flush()
            db.add(Publication(
                product_id=product.id, artifact_id=article.id,
                channel="own_site", status="published",
                external_reference=f"/blog/{slug}",
                published_at=datetime.now(timezone.utc),
            ))
            db.commit()
            return article

        yield db, servgo, tenant, new_published
    engine.dispose()


def _notice(db):
    return db.scalar(select(Artifact).where(Artifact.kind == "geo_indexnow_outbox"))


def test_transactional_servgo_publication_event_is_idempotent(scenario):
    db, servgo, tenant, make_article = scenario
    source = make_article(servgo, "zapis-klientov")
    assert enqueue_published_article(db, source, "zapis-klientov")
    assert not enqueue_published_article(db, source, "zapis-klientov")
    db.commit()
    notice = _notice(db)
    event = json.loads(notice.content)
    assert event["article_id"] == str(source.id)
    assert event["url"] == "https://servgo.ru/blog/zapis-klientov"
    assert event["state"] == "pending"
    assert not enqueue_published_article(db, make_article(tenant, "local-news"), "local-news")
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.kind == "geo_indexnow_outbox")))) == 1


def test_disabled_key_never_makes_request_or_changes_queue(scenario):
    db, servgo, _, make_article = scenario
    enqueue_published_article(db, make_article(servgo, "zapis-klientov"), "zapis-klientov")
    db.commit()
    fake = FakeIndexNow("ab123456")
    assert deliver_one(db, key=None, client=fake) == 0
    assert fake.calls == []
    assert json.loads(_notice(db).content)["state"] == "pending"


def test_verified_public_key_then_acceptance_is_not_indexing_evidence(scenario):
    db, servgo, _, make_article = scenario
    enqueue_published_article(db, make_article(servgo, "zapis-klientov"), "zapis-klientov")
    db.commit()
    fake = FakeIndexNow("ab123456", status=202)
    assert deliver_one(db, key="ab123456", client=fake) == 1
    state = json.loads(_notice(db).content)
    assert state["state"] == "accepted_by_endpoint"
    assert state["last_http_status"] == 202
    assert len(fake.calls) == 2
    assert fake.calls[1][2]["urlList"] == ["https://servgo.ru/blog/zapis-klientov"]
    assert deliver_one(db, key="ab123456", client=fake) == 0
    assert len(fake.calls) == 2


def test_unverified_domain_key_never_submits_and_can_be_retried(scenario):
    db, servgo, _, make_article = scenario
    enqueue_published_article(db, make_article(servgo, "zapis-klientov"), "zapis-klientov")
    db.commit()
    fake = FakeIndexNow("ab123456", verified=False)
    assert deliver_one(db, key="ab123456", client=fake) == 0
    assert [x[0] for x in fake.calls] == ["GET"]
    state = json.loads(_notice(db).content)
    assert state["attempts"] == 0
    assert state["next_attempt_at"] is not None
    assert deliver_one(db, key="ab123456", client=FakeIndexNow("ab123456")) == 0
    from datetime import timedelta
    recovered_at = datetime.fromisoformat(state["next_attempt_at"]) + timedelta(minutes=1)
    assert deliver_one(db, key="ab123456", client=FakeIndexNow("ab123456"), now=recovered_at) == 1


def test_transient_failure_has_backoff_and_technical_retries_are_bounded(scenario):
    db, servgo, _, make_article = scenario
    enqueue_published_article(db, make_article(servgo, "zapis-klientov"), "zapis-klientov")
    db.commit()
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    fake = FakeIndexNow("ab123456", status=429)
    assert deliver_one(db, key="ab123456", client=fake, now=now) == 1
    state = json.loads(_notice(db).content)
    assert state["attempts"] == 1
    assert state["state"] == "pending"
    assert deliver_one(db, key="ab123456", client=fake, now=now) == 0
    for i in range(2, 6):
        state["next_attempt_at"] = None
        _notice(db).content = json.dumps(state)
        db.commit()
        assert deliver_one(db, key="ab123456", client=fake, now=now) == 1
        state = json.loads(_notice(db).content)
        assert state["attempts"] == i
    assert state["state"] == "failed_after_retries"
    assert deliver_one(db, key="ab123456", client=fake, now=now) == 0


def test_invalid_url_and_unpublished_article_never_post(scenario):
    db, servgo, _, make_article = scenario
    article = make_article(servgo, "zapis-klientov")
    enqueue_published_article(db, article, "zapis-klientov")
    db.commit()
    article.status = ArtifactStatus.DRAFT
    db.commit()
    fake = FakeIndexNow("ab123456")
    assert deliver_one(db, key="ab123456", client=fake) == 1
    assert fake.calls == []
    assert json.loads(_notice(db).content)["state"] == "skipped_unpublished"


def test_rejects_bad_key_and_cross_domain_payload(scenario):
    db, servgo, _, make_article = scenario
    article = make_article(servgo, "zapis-klientov")
    enqueue_published_article(db, article, "zapis-klientov")
    db.commit()
    fake = FakeIndexNow("ab123456")
    with pytest.raises(ValueError):
        deliver_one(db, key="bad key!", client=fake)
    notice = _notice(db)
    state = json.loads(notice.content)
    state["url"] = "https://attacker.invalid/private"
    notice.content = json.dumps(state)
    db.commit()
    assert deliver_one(db, key="ab123456", client=fake) == 1
    assert fake.calls == []
    assert json.loads(_notice(db).content)["state"] == "rejected"
