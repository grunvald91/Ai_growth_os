import json

import pytest
from sqlalchemy import select

from app.content.viral_research import (
    ViralVideoCandidate,
    ViralVideoResearch,
    build_search_queries,
    parse_view_count,
    parse_youtube_search_page,
    run_viral_video_research,
)
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus
from app.privacy import PrivacyGateway


def test_parse_view_count_russian_and_compact():
    assert parse_view_count("1,2 млн просмотров") == 1_200_000
    assert parse_view_count("845 тыс. просмотров") == 845_000
    assert parse_view_count("2.5M views") == 2_500_000
    assert parse_view_count("нет данных") is None


def test_parse_youtube_search_page_extracts_verifiable_candidate():
    initial = {
        "contents": [{
            "videoRenderer": {
                "videoId": "AbCdEf12345",
                "title": {"runs": [{"text": "5 ошибок записи клиентов"}]},
                "ownerText": {"runs": [{"text": "Studio Lab"}]},
                "shortViewCountText": {"simpleText": "1,4 млн просмотров"},
                "publishedTimeText": {"simpleText": "2 месяца назад"},
                "lengthText": {"simpleText": "0:43"},
            }
        }]
    }
    page = "<script>var ytInitialData = " + json.dumps(initial, ensure_ascii=False) + ";</script>"
    rows = parse_youtube_search_page(page, "салон запись shorts")
    assert len(rows) == 1
    row = rows[0]
    assert row.source_id == "yt:AbCdEf12345"
    assert row.view_count == 1_400_000
    assert row.is_short is True
    assert "/shorts/" in str(row.url)


def test_search_queries_are_niche_specific():
    context = {"company": {"profile": {
        "identity": {"name": "Demo"},
        "offer": {"category": "массажный салон", "products_services": []},
        "customers": {"primary_segments": ["клиенты массажа"]},
    }}}
    niche, queries = build_search_queries(context, 4)
    assert niche == "массажный салон"
    assert len(queries) == 4
    assert any("массажный салон" in query for query in queries)
    assert any("клиенты массажа" in query for query in queries)


class FakeScout:
    async def collect(self, queries, max_candidates=16):
        return [
            ViralVideoCandidate(
                source_id="yt:one1234",
                url="https://www.youtube.com/shorts/one1234",
                query=queries[0],
                title="Почему клиент не записывается после первого сообщения",
                channel="A",
                view_count=1_200_000,
                view_count_text="1,2 млн просмотров",
                is_short=True,
                transcript_excerpt="Клиент спрашивает цену, потом время, потом снова ждёт ответа.",
            ),
            ViralVideoCandidate(
                source_id="yt:two1234",
                url="https://www.youtube.com/shorts/two1234",
                query=queries[1],
                title="Три ошибки администратора салона",
                channel="B",
                view_count=780_000,
                view_count_text="780 тыс. просмотров",
                is_short=True,
                transcript_excerpt="Первая ошибка — заставлять клиента ждать ответа о свободном времени.",
            ),
            ViralVideoCandidate(
                source_id="yt:three1234",
                url="https://www.youtube.com/shorts/three1234",
                query=queries[2],
                title="Сколько сообщений нужно до записи",
                channel="C",
                view_count=510_000,
                view_count_text="510 тыс. просмотров",
                is_short=True,
            ),
        ]


@pytest.mark.asyncio
async def test_viral_research_persists_grounded_artifact_before_plan(db, tmp_path):
    product = Product(name="Demo", slug="demo")
    db.add(product)
    db.flush()
    task = Task(
        product_id=product.id,
        title="Редактор: контент-план",
        assigned_agent="editorial_planner",
        status=TaskStatus.RUNNING,
    )
    db.add(task)
    db.commit()

    analysis = {
        "summary": "В роликах повторяется боль ожидания ответа и длинной переписки перед записью.",
        "hook_patterns": [{
            "pattern": "Считать количество сообщений до целевого действия.",
            "why_it_may_work": "Быстро делает операционную боль измеримой и узнаваемой.",
            "source_ids": ["yt:one1234", "yt:three1234"],
        }],
        "topic_patterns": [{
            "pattern": "Ошибки администратора, из-за которых клиент ждёт.",
            "why_it_may_work": "Формат ошибки создаёт ясный конфликт и обещает практическую проверку.",
            "source_ids": ["yt:two1234"],
        }],
        "seo_topic_seeds": [
            "как сократить переписку при записи клиента",
            "ошибки онлайн-записи в салоне",
        ],
        "content_angles": ["Путь клиента как количество ручных уточнений до подтверждения."],
        "cautions": ["Не переносить просмотры ролика в обещание продаж."],
    }
    provider = FakeLLMProvider(LLMResponse(
        content=json.dumps(analysis, ensure_ascii=False),
        model="kimi-2.7",
        usage=LLMUsage(input_tokens=100, output_tokens=120),
    ))
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider)
    settings = Settings(
        products_root=tmp_path,
        llm_provider="xai",
        llm_model="kimi-2.7",
        viral_research_mode="youtube_public",
    )
    context = {"company": {"profile": {
        "identity": {"name": "Demo", "category": "салон красоты"},
        "offer": {"products_services": []},
        "customers": {"primary_segments": ["владельцы салонов"]},
    }}}

    artifact = await run_viral_video_research(
        db=db, settings=settings, llm=llm, task=task, context=context, scout=FakeScout()
    )

    stored = ViralVideoResearch.model_validate_json(artifact.content)
    assert stored.source_status == "evidence_available"
    assert stored.analysis_status == "completed"
    assert len(stored.candidates) == 3
    assert stored.hook_patterns[0].source_ids == ["yt:one1234", "yt:three1234"]
    run = db.scalar(select(AgentRun).where(AgentRun.agent_name == "viral_video_researcher"))
    assert run.status == "completed"
    assert run.output_references == [{"kind": "artifact", "id": str(artifact.id)}]
    assert db.scalar(select(CostRecord).where(CostRecord.agent_run_id == run.id)) is not None
    assert db.scalar(select(Artifact).where(Artifact.kind == "viral_video_research")) is not None
