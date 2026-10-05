import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.content.viral_content_research import (
    ArticleCandidate,
    RankedVideoCandidate,
    ViralContentResearch,
    run_viral_content_research,
)
from app.content.viral_research import ViralVideoCandidate
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus
from app.privacy import PrivacyGateway


class FakeVideoScout:
    async def collect(self, queries, max_candidates=16):
        return [
            ViralVideoCandidate(
                source_id="yt:one1234",
                url="https://www.youtube.com/shorts/one1234",
                query=queries[0],
                title="Три ошибки записи в салоне",
                channel="Studio A",
                view_count=900_000,
                view_count_text="900 тыс. просмотров",
                is_short=True,
                transcript_excerpt="Клиент ждёт, пока администратор уточняет услугу, мастера и время.",
            ),
            ViralVideoCandidate(
                source_id="yt:two1234",
                url="https://www.youtube.com/shorts/two1234",
                query=queries[0],
                title="Почему клиент пропадает после вопроса о времени",
                channel="Studio B",
                view_count=600_000,
                view_count_text="600 тыс. просмотров",
                is_short=True,
            ),
            ViralVideoCandidate(
                source_id="yt:three1234",
                url="https://www.youtube.com/shorts/three1234",
                query=queries[0],
                title="Когда запись превращается в чат на двадцать сообщений",
                channel="Studio C",
                view_count=500_000,
                view_count_text="500 тыс. просмотров",
                is_short=True,
            ),
        ]


class FakeArticleScout:
    async def collect(self, queries, max_candidates=16):
        now = datetime.now(timezone.utc)
        return [
            ArticleCandidate(
                source_id="article:1",
                url="https://example.com/a",
                query=queries[0],
                title="Как салону сократить ручную запись клиентов",
                publisher="Example A",
                published_at=now,
                age_days=0,
                snippet="Разбираем путь клиента и ручные уточнения.",
                cross_source_hits=3,
                trend_score=92,
                evidence_confidence="high",
            ),
            ArticleCandidate(
                source_id="article:2",
                url="https://example.org/b",
                query=queries[0],
                title="Онлайн-запись и ожидание ответа администратора",
                publisher="Example B",
                published_at=now,
                age_days=0,
                snippet="Клиенту важно быстро понять свободное время.",
                cross_source_hits=2,
                trend_score=81,
                evidence_confidence="medium",
            ),
            ArticleCandidate(
                source_id="article:3",
                url="https://example.net/c",
                query=queries[0],
                title="Почему длинная переписка мешает записи",
                publisher="Example C",
                published_at=now,
                age_days=0,
                snippet="Чем больше ручных шагов, тем сложнее путь до подтверждения.",
                cross_source_hits=2,
                trend_score=79,
                evidence_confidence="medium",
            ),
        ]


def ranked_videos(raw):
    return [
        RankedVideoCandidate(
            source_id=item.source_id,
            url=item.url,
            query=item.query,
            title=item.title,
            channel=item.channel,
            view_count=item.view_count,
            published_text=item.published_text,
            is_short=item.is_short,
            transcript_excerpt=item.transcript_excerpt,
            channel_id=f"channel-{index}",
            published_at=datetime.now(timezone.utc),
            age_days=2,
            channel_baseline_median_views=100_000,
            outlier_ratio=(item.view_count or 0) / 100_000,
            viral_score=90 - index,
            evidence_confidence="high",
        )
        for index, item in enumerate(raw, 1)
    ]


@pytest.mark.asyncio
async def test_multisource_research_maps_pain_to_verified_product_fact(db, tmp_path, monkeypatch):
    product = Product(name="ServGo", slug="servgo")
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

    async def fake_rank(raw, timeout_seconds=20.0):
        return ranked_videos(raw)

    monkeypatch.setattr("app.content.viral_content_research.rank_youtube_candidates", fake_rank)

    analysis = {
        "summary": "Повторяется конкретная боль: клиент и администратор тратят много ручных шагов до выбора времени.",
        "audience_pains": [
            {
                "pain": "Долгая переписка вокруг услуги, специалиста и свободного времени.",
                "source_ids": ["yt:one1234", "article:1"],
                "why_it_matters": "Клиенту приходится ждать и повторно уточнять детали.",
                "product_match": "verified_match",
                "product_fact_id": "profile_fact:1",
                "product_match_explanation": "Подтверждённый клиентский flow позволяет выбрать услугу, специалиста и слот.",
                "presentation_angle": "Показать путь из двадцати сообщений против понятного выбора шага за шагом.",
                "humor_angle": "Слегка обыграть чат, который внезапно стал второй CRM владельца.",
            },
            {
                "pain": "Владелец не видит простой последовательности от интереса до подтверждения.",
                "source_ids": ["yt:three1234", "article:3"],
                "why_it_matters": "Ручной процесс распадается на несвязанные уточнения.",
                "product_match": "hypothesis",
                "product_fact_id": None,
                "product_match_explanation": "Исследование показывает тему для проверки, но не доказывает бизнес-эффект.",
                "presentation_angle": "Разобрать путь клиента по шагам и убрать лишние развилки.",
                "humor_angle": "Сравнить запись с квестом, где приз — просто свободное окно.",
            },
        ],
        "hook_patterns": [
            {
                "pattern": "Считать ручные сообщения до целевого действия.",
                "why_it_may_work": "Делает скрытую операционную боль видимой.",
                "source_ids": ["yt:three1234"],
            },
            {
                "pattern": "Начинать с ошибки, которую владелец узнаёт сразу.",
                "why_it_may_work": "Создаёт мгновенное узнавание ситуации.",
                "source_ids": ["yt:one1234"],
            },
        ],
        "topic_patterns": [
            {
                "pattern": "Ошибки ручной записи.",
                "why_it_may_work": "Практичный диагностический формат.",
                "source_ids": ["yt:one1234", "article:1"],
            },
            {
                "pattern": "Ожидание ответа и потеря контекста.",
                "why_it_may_work": "Фокусируется на реальной сцене взаимодействия.",
                "source_ids": ["yt:two1234", "article:2"],
            },
        ],
        "article_patterns": [
            {
                "pattern": "Пошаговый разбор пути клиента с конкретными точками трения.",
                "why_it_may_work": "Даёт полезную SEO-структуру, а не рекламный текст.",
                "source_ids": ["article:1", "article:3"],
            }
        ],
        "stepps_mechanisms": ["Practical Value", "Stories"],
        "seo_topic_seeds": [
            "как сократить переписку при записи",
            "ошибки записи клиентов в салоне",
            "как клиент выбирает свободное время",
            "онлайн запись без ручных уточнений",
            "путь клиента от сообщения до подтверждения",
        ],
        "content_angles": [
            "Сколько сообщений нужно, чтобы записать одного клиента",
            "Где клиент застревает между вопросом и свободным окном",
            "Чат как самодельная CRM",
            "Путь клиента без квеста по времени",
            "Что можно отдать клиенту на самостоятельный выбор",
        ],
        "white_space_angles": [
            "Разобрать запись как UX-путь, а не как список функций.",
            "Сравнить ручной чат и структурированный выбор без обещаний роста.",
        ],
        "humor_angles": [
            "Чат, который незаметно устроился на работу администратором.",
            "Запись как квест, где босс — свободное окно на 18:30.",
        ],
        "counterexamples": ["Большой охват сам по себе не означает высокий спрос на сервис записи."],
        "cautions": ["Не переносить просмотры роликов в обещание продаж."],
    }
    provider = FakeLLMProvider(LLMResponse(
        content=json.dumps(analysis, ensure_ascii=False),
        model="kimi-2.7",
        usage=LLMUsage(input_tokens=220, output_tokens=330),
    ))
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider)
    settings = Settings(
        products_root=tmp_path,
        llm_provider="xai",
        llm_model="kimi-2.7",
        viral_research_mode="public_multisource",
        viral_research_max_queries=4,
    )
    context = {"company": {"profile": {
        "identity": {"name": "ServGo"},
        "offer": {"category": "онлайн-запись", "products_services": []},
        "customers": {
            "primary_segments": ["Массажные и beauty-студии"],
            "pains": ["Заявки и запись разбросаны по мессенджерам"],
        },
        "proof": {"approved_facts": [{
            "statement": "Client flow includes business/service selection, optional specialist selection, date/time-slot selection, contact details and confirmation."
        }]},
    }}}

    artifact = await run_viral_content_research(
        db=db,
        settings=settings,
        llm=llm,
        task=task,
        context=context,
        video_scout=FakeVideoScout(),
        article_scout=FakeArticleScout(),
    )

    stored = ViralContentResearch.model_validate_json(artifact.content)
    assert artifact.kind == "viral_content_research"
    assert stored.source_status == "evidence_available"
    assert stored.analysis_status == "completed"
    assert stored.research_quality == "strong_multisource"
    assert len(stored.video_candidates) == 3
    assert len(stored.article_candidates) == 3
    assert stored.audience_pains[0].product_match == "verified_match"
    assert stored.audience_pains[0].product_fact_id == "profile_fact:1"
    assert len(stored.white_space_angles) >= 2
    assert len(stored.humor_angles) >= 2

    run = db.scalar(select(AgentRun).where(AgentRun.agent_name == "viral_content_researcher"))
    assert run.status == "completed"
    assert run.output_references == [{"kind": "artifact", "id": str(artifact.id)}]
    assert db.scalar(select(CostRecord).where(CostRecord.agent_run_id == run.id)) is not None
    assert db.scalar(select(Artifact).where(Artifact.kind == "viral_content_research")) is not None
