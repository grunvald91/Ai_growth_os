"""Semantic core: suggestions are collected, clustered by intent and mapped to funnel stages; failures stay honest."""
import json

import httpx
import pytest

from app.models.domain import Product
from app.seo.clustering import classify_intent, cluster_queries, normalize
from app.seo.service import build_semantic_core, latest_semantic_core
from app.seo.suggest import expand_seed, parse_suggestions


def _product(db):
    product = Product(name="ServGo", slug="servgo")
    db.add(product)
    db.commit()
    return product


def test_parse_handles_both_shapes_and_garbage():
    assert parse_suggestions(json.dumps(["q", ["a", "b"]])) == ["a", "b"]
    assert parse_suggestions(json.dumps(["q", [["a", {"k": 1}], "b"]])) == ["a", "b"]
    assert parse_suggestions("<html>captcha</html>") == []
    assert parse_suggestions(json.dumps({"x": 1})) == []


def test_expansion_covers_questions_suffixes_and_letters_without_duplicates():
    probes = expand_seed("онлайн запись клиентов")
    assert probes[0] == "онлайн запись клиентов"
    assert "как онлайн запись клиентов" in probes and "онлайн запись клиентов цена" in probes
    assert len(probes) == len(set(probes)) > 40


@pytest.mark.parametrize("query,intent", [
    ("как вести запись клиентов", "informational"),
    ("программа для записи клиентов цена", "commercial"),
    ("лучшие сервисы онлайн записи", "commercial"),
    ("подключить онлайн запись", "transactional"),
])
def test_intents(query, intent):
    assert classify_intent(query) == intent


def test_clusters_group_word_forms_and_never_mix_intents():
    clusters = cluster_queries({
        "yandex": {"программа для записи клиентов", "программы для записи клиентов", "запись клиентов цена"},
        "google": {"программа для записи клиентов"},
    })
    heads = {c.head_query: c for c in clusters}
    program = next(c for c in clusters if "программ" in c.head_query)
    assert len(program.queries) == 2 and program.engines == ["google", "yandex"]
    assert program.stage == "awareness"
    price = next(c for c in clusters if "цена" in c.head_query)
    assert price.intent == "commercial" and price.stage == "consideration"
    assert len(heads) == len(clusters)


def test_normalize():
    assert normalize("Ёлка,  Цена!") == "елка цена"


def _mock(fail_google=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if "google" in request.url.host and fail_google:
            return httpx.Response(503)
        part = request.url.params.get("part") or request.url.params.get("q")
        return httpx.Response(200, text=json.dumps([part, [f"{part} цена", f"{part} отзывы"]]))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_build_saves_artifact_with_source_status(db):
    product = _product(db)
    artifact = await build_semantic_core(db, product.id, ["запись клиентов"], client=_mock())
    core = latest_semantic_core(db, product.id)
    assert artifact.kind == "semantic_core" and core.demand_data == "none"
    assert {s.engine for s in core.sources} == {"yandex", "google"}
    assert all(s.failed_requests == 0 for s in core.sources)
    assert core.clusters


@pytest.mark.asyncio
async def test_one_engine_down_is_reported_not_hidden(db):
    product = _product(db)
    await build_semantic_core(db, product.id, ["запись клиентов"], client=_mock(fail_google=True))
    core = latest_semantic_core(db, product.id)
    google = next(s for s in core.sources if s.engine == "google")
    assert google.failed_requests == google.requests > 0 and google.queries_found == 0
    assert all(c.engines == ["yandex"] for c in core.clusters)


@pytest.mark.asyncio
async def test_all_engines_down_builds_nothing(db):
    product = _product(db)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(RuntimeError):
        await build_semantic_core(db, product.id, ["запись клиентов"], client=client)
    assert latest_semantic_core(db, product.id) is None
