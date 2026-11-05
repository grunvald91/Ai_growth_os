"""Historical collected search queries survive later semantic-core refreshes."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.domain import Artifact, Product
from app.seo.contracts import QueryCluster, SemanticCore, SourceStatus
from app.seo import revise_servgo_owner_articles as release


def _core(query: str, *, found: int = 1, engines=None) -> SemanticCore:
    return SemanticCore(
        seeds=["запись клиентов"], collected_at=datetime.now(timezone.utc),
        sources=[SourceStatus(engine="google", requests=1, failed_requests=0,
                              queries_found=found)],
        clusters=[QueryCluster(id="c001", head_query=query, stage="consideration",
                               intent="commercial", queries=[query],
                               engines=engines or ["google"])],
    )


def test_historical_verified_core_resolves_after_new_core_replaces_it(db, monkeypatch):
    company = Product(name="ServGo", slug="servgo")
    db.add(company)
    db.flush()
    old = _core("как организовать запись клиентов")
    latest = _core("новая тема для семантического ядра")
    db.add(Artifact(product_id=company.id, kind="semantic_core", title="Historical",
                    content=old.model_dump_json(),
                    created_at=datetime.now(timezone.utc) - timedelta(days=2)))
    db.commit()
    monkeypatch.setattr(release, "latest_semantic_core", lambda d, pid: latest)
    cluster = release._query_in_collected_core(db, company.id, "как организовать запись клиентов")
    assert cluster.queries == ["как организовать запись клиентов"]


def test_historical_provenance_never_uses_another_company(db, monkeypatch):
    owner = Product(name="ServGo", slug="servgo")
    other = Product(name="Demo", slug="demo")
    db.add_all([owner, other])
    db.flush()
    db.add(Artifact(product_id=other.id, kind="semantic_core", title="Other tenant",
                    content=_core("как организовать запись клиентов").model_dump_json()))
    db.commit()
    monkeypatch.setattr(release, "latest_semantic_core",
                        lambda d, pid: _core("другая проверенная тема"))
    with pytest.raises(ValueError, match="saved collected cluster"):
        release._query_in_collected_core(db, owner.id, "как организовать запись клиентов")


def test_unverified_and_ambiguous_queries_still_fail(db, monkeypatch):
    owner = Product(name="ServGo", slug="servgo")
    db.add(owner)
    db.flush()
    missing_evidence = _core("как организовать запись клиентов", found=0)
    db.add(Artifact(product_id=owner.id, kind="semantic_core", title="Incomplete",
                    content=missing_evidence.model_dump_json()))
    db.commit()
    monkeypatch.setattr(release, "latest_semantic_core", lambda d, pid: None)
    with pytest.raises(ValueError, match="no recorded search source"):
        release._query_in_collected_core(db, owner.id, "как организовать запись клиентов")

    ambiguous = _core("как организовать запись клиентов")
    ambiguous.clusters.append(QueryCluster(
        id="c002", head_query="как организовать запись клиентов",
        intent="commercial", stage="consideration",
        queries=["как организовать запись клиентов"], engines=["google"]))
    monkeypatch.setattr(release, "latest_semantic_core", lambda d, pid: ambiguous)
    with pytest.raises(ValueError, match="multiple collected clusters"):
        release._query_in_collected_core(db, owner.id, "как организовать запись клиентов")
