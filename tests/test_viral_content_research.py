import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.content.viral_content_research import (
    ArticleCandidate,
    RankedVideoCandidate,
    ViralContentResearch,
    parse_duckduckgo_html,
    run_viral_content_research,
)
from app.content.viral_research import ViralVideoCandidate
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus
from app.privacy import PrivacyGateway


def test_duckduckgo_html_adds_evergreen_article_evidence():
    page = """
    <div class="result results_links">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbooking-errors">5 ошибок онлайн-записи в салоне</a>
      <a class="result__snippet">Как сократить ручные уточнения услуги, мастера и времени.</a>
    </div>
    """
    rows = parse_duckduckgo_html(page, "массажный салон онлайн запись")
    assert len(rows) == 1
    assert str(rows[0].url) == "https://example.com/booking-errors"
    assert rows[0].publisher == "example.com"
    assert "ручные уточнения" in (rows[0].snippet or "")
    assert rows[0].source_id.startswith("web:")


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


class SparseVideoScout(FakeVideoScout):
    async def collect(self, queries, max_candidates=16):
        return (await super().collect(queries, max_candidates=max_candidates))[:1]


class SparseArticleScout(FakeArticleScout):
    async def collect(self, queries, max_candidates=16):
        return (await super().collect(queries, max_candidates=max_candidates))[:1]



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


def minimum_complete_analysis():
    return {
        "summary": "Повторяется заметная операционная боль ручной записи; тема подходит для сильного маркетингового теста без обещания гарантированного результата.",
        "audience_pains": [
            {
                "pain": "Клиент ждёт ответа о свободном времени.",
                "source_ids": ["yt:one1234", "article:1"],
                "why_it_matters": "Ожидание растягивает путь до записи и создаёт лишнюю переписку.",
                "product_match": "gap",
                "product_fact_id": None,
                "product_match_explanation": "Боль подтверждена источниками; продуктовый мост должен опираться только на доступные факты.",
                "presentation_angle": "Показать путь клиента от первого вопроса до выбора окна.",
                "humor_angle": "Календарь, который живёт сразу в трёх чатах и одной голове.",
            },
            {
                "pain": "Администратор вручную собирает услугу, мастера и время.",
                "source_ids": ["yt:three1234", "article:3"],
                "why_it_matters": "Один простой запрос превращается в цепочку повторяющихся уточнений.",
                "product_match": "gap",
                "product_fact_id": None,
                "product_match_explanation": "Источники подтверждают процессную боль, но не обещают бизнес-результат.",
                "presentation_angle": "Сравнить ручной квест и понятный пошаговый маршрут.",
                "humor_angle": "Администратор как CRM, календарь и диспетчер в одном человеке.",
            },
        ],
        "hook_patterns": [
            {
                "pattern": "Посчитать сообщения до записи.",
                "why_it_may_work": "Число быстро делает скрытую ручную работу заметной.",
                "source_ids": ["yt:three1234"],
            },
            {
                "pattern": "Начать с узнаваемой ошибки процесса.",
                "why_it_may_work": "Владелец сразу узнаёт рабочую ситуацию.",
                "source_ids": ["yt:one1234"],
            },
        ],
        "topic_patterns": [],
        "article_patterns": [],
        "stepps_mechanisms": ["Practical Value", "Stories"],
        "seo_topic_seeds": [
            "как сократить переписку при записи",
            "как организовать запись клиентов",
            "ошибки ручной записи в салоне",
        ],
        "content_angles": [
            "Сколько сообщений стоит одна запись",
            "Когда чат незаметно становится CRM",
            "Где клиент застревает до выбора времени",
        ],
        "white_space_angles": ["Показать запись как UX-маршрут, а не список функций."],
        "humor_angles": [
            "Чат, который незаметно устроился работать CRM.",
            "Свободное окно как финальный босс маленького квеста.",
        ],
        "counterexamples": [],
        "cautions": [],
    }


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



@pytest.mark.asyncio
async def test_sparse_but_grounded_analysis_is_valid(db, tmp_path, monkeypatch):
    product = Product(name="ServGo", slug="servgo-sparse")
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
    provider = FakeLLMProvider(LLMResponse(
        content=json.dumps({
            "summary": "Источники дают полезный тематический сигнал, но данных недостаточно, чтобы честно заполнить каждый аналитический блок."
        }, ensure_ascii=False),
        model="kimi-2.7",
        usage=LLMUsage(input_tokens=100, output_tokens=50),
    ))
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider)
    settings = Settings(
        products_root=tmp_path,
        llm_provider="xai",
        llm_model="kimi-2.7",
        viral_research_mode="public_multisource",
    )
    context = {"company": {"profile": {
        "identity": {"name": "ServGo"},
        "offer": {"category": "онлайн-запись", "products_services": []},
        "customers": {"primary_segments": ["Массажные студии"], "pains": []},
        "proof": {"approved_facts": []},
    }}}

    artifact = await run_viral_content_research(
        db=db, settings=settings, llm=llm, task=task, context=context,
        video_scout=SparseVideoScout(), article_scout=SparseArticleScout(),
    )
    stored = ViralContentResearch.model_validate_json(artifact.content)
    assert stored.analysis_status == "completed"
    assert stored.hook_patterns == []
    assert stored.audience_pains == []


@pytest.mark.asyncio
async def test_rich_partial_analysis_survives_failed_repair_and_keeps_marketing_angles(db, tmp_path, monkeypatch):
    product = Product(name="ServGo", slug="servgo-partial-repair")
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

    primary = FakeLLMProvider(LLMResponse(
        content=json.dumps({
            "summary": "Источники показывают узнаваемую операционную тему ручной записи; полезные наблюдения нужно сохранить, даже если часть структурированных блоков модель не заполнила.",
            "topic_patterns": [{
                "pattern": "Ручная запись превращает простой запрос клиента в длинную цепочку уточнений.",
                "why_it_may_work": "Сцена быстро узнаётся владельцами сервисного бизнеса и даёт основу для сильного контента.",
                "source_ids": ["yt:three1234", "article:3"],
            }],
        }, ensure_ascii=False),
        model="kimi-2.7",
        usage=LLMUsage(input_tokens=80, output_tokens=60),
    ))
    broken_repair = FakeLLMProvider(LLMResponse(
        content="not-json",
        model="grok-4.7",
        usage=LLMUsage(input_tokens=90, output_tokens=10),
    ))
    monkeypatch.setattr("app.core.composition.build_llm_provider", lambda settings: broken_repair)

    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=primary)
    settings = Settings(
        app_env="production",
        products_root=tmp_path,
        llm_provider="xai",
        llm_model="kimi-2.7",
        llm_fallback_models="grok-4.7",
        viral_research_mode="public_multisource",
    )
    context = {"company": {"profile": {
        "identity": {"name": "ServGo"},
        "offer": {"category": "онлайн-запись", "products_services": []},
        "customers": {"primary_segments": ["Массажные студии"], "pains": []},
        "proof": {"approved_facts": []},
    }}}

    artifact = await run_viral_content_research(
        db=db, settings=settings, llm=llm, task=task, context=context,
        video_scout=FakeVideoScout(), article_scout=FakeArticleScout(),
    )

    stored = ViralContentResearch.model_validate_json(artifact.content)
    assert stored.analysis_status == "completed"
    assert stored.topic_patterns[0].source_ids == ["yt:three1234", "article:3"]
    assert len(stored.seo_topic_seeds) >= 3
    assert len(stored.content_angles) >= 3
    assert len(stored.humor_angles) >= 2
    assert any("сохранён полезный валидный первичный анализ" in item for item in stored.cautions)
    assert any("audience_pains" in item for item in stored.cautions)

    run = db.scalar(select(AgentRun).where(
        AgentRun.task_id == task.id,
        AgentRun.agent_name == "viral_content_researcher",
    ))
    assert run.status == "completed"
    costs = list(db.scalars(select(CostRecord).where(CostRecord.agent_run_id == run.id)))
    assert len(costs) == 2


@pytest.mark.asyncio
async def test_production_analysis_uses_configured_fallback_after_invalid_primary(db, tmp_path, monkeypatch):
    product = Product(name="ServGo", slug="servgo-fallback")
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

    primary = FakeLLMProvider(LLMResponse(
        content=json.dumps({
            "summary": "Первичная модель дала хороший абзац, но не заполнила обязательные маркетинговые блоки для богатого evidence ledger."
        }, ensure_ascii=False),
        model="kimi-2.7",
        usage=LLMUsage(input_tokens=50, output_tokens=20),
    ))
    fallback = FakeLLMProvider(LLMResponse(
        content=json.dumps(minimum_complete_analysis(), ensure_ascii=False),
        model="grok-4.7",
        usage=LLMUsage(input_tokens=90, output_tokens=40),
    ))
    monkeypatch.setattr("app.core.composition.build_llm_provider", lambda settings: fallback)

    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=primary)
    settings = Settings(
        app_env="production",
        products_root=tmp_path,
        llm_provider="xai",
        llm_model="kimi-2.7",
        llm_fallback_models="grok-4.7",
        viral_research_mode="public_multisource",
    )
    context = {"company": {"profile": {
        "identity": {"name": "ServGo"},
        "offer": {"category": "онлайн-запись", "products_services": []},
        "customers": {"primary_segments": ["Массажные студии"], "pains": []},
        "proof": {"approved_facts": []},
    }}}

    artifact = await run_viral_content_research(
        db=db, settings=settings, llm=llm, task=task, context=context,
        video_scout=FakeVideoScout(), article_scout=FakeArticleScout(),
    )
    stored = ViralContentResearch.model_validate_json(artifact.content)
    assert stored.analysis_status == "completed"
    assert any("fallback-моделью" in caution for caution in stored.cautions)
    run = db.scalar(select(AgentRun).where(
        AgentRun.task_id == task.id,
        AgentRun.agent_name == "viral_content_researcher",
    ))
    assert run.model == "grok-4.7"
    costs = list(db.scalars(select(CostRecord).where(CostRecord.agent_run_id == run.id)))
    assert len(costs) == 2
