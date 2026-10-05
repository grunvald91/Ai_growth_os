"""Offline article workflow integration and API acceptance tests."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.products import get_article_production
from app.core.composition import build_article_production
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.fake import LLMResponse, LLMUsage
from app.main import app
from app.models.domain import Product, Task, Artifact, Approval, AgentRun, CostRecord, ToolCall, Publication, TaskStatus, Experiment
from app.workflows.article_contracts import ArticleProductionInput, ArticleArtifact


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = outputs
        self.requests = []

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        output = self.outputs[len(self.requests) - 1]
        if isinstance(output, Exception):
            raise output
        return LLMResponse(content=output if isinstance(output, str) else json.dumps(output),
            model='fake', usage=LLMUsage(input_tokens=10, output_tokens=20))


def valid_outputs():
    measurement = {k: dict(event=k + '_event', metric=k + '_rate', attribution='Proposed campaign attribution')
        for k in ['content', 'traffic', 'lead', 'activation', 'customer']}
    measurement.update(
        activation_definition='demo',
        review_window_days=14,
        success_hypothesis='Useful guides may generate qualified inquiries',
    )

    video_brief = dict(
        asset_type='short_video',
        target_platform='youtube_shorts',
        aspect_ratio='9:16',
        duration_seconds=35,
        objective='Объяснить владельцу бизнеса логику онлайн-записи и привести к осмысленному запросу демо.',
        hook_variants=['Сколько сообщений до записи?', 'Где клиент ждёт ответа?', 'Проверьте путь записи за 30 секунд'],
        creative_concept='Один ведущий в кадре плюс крупные текстовые акценты и простые предметные перебивки.',
        visual_style='Чистый современный деловой стиль, естественный свет, спокойный фон, крупные читаемые титры.',
        setting_and_props='Вертикальная съёмка в рабочем пространстве; телефон и календарь как нейтральный реквизит.',
        talent_direction='Говорить живо и уверенно, без дикторской манеры; смотреть в камеру; короткие фразы.',
        voiceover_or_dialogue_script='Если клиенту приходится писать вам, чтобы узнать услугу, время и подтверждение, часть пути всё ещё держится на ручной переписке. Разберите путь записи по шагам и проверьте, какие из них можно сделать понятнее.',
        shots=[
            dict(number=1, start_second=0, end_second=4, purpose='Хук', shot_type='Средний план',
                 visual_description='Ведущий смотрит в камеру, рядом телефон с открытым календарём.',
                 camera_and_movement='Статичная вертикальная камера на уровне глаз.',
                 action='Ведущий произносит первую фразу и показывает телефон.',
                 spoken_text='Сколько сообщений нужно клиенту, чтобы просто записаться?',
                 on_screen_text='Сколько сообщений до записи?', transition='Жёсткая склейка',
                 sound_or_music_note='Тихий современный ритм под голосом.',
                 generation_prompt='Vertical 9:16 medium shot of a small-business owner looking into camera beside a phone with a neutral calendar, natural daylight, realistic office, subtle push-in, no logos or readable third-party UI.',
                 avoid_in_generation='Чужие логотипы, фальшивый интерфейс, лишние руки, мелкий текст.'),
            dict(number=2, start_second=4, end_second=22, purpose='Основная мысль', shot_type='Чередование среднего и крупного плана',
                 visual_description='Ведущий перечисляет шаги, между фразами — крупные планы телефона и календаря.',
                 camera_and_movement='Лёгкие цифровые приближения на ключевых словах.',
                 action='На каждом шаге появляется одно короткое слово: услуга, специалист, время, контакты.',
                 spoken_text='Разложите путь на шаги: услуга, при необходимости специалист, свободное время, контакты и понятное подтверждение.',
                 on_screen_text='Услуга → специалист → время → контакты', transition='Быстрые склейки по речи',
                 sound_or_music_note='Музыка без вокала; акценты на появлении текста.',
                 generation_prompt='Vertical 9:16 sequence alternating presenter medium shot and close-ups of a phone and paper calendar, clean realistic service-business workspace, simple four-step visual rhythm, no branded UI.',
                 avoid_in_generation='Не показывать реальные персональные данные, чужие бренды или несуществующие функции.'),
            dict(number=3, start_second=22, end_second=35, purpose='Вывод и CTA', shot_type='Крупный план',
                 visual_description='Ведущий снова напрямую обращается к зрителю; фон остаётся чистым.',
                 camera_and_movement='Статичный крупный план.',
                 action='В финале появляется короткий CTA.',
                 spoken_text='Если хотите сравнить этот путь со своей записью, сохраните ролик и выпишите, где клиенту сегодня приходится писать менеджеру.',
                 on_screen_text='Сохраните и проверьте свой путь записи', transition='Плавное затемнение',
                 sound_or_music_note='Музыка слегка поднимается и уходит в финале.',
                 generation_prompt='Vertical 9:16 close-up of presenter delivering a calm CTA in a bright minimal workspace, phone lowered, clean negative space for overlay text, gentle fade-out, realistic lighting.',
                 avoid_in_generation='Неон, киберпанк, обещания роста, чужие логотипы.'),
        ],
        subtitle_style='Белые крупные субтитры по 3–6 слов, ключевые слова выделять жирным; нижняя безопасная зона.',
        music_direction='Нейтральная современная инструментальная музыка 95–110 BPM, без драматического напряжения.',
        cover_frame='Портрет ведущего по пояс, телефон в руке; крупный текст: «Сколько сообщений до записи?»',
        editing_rhythm='Первые 4 секунды без пауз, далее смена визуального акцента каждые 2–4 секунды.',
        editing_mix_notes='Голос впереди музыки; ducking музыки под речь; короткие акценты на появлении текста; без агрессивных переходов.',
        cta_frame='Финальные 2 секунды: «Сохраните и проверьте свой путь записи».',
        caption_variants=['Проверьте, сколько ручных шагов остаётся в вашей записи.', 'Разберите путь клиента от услуги до подтверждения.'],
        export_spec='1080×1920, H.264, 25/30 fps, длительность около 35 секунд, без встроенного водяного знака.',
        production_notes=['Не показывать интерфейс несуществующей функции.', 'Не использовать обещания роста или снижения no-show без данных.'],
    )

    story_brief = dict(
        asset_type='story',
        aspect_ratio='9:16',
        objective='Дать владельцу бизнеса быстрый чек-лист пути записи.',
        narrative_arc='Проблема → четыре шага → вопрос к своему процессу → CTA.',
        visual_style='Минималистичный деловой стиль, много воздуха, крупная типографика, один смысл на экран.',
        typography_direction='Крупный гротеск, 1–2 начертания, не более 12–16 слов на основном экране.',
        frames=[
            dict(number=1, duration_seconds=5, purpose='Хук',
                 visual_description='Фон: телефон и блокнот на рабочем столе, без узнаваемых чужих интерфейсов.',
                 composition='Объект в нижней трети, текст в верхней и средней безопасной зоне.',
                 on_screen_text='Клиент всё ещё пишет менеджеру, чтобы записаться?',
                 animation_or_transition='Лёгкое появление текста снизу вверх.',
                 interaction='Опрос: «Да / Уже онлайн»',
                 safe_area_note='Не размещать текст в верхних 250 px и нижних 350 px.'),
            dict(number=2, duration_seconds=5, purpose='Структура',
                 visual_description='Четыре простых текстовых шага с тонкими стрелками между ними.',
                 composition='Вертикальная цепочка по центру.',
                 on_screen_text='Услуга → специалист → время → контакты',
                 animation_or_transition='Шаги появляются последовательно.',
                 interaction=None, safe_area_note='CTA-зона свободна внизу.'),
            dict(number=3, duration_seconds=6, purpose='Проверка',
                 visual_description='Чек-лист на светлом фоне, один пункт подсвечен.',
                 composition='Заголовок сверху, три строки по центру.',
                 on_screen_text='Где сегодня клиент ждёт ответа человека?',
                 animation_or_transition='Мягкое увеличение ключевой строки.',
                 interaction='Стикер-вопрос: «На каком шаге?»',
                 safe_area_note='Стикер не перекрывает основной текст.'),
            dict(number=4, duration_seconds=5, purpose='CTA',
                 visual_description='Чистый брендовый экран с коротким финальным сообщением.',
                 composition='Текст по центру, логотип/название небольшим внизу.',
                 on_screen_text='Сохраните чек-лист и разберите свой путь записи',
                 animation_or_transition='Плавное появление.',
                 interaction=None, safe_area_note='Основной текст в центральной безопасной зоне.'),
        ],
        final_cta='Сохраните серию и выпишите один шаг, который сейчас требует ручной переписки.',
        export_spec='1080×1920, PNG/JPG для статичных кадров или MP4 H.264 для анимированных, 9:16.',
        production_notes=['Не использовать чужие логотипы и интерфейсы.', 'Не обещать результат без измерений.'],
    )

    image_brief = dict(
        asset_type='static_image',
        target_formats=['4:5', '1:1'],
        objective='Остановить внимание владельца сервисного бизнеса и визуально показать последовательность записи.',
        visual_style='Современный минимализм, editorial SaaS, реалистичный предметный фон без псевдоинтерфейсов.',
        subject='Телефон, календарь и четыре лаконичных шага записи как визуальная схема.',
        composition='В 4:5 телефон справа, цепочка из четырёх шагов слева; в 1:1 схема перестраивается в две строки.',
        background_and_environment='Светлый тёплый рабочий стол, минимум предметов, мягкая глубина резкости.',
        lighting_and_mood='Мягкий дневной свет, спокойное профессиональное настроение, без неонового tech-клише.',
        typography_direction='Крупный нейтральный гротеск, высокий контраст, максимум две гарнитуры/начертания.',
        overlay_text='Сколько шагов проходит клиент до записи?',
        brand_elements='Небольшое название ServGo; без выдуманных UI-экранов и неподтверждённых бейджей.',
        image_generation_prompt='Фотореалистичный современный рабочий стол небольшого сервисного бизнеса, смартфон и бумажный календарь, много свободного пространства для типографики, чистая editorial SaaS эстетика, мягкий естественный дневной свет, вертикальная композиция 4:5, без текста внутри самой генерации, без логотипов сторонних сервисов, без фантастического интерфейса.',
        avoid_in_visual='Неон, киберпанк, перегруженный интерфейс, фальшивые графики роста, чужие бренды, мелкий нечитаемый текст, лишние руки.',
        cta_treatment='CTA не обязателен на изображении; при необходимости маленькая строка внизу: «Проверьте свой путь записи».',
        export_spec='Основной 1080×1350 (4:5), адаптация 1080×1080 (1:1), PNG/JPG, текст наносить после генерации изображения.',
        production_notes=['Сначала создавать чистый визуал без текста, затем верстать типографику отдельно.', 'Проверить читаемость на экране телефона.'],
    )

    kinds = ['dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder', 'short_video', 'story', 'static_image']
    hooks = ['Разберём путь записи без лишних обещаний', 'Обсудим рабочий чек-лист записи',
        'Эксперимент с процессом онлайн-записи', 'Сохраните четыре шага записи',
        'Какие вопросы задать системе записи?', 'Что бы я проверил до автоматизации',
        'Сколько сообщений до записи?', 'Четыре экрана про путь клиента', 'Одна схема пути записи']
    frames = ['Образовательный материал', 'Профессиональное обсуждение', 'Аналитический разбор',
        'Короткий пост', 'FAQ', 'Авторская заметка', 'Режиссёрский сценарий',
        'Покадровые сторис', 'Техническое задание на изображение']

    derivatives = []
    for kind, hook, framing in zip(kinds, hooks, frames):
        item = dict(
            kind=kind,
            audience='Владельцы сервисного бизнеса',
            framing=framing,
            hook=hook,
            outline=['Объяснить проблему', 'Дать один практический следующий шаг'],
            cta='Проверить свой путь записи',
            landing_page=dict(label='Предлагаемая страница', destination='/demo'),
            adaptation_notes='Только текстовый материал для последующего производства и проверки.',
            video_mode='brief_only' if kind == 'short_video' else None,
        )
        if kind == 'short_video':
            item['production_brief'] = video_brief
        elif kind == 'story':
            item['production_brief'] = story_brief
        elif kind == 'static_image':
            item['production_brief'] = image_brief
        derivatives.append(item)

    safe_body = dict(
        sections=[dict(heading=h, kind='advice', body=(b + ' ') * 12) for h, b in [
            ('Путь клиента', 'Разложите путь записи на понятные шаги и проверьте, где человеку приходится ждать ответа.'),
            ('Ожидания клиента', 'Сравните желаемый путь клиента с тем, как запись происходит сегодня, и отметьте ручные точки.'),
            ('Небольшой эксперимент', 'Проверяйте изменения на ограниченном сценарии и заранее определите, какие наблюдения будут полезны.'),
            ('Результат проверки', 'Смотрите на реальные обращения и действия бизнеса, а не считайте просмотры доказательством продаж.'),
        ]],
        proof_needed_notes=['Проверить продуктовые факты и собрать реальные данные воронки'],
    )

    human_article = dict(
        language='ru',
        title='Как устроить понятный путь онлайн-записи в небольшом сервисном бизнесе',
        lead='Когда запись держится на переписке, клиенту приходится задавать одни и те же вопросы: какая услуга доступна, кто может принять, когда есть свободное время и что делать дальше. Полезнее смотреть не на отдельную кнопку «Записаться», а на весь путь от первого выбора до понятного подтверждения.',
        fact_renderings=[],
        sections=[
            dict(heading='Начните не с сервиса, а с пути клиента',
                 body=('Сначала выпишите реальный сценарий человека: что он знает в начале, что должен выбрать и где ему требуется помощь менеджера. Чем меньше скрытых шагов, тем легче увидеть, что стоит автоматизировать, а что разумно оставить под контролем человека. ')*4),
            dict(heading='Где обычно возникает ручная работа',
                 body=('Ручная работа появляется там, где информация разбросана между сообщениями, календарём и памятью сотрудников. Проверьте, сколько раз клиенту приходится уточнять условия и сколько решений менеджер принимает просто для того, чтобы довести запись до понятного статуса. ')*4),
            dict(heading='Предоплата — отдельный процесс, а не одна кнопка',
                 body=('Если бизнес использует предоплату, заранее определите, кто проверяет перевод, что видит клиент во время ожидания и в какой момент запись считается подтверждённой. Такой процесс важно описать до автоматизации, иначе система лишь ускорит существующую путаницу. ')*4),
            dict(heading='Проверяйте на небольшом сценарии',
                 body=('Не нужно сразу перестраивать все каналы. Возьмите один тип услуги или один сценарий записи, запустите ограниченную проверку и фиксируйте реальные обращения. Так станет понятно, какие шаги действительно помогают клиенту, а какие требуют доработки. ')*4),
        ],
        closing='Хорошая онлайн-запись — это не максимальное количество функций. Это понятный маршрут, в котором клиент знает следующий шаг, а бизнес понимает, где требуется человеческое решение. Начните с карты своего процесса — и уже по ней сравнивайте инструменты.',
        editorial_notes=['Не добавлялись неподтверждённые обещания и сравнения с конкурентами.'],
    )

    text_derivatives = []
    for item in derivatives[:6]:
        text_derivatives.append({
            key: value for key, value in item.items()
            if key not in {'video_mode', 'production_brief'}
        })

    video_item = dict(derivatives[6])
    story_item = {
        key: value for key, value in derivatives[7].items()
        if key != 'video_mode'
    }
    image_item = {
        key: value for key, value in derivatives[8].items()
        if key != 'video_mode'
    }

    return [
        dict(audience='Владельцы сервисного бизнеса', pain='Ручная координация записи',
             search_intent='Разобраться в процессе онлайн-записи',
             angle='Практический разбор пути клиента', titles=['Чек-лист онлайн-записи', 'Путь клиента до записи'],
             hypothesis='Понятное объяснение процесса может привести к осмысленным обращениям'),
        dict(primary_query='онлайн запись для сервисного бизнеса',
             related_queries=['как организовать онлайн запись'], title='Чек-лист онлайн-записи',
             hook='Начните с пути клиента', outline=['Проблема', 'Путь', 'Проверка'], adaptation_notes='Русский материал'),
        safe_body,
        dict(cta='Сравните этот путь со своей текущей записью',
             landing_page=dict(label='Предлагаемая страница', destination='/demo'),
             internal_links=[dict(label='Предлагаемый материал', destination='/guides/booking')],
             measurement=measurement,
             distribution=[dict(channel='own_site', action='prepare_draft',
                                rationale='Начать с собственного черновика', downstream_goal='lead')]),
        human_article,
        dict(derivatives=text_derivatives),
        video_item,
        story_item,
        image_item,
    ]

@pytest.fixture
def product(db):
    row = Product(name='ServGo', slug='servgo')
    db.add(row); db.commit()
    return row


def setup(db, product, tmp_path, outputs=None, **kwargs):
    provider = SequenceProvider(outputs if outputs is not None else valid_outputs())
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    request = ArticleProductionInput(product_id=product.id, topic='Booking checklist', audience='Owners', problem='Scheduling friction', **kwargs)
    return workflow, provider, request


@pytest.mark.asyncio
async def test_long_form_distinct_variants_measurement_and_audit(db, product, tmp_path):
    workflow, provider, request = setup(db, product, tmp_path)
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    assert len(result.artifact_ids) == 10 and len(result.run_ids) == len(provider.requests) == 9
    assert all(request.metadata["reasoning_effort"] == "low" for request in provider.requests)
    assert {request.metadata["team_role"] for request in provider.requests} == {
        "content_strategist", "seo_editor", "article_writer", "conversion_editor", "human_editor",
        "repurposing_editor", "video_director", "story_director", "visual_designer"
    }
    by_role = {request.metadata["team_role"]: request.max_tokens for request in provider.requests}
    assert by_role == {
        "content_strategist": 3000,
        "seo_editor": 3500,
        "article_writer": 8000,
        "conversion_editor": 3500,
        "human_editor": 8000,
        "repurposing_editor": 8000,
        "video_director": 5500,
        "story_director": 4500,
        "visual_designer": 4000,
    }
    writer_request = next(r for r in provider.requests if r.metadata["team_role"] == "article_writer")
    assert "claims" not in writer_request.response_schema["properties"]
    assert set(writer_request.response_schema["$defs"]["ArticleBodySection"]["properties"]["kind"]["enum"]) == {"advice", "hypothesis"}
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert len(content.full_article) >= 2000 and len(content.draft.sections) >= 4
    assert content.conversion.cta
    assert len({d.hook for d in content.conversion.derivatives}) == 9
    assert len({d.framing for d in content.conversion.derivatives}) == 9
    video = next(d for d in content.conversion.derivatives if d.kind == 'short_video')
    story = next(d for d in content.conversion.derivatives if d.kind == 'story')
    image = next(d for d in content.conversion.derivatives if d.kind == 'static_image')
    assert video.video_mode == 'brief_only' and len(video.production_brief.shots) >= 3
    assert story.production_brief.aspect_ratio == '9:16' and len(story.production_brief.frames) >= 3
    assert image.production_brief.image_generation_prompt and image.production_brief.target_formats
    assert content.reader_article is not None
    assert '[HYPOTHESIS]' not in content.full_article and '[ADVICE]' not in content.full_article
    for plan in [content.conversion.measurement] + [d.measurement for d in content.conversion.derivatives]:
        assert all(getattr(plan, k).event for k in ['content', 'traffic', 'lead', 'activation', 'customer'])
    assert all(db.get(Artifact, aid).task_id == result.task_id for aid in result.artifact_ids)
    assert all(db.get(AgentRun, rid).status == 'completed' for rid in result.run_ids)
    assert len(list(db.scalars(select(CostRecord)))) == 9
    assert all(c.details['cost_known'] is False for c in db.scalars(select(CostRecord)))
    assert list(db.scalars(select(ToolCall))) == list(db.scalars(select(Publication))) == []
    assert result.approval_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['malformed', 'short', 'missing_cta', 'missing_measurement', 'fake_claim', 'video', 'external_link', 'extra', 'wrong_kind', 'source_copy'])
async def test_invalid_output_keeps_only_durable_stage_checkpoints(db, product, tmp_path, mutation):
    outputs = valid_outputs()
    if mutation == 'malformed': outputs[0] = 'not json'
    elif mutation == 'short': outputs[2]['sections'] = outputs[2]['sections'][:1]
    elif mutation == 'missing_cta': del outputs[3]['cta']
    elif mutation == 'missing_measurement': del outputs[3]['measurement']['customer']
    elif mutation == 'fake_claim': outputs[2]['claims'] = [dict(claim='Guaranteed growth', quote='Guaranteed growth', source_id='invented')]
    elif mutation == 'video': outputs[6]['video_mode'] = 'render_mp4'
    elif mutation == 'external_link': outputs[3]['landing_page']['destination'] = 'https://unverified.example/demo'
    elif mutation == 'extra': outputs[0]['verified_revenue'] = 1000000
    elif mutation == 'source_copy': outputs[5]['derivatives'][0]['hook'] = outputs[1]['hook']
    elif mutation == 'wrong_kind': outputs[5]['derivatives'][0]['kind'] = 'telegram'
    workflow, _, request = setup(db, product, tmp_path, outputs)
    result = await workflow.run(request)
    assert result.status == 'failed' and result.error
    assert result.artifact_ids == result.approval_ids == []
    artifacts = list(db.scalars(select(Artifact).order_by(Artifact.created_at, Artifact.id)))
    assert all(a.kind == 'content_stage_output' for a in artifacts)
    assert result.checkpoint_artifact_ids == [a.id for a in artifacts]
    assert list(db.scalars(select(Approval))) == []
    assert db.get(Task, result.task_id).status == TaskStatus.FAILED
    runs = list(db.scalars(select(AgentRun)))
    assert sum(r.status == 'completed' for r in runs) == len(artifacts)
    assert any(r.status == 'failed' for r in runs)




@pytest.mark.asyncio
async def test_verified_product_facts_are_injected_deterministically(db, product, tmp_path):
    path = tmp_path / 'servgo' / '.claude'
    path.mkdir(parents=True)
    path.joinpath('product-marketing-context.md').write_text(
        '## Product\n'
        'ServGo is an online service booking system.\n'
        'Current verified flow includes:\n'
        '- customer selects a service;\n'
        '- owner confirms the booking.\n'
        '\n## Candidate business segments\n'
        '- massage studios are a hypothesis, not a fact.\n'
    )
    from app.workflows.article_production import product_facts
    context_text = path.joinpath('product-marketing-context.md').read_text()
    facts_map = product_facts(context_text)
    outputs = valid_outputs()
    outputs[4]['fact_renderings'] = [
        {'source_id': source_id, 'text': f'Проверенный факт ServGo: {index + 1}.'}
        for index, source_id in enumerate(facts_map)
    ]
    workflow, provider, request = setup(db, product, tmp_path, outputs)

    result = await workflow.run(request)

    assert result.status == 'completed', result.error
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    facts = [section for section in content.draft.sections if section.kind == 'fact']
    assert len(facts) == 1
    assert 'ServGo is an online service booking system.' in facts[0].body
    assert 'customer selects a service;' in facts[0].body
    assert 'owner confirms the booking.' in facts[0].body
    assert 'massage studios are a hypothesis' not in facts[0].body
    assert len(content.draft.claims) == 3
    assert all(claim.claim == claim.quote for claim in content.draft.claims)
    writer_request = next(r for r in provider.requests if r.metadata["team_role"] == "article_writer")
    assert "claims" not in writer_request.response_schema["properties"]


@pytest.mark.asyncio
async def test_article_binds_verified_experiment_context(db, product, tmp_path):
    experiment = Experiment(
        product_id=product.id,
        name='Own-site SEO test',
        hypothesis='Massage studios are an unranked audience hypothesis.',
        audience='Small massage-studio owners evaluating booking tools.',
        channel='own_site',
        target_metric='qualified_leads',
        success_condition='Persist at least one qualified lead tied to the page before considering KEEP.',
        status='draft',
    )
    db.add(experiment); db.commit()
    workflow, provider, request = setup(db, product, tmp_path, experiment_id=experiment.id)
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert content.experiment_id == experiment.id
    outbound = provider.requests[0].messages[0].content
    assert str(experiment.id) in outbound
    assert 'qualified_leads' in outbound


@pytest.mark.asyncio
async def test_article_rejects_missing_or_foreign_experiment(db, product, tmp_path):
    other = Product(name='Other experiment owner', slug='other-experiment-owner')
    db.add(other); db.flush()
    foreign = Experiment(product_id=other.id, name='Foreign', hypothesis='Foreign hypothesis')
    db.add(foreign); db.commit()

    for experiment_id in [uuid4(), foreign.id]:
        workflow, provider, request = setup(db, product, tmp_path, experiment_id=experiment_id)
        with pytest.raises(LookupError):
            await workflow.run(request)
        assert provider.requests == []




@pytest.mark.asyncio
async def test_creative_specialists_are_separate_calls(db, product, tmp_path):
    workflow, provider, request = setup(db, product, tmp_path)

    result = await workflow.run(request)

    assert result.status == 'completed', result.error
    roles = [request.metadata["team_role"] for request in provider.requests]
    assert roles[-4:] == ['repurposing_editor', 'video_director', 'story_director', 'visual_designer']
    schemas = {request.metadata["team_role"]: request.response_schema for request in provider.requests}
    assert schemas['video_director']['properties']['kind']['const'] == 'short_video'
    assert schemas['story_director']['properties']['kind']['const'] == 'story'
    assert schemas['visual_designer']['properties']['kind']['const'] == 'static_image'
    assert len(list(db.scalars(select(CostRecord)))) == 9


@pytest.mark.asyncio
async def test_measurement_window_may_remain_unset(db, product, tmp_path):
    outputs = valid_outputs()
    outputs[3]['measurement']['review_window_days'] = None

    workflow, _, request = setup(db, product, tmp_path, outputs)

    result = await workflow.run(request)

    assert result.status == 'completed', result.error
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert content.conversion.measurement.review_window_days is None
    assert all(d.measurement.review_window_days is None for d in content.conversion.derivatives)


@pytest.mark.asyncio
async def test_publication_only_requests_approval(db, product, tmp_path):
    workflow, _, request = setup(db, product, tmp_path, request_publication=True)
    result = await workflow.run(request)
    assert result.status == 'completed' and result.approval_ids
    assert all(db.get(Approval, aid).status == 'pending' and db.get(Approval, aid).risk_level == 'YELLOW' for aid in result.approval_ids)
    assert list(db.scalars(select(ToolCall))) == list(db.scalars(select(Publication))) == []


@pytest.mark.asyncio
async def test_provider_failure_does_not_expose_credentials(db, product, tmp_path):
    workflow, _, request = setup(db, product, tmp_path, [RuntimeError('api_key=private-provider-secret')])
    result = await workflow.run(request)
    assert result.status == 'failed'
    assert 'private-provider-secret' not in result.model_dump_json()
    assert all('private-provider-secret' not in (r.error or '') for r in db.scalars(select(AgentRun)))


@pytest.mark.asyncio
async def test_privacy_on_all_requests_and_output(db, product, tmp_path):
    path = tmp_path / 'servgo' / '.claude'
    path.mkdir(parents=True)
    raw_context = '## Product\nServGo is a booking service. api_key=private-context-secret'
    (path / 'product-marketing-context.md').write_text(raw_context)
    from app.privacy import PrivacyGateway
    from app.workflows.article_production import product_facts
    outputs = valid_outputs()
    outputs[0]['hypothesis'] += ' api_key=private-response-secret'
    sanitized_facts = product_facts(PrivacyGateway().sanitize_text(raw_context))
    outputs[4]['fact_renderings'] = [
        {'source_id': source_id, 'text': 'ServGo — сервис для онлайн-записи.'}
        for source_id in sanitized_facts
    ]
    workflow, provider, request = setup(db, product, tmp_path, outputs)
    request.problem += ' password=private-input-secret'
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    sent = ''.join(r.model_dump_json() for r in provider.requests)
    stored = ''.join(a.content for a in db.scalars(select(Artifact)))
    for secret in ['private-input-secret', 'private-context-secret', 'private-response-secret']:
        assert secret not in sent and secret not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['missing_task', 'foreign_task', 'missing_decision', 'nonqueued'])
async def test_invalid_refs_never_call_provider(db, product, tmp_path, reference):
    other = Product(name='Other', slug='other'); db.add(other); db.flush()
    task = Task(product_id=other.id if reference == 'foreign_task' else product.id, title='Article',
        status=TaskStatus.COMPLETED if reference == 'nonqueued' else TaskStatus.QUEUED)
    db.add(task); db.commit()
    refs = {'missing_task': dict(task_id=uuid4()), 'foreign_task': dict(task_id=task.id),
        'missing_decision': dict(decision_id=uuid4()), 'nonqueued': dict(task_id=task.id)}
    workflow, provider, request = setup(db, product, tmp_path, **refs[reference])
    with pytest.raises(ValueError if reference == 'nonqueued' else LookupError):
        await workflow.run(request)
    assert provider.requests == []


def test_api_ids_inspection_and_bad_references(tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        provider = SequenceProvider(valid_outputs())
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_article_production] = lambda: build_article_production(db, Settings(products_root=tmp_path), provider)
        try:
            with TestClient(app) as client:
                pid = client.post('/products', json=dict(name='ServGo', slug='servgo')).json()['id']
                body = dict(topic='Booking checklist')
                response = client.post(f'/products/{pid}/articles', json=body)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['status'] == 'completed', result
                assert result['main_artifact_id'] == result['artifact_ids'][0]
                assert len(result['derivative_artifact_ids']) == 9 and len(result['run_ids']) == 9
                aid = result['main_artifact_id']
                assert client.get(f'/products/{pid}/artifacts/{aid}').status_code == 200
                artifacts = client.get(f'/products/{pid}/artifacts').json()
                assert len(artifacts) == 19
                assert sum(a['kind'] == 'content_stage_output' for a in artifacts) == 9
                other = client.post('/products', json=dict(name='Other', slug='other')).json()['id']
                assert client.get(f'/products/{other}/artifacts/{aid}').status_code == 404
                for key in ['task_id', 'decision_id']:
                    assert client.post(f'/products/{pid}/articles', json={**body, key: str(uuid4())}).status_code == 404
                assert client.post(f'/products/{uuid4()}/articles', json=body).status_code == 404
                assert client.post(f'/products/{pid}/articles', json={**body, 'channel': 'ads'}).status_code == 422
                assert client.post(f'/products/{pid}/articles', json={**body, 'topic': '   '}).status_code == 422
                assert client.post(f'/products/{pid}/articles', json={**body, 'task_id': result['task_id']}).status_code == 409
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.asyncio
async def test_russian_is_default_and_visual_assets_are_required(db, product, tmp_path):
    workflow, provider, request = setup(db, product, tmp_path)
    assert request.language == 'ru'

    result = await workflow.run(request)

    assert result.status == 'completed', result.error
    first_prompt = provider.requests[0].system_prompt
    assert 'request.language' in first_prompt
    assert 'natural Russian' in first_prompt
    package = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    kinds = {item.kind for item in package.conversion.derivatives}
    assert {'story', 'short_video', 'static_image'} <= kinds


def test_article_api_defaults_to_russian():
    from app.api.schemas import ArticleCreate
    assert ArticleCreate(topic='Тест').language == 'ru'


@pytest.mark.asyncio
async def test_human_editor_requires_all_verified_fact_sources(db, product, tmp_path):
    path = tmp_path / 'servgo' / '.claude'
    path.mkdir(parents=True)
    text_context = '## Product\nServGo поддерживает выбор услуги.\n'
    path.joinpath('product-marketing-context.md').write_text(text_context)
    outputs = valid_outputs()
    outputs[4]['fact_renderings'] = []

    workflow, _, request = setup(db, product, tmp_path, outputs)
    result = await workflow.run(request)

    assert result.status == 'failed'
    assert result.error == 'invalid_human_editor_grounding'


@pytest.mark.asyncio
async def test_visual_assets_are_text_briefs_not_render_requests(db, product, tmp_path):
    workflow, _, request = setup(db, product, tmp_path)
    result = await workflow.run(request)
    assert result.status == 'completed', result.error

    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    video = next(d for d in article.conversion.derivatives if d.kind == 'short_video')
    story = next(d for d in article.conversion.derivatives if d.kind == 'story')
    image = next(d for d in article.conversion.derivatives if d.kind == 'static_image')

    assert video.production_brief.export_spec
    assert all(shot.visual_description and shot.camera_and_movement for shot in video.production_brief.shots)
    assert all(frame.on_screen_text and frame.composition for frame in story.production_brief.frames)
    assert image.production_brief.image_generation_prompt
    assert image.production_brief.export_spec


@pytest.mark.asyncio
async def test_truncated_stage_retries_once_and_accounts_both_calls(db, product, tmp_path):
    outputs = valid_outputs()
    workflow, provider, request = setup(db, product, tmp_path, [*outputs[:5], {}, *outputs[5:]])
    original = provider.generate
    async def generate(req):
        result = await original(req)
        if len(provider.requests) == 6:
            return result.model_copy(update={'stop_reason': 'length'})
        return result
    provider.generate = generate
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    attempts = [r for r in provider.requests if r.metadata['team_role'] == 'repurposing_editor']
    assert [r.max_tokens for r in attempts] == [8000, 16000]
    assert len(list(db.scalars(select(CostRecord)))) == 10
    assert 'Владельцы' in provider.requests[1].messages[0].content
    assert r'\u0412' not in provider.requests[1].messages[0].content


@pytest.mark.asyncio
async def test_repeated_truncation_fails_atomically_without_third_attempt(db, product, tmp_path):
    workflow, provider, request = setup(db, product, tmp_path, [{}, {}])
    original = provider.generate
    async def generate(req):
        return (await original(req)).model_copy(update={'stop_reason': 'length'})
    provider.generate = generate
    result = await workflow.run(request)
    assert result.status == 'failed' and len(provider.requests) == 2
    assert not result.artifact_ids and not list(db.scalars(select(Artifact)))
    assert len(list(db.scalars(select(CostRecord)))) == 2
