import copy
import json
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.content.contracts import ReadyTextSet, VideoVariants
from app.content.service import queue_content
from app.content.render import render_video, asset_path
from app.core.config import Settings
from app.core.composition import build_article_production
from app.models.domain import Artifact, Product, TaskStatus, ToolCall, Publication
from app.workflows.article_contracts import ArticleProductionInput
from test_articles import valid_outputs, SequenceProvider


def ready_outputs():
    outputs = valid_outputs()
    bodies = [
        'Начните с маршрута клиента. Подумайте, какую услугу он ищет и что должен узнать перед записью. ',
        'Коллеги, предлагаю обсудить организацию расписания. Проверьте, где менеджер повторяет одну операцию вручную. ',
        'Рассмотрим процесс через ограничения и выбор инструментов. Автоматизация требует описания этапов и проверки исходных условий. ',
        'Чек-лист на сегодня: откройте путь записи и выпишите три вопроса посетителя. Выберите один шаг для проверки. ',
        'Вопрос: что проверить первым? Ответ: доступность услуг и времени. Вопрос: как проверить результат? Ответ: записывать реальные обращения. ',
        'Тема для авторского обсуждения: почему важно начинать с понятного процесса. Это предложение для поста основателя о выборе следующего шага. ',
    ]
    texts = {'texts':[dict(channel=c, title=c, body=b*30, editorial_checks=['Проверить CTA и ссылки'])
        for c,b in zip(['dzen','tenchat','vc_ru','telegram','faq','founder'],bodies)]}
    videos=[]
    for platform,hook in [('youtube_shorts','Проверьте путь записи'),('instagram_reels','Где клиент ждёт ответа')]:
        video=copy.deepcopy(outputs[7])
        video['hook']=hook
        video['production_brief']['target_platform']=platform
        video['production_brief']['duration_seconds']=30
        video['production_brief']['shots'][-1]['end_second']=30
        videos.append(video)
    return outputs + [texts, videos[0], videos[1]]

@pytest.mark.asyncio
async def test_complete_factory_outputs_ready_texts_and_two_platform_video_briefs(db, tmp_path):
    product=Product(name='ServGo',slug='servgo');db.add(product);db.commit()
    provider=SequenceProvider(ready_outputs())
    result=await build_article_production(db,Settings(products_root=tmp_path),provider).run(
        ArticleProductionInput(product_id=product.id,topic='Онлайн-запись',produce_ready_assets=True,request_publication=True))
    assert result.status == 'completed',result.error
    assert len(provider.requests)==12
    assert len(list(db.scalars(select(Artifact).where(Artifact.kind=='channel_text'))))==6
    scripts=list(db.scalars(select(Artifact).where(Artifact.kind=='video_script')))
    assert len(scripts)==2
    assert {json.loads(s.content)['production_brief']['target_platform'] for s in scripts}=={'youtube_shorts','instagram_reels'}
    assert len(result.approval_ids)==1
    assert list(db.scalars(select(ToolCall)))==list(db.scalars(select(Publication)))==[]
    assert not list(db.scalars(select(Artifact).where(Artifact.kind=='render_request')))

@pytest.mark.asyncio
async def test_factory_duplicate_channel_copy_fails_atomically(db,tmp_path):
    p=Product(name='P',slug='p');db.add(p);db.commit()
    outputs=ready_outputs();outputs[10]['texts'][1]['body']=outputs[10]['texts'][0]['body']
    result=await build_article_production(db,Settings(products_root=tmp_path),SequenceProvider(outputs)).run(
        ArticleProductionInput(product_id=p.id,topic='Test',produce_ready_assets=True))
    assert result.status=='failed'
    checkpoints=list(db.scalars(select(Artifact).where(Artifact.kind=='content_stage_output')))
    assert len(checkpoints)==9
    assert result.checkpoint_artifact_ids==[a.id for a in checkpoints]
    assert not list(db.scalars(select(Artifact).where(Artifact.kind=='article')))


def test_queue_content_is_idempotent_and_distinct_from_outreach(db):
    p=Product(name='P',slug='p');db.add(p);db.commit()
    first=queue_content(db,p.id);second=queue_content(db,p.id)
    assert first.id==second.id and first.assigned_agent=='content_factory'
    assert first.title.startswith('CONTENT:')


def test_actual_renderer_outputs_mp4_cover_and_cyrillic_subtitles(tmp_path):
    brief=VideoVariants.model_validate({'videos': ready_outputs()[-2:]}).videos[0].production_brief
    metadata=render_video(brief,tmp_path/'assets','ServGo')
    assert metadata['duration_seconds']==30 and metadata['audio']=='none'
    assert (tmp_path/'assets/video.mp4').stat().st_size>10000
    assert 'Сколько' in (tmp_path/'assets/subtitles.srt').read_text()
    from PIL import Image
    assert Image.open(tmp_path/'assets/cover.png').size==(720,1280)


def test_reject_overlapping_timeline_and_path_injection(tmp_path):
    videos=copy.deepcopy(ready_outputs()[-2:]);videos[0]['production_brief']['shots'][1]['start_second']=3
    with pytest.raises(ValueError):VideoVariants.model_validate({'videos': videos})
    with pytest.raises(ValueError):asset_path(tmp_path,uuid4(),uuid4(),'../../x')
