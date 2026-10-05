import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.content.calendar import (EditorialPlan, approve_plan, pause_plan, plan_record, planning_context,
    queue_plan, record_publication, render_calendar, run_planner_once, save_preferences, tick_calendar)
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Metric, Product, Task, TaskStatus, ToolCall, Publication
from app.privacy import PrivacyGateway
from app.content.telegram import TelegramContent


def draft():
    return {'timezone':'Europe/Moscow', 'horizon_days':14, 'review_after_days':7,
        'strategy':'Проверить полезные материалы о пути клиента для владельцев небольшого бизнеса.',
        'capacity_assumptions':['Предлагается один общий пакет; доступное время владельца ещё неизвестно.'],
        'unknowns':['Аккаунты и фактическое время на проверку не подтверждены.'],
        'channels':[{'platform':p, 'decision':'test', 'rationale':'Проверить интерес аудитории; эффективность пока не измерена.', 'evidence_refs':[]}
                    for p in ['own_site','dzen']],
        'slots':[{'package_key':'booking', 'topic':'Как проверить путь онлайн-записи', 'platform':p,
                  'format':'article', 'day_offset':3+i, 'hour':18, 'prepare_lead_hours':24,
                  'audience':'Владельцы сервисного бизнеса', 'objective':'Получить релевантные обращения',
                  'rationale':'Начальный график для проверки, без утверждения оптимального часа.',
                  'success_metric':'leads', 'timing_basis':'test_hypothesis', 'evidence_refs':[]}
                 for i,p in enumerate(['own_site','dzen'])]}


class Borrow:
    def __init__(self, db): self.db=db
    def __enter__(self): return self.db
    def __exit__(self, *args): pass


def setup(db, tmp_path):
    p=Product(name='ServGo',slug='servgo');db.add(p);db.commit()
    settings=Settings(products_root=tmp_path, llm_provider='xai', llm_model='kimi-2.7',
                      viral_research_mode='disabled')
    queue_plan(db,p.id)
    return p,settings


async def make_plan(db, settings, data=None):
    provider=FakeLLMProvider(LLMResponse(content=json.dumps(data or draft(),ensure_ascii=False),model='kimi-2.7',
                                     usage=LLMUsage(input_tokens=12,output_tokens=34)))
    llm=LLMService(privacy_gateway=PrivacyGateway(),provider=provider)
    task=db.scalar(select(Task).where(Task.assigned_agent=='editorial_planner',Task.status==TaskStatus.QUEUED))
    assert await run_planner_once(lambda:Borrow(db),settings,llm)==1
    return db.scalar(select(Artifact).where(Artifact.kind=='editorial_plan',Artifact.task_id==task.id)),provider


@pytest.mark.asyncio
async def test_owner_approval_then_due_preparation_once_and_shared_package(db,tmp_path):
    p,settings=setup(db,tmp_path)
    row,provider=await make_plan(db,settings)
    plan=json.loads(row.content)
    due=datetime.fromisoformat(plan['slots'][0]['prepare_at'])
    assert tick_calendar(db,settings,due)==0
    assert not list(db.scalars(select(Task).where(Task.assigned_agent=='content_factory')))
    approve_plan(db,settings,row.id)
    assert tick_calendar(db,settings,due-timedelta(seconds=1))==0
    assert tick_calendar(db,settings,due)==1
    assert tick_calendar(db,settings,due)==0
    tasks=list(db.scalars(select(Task).where(Task.assigned_agent=='content_factory')))
    assert len(tasks)==1
    request=db.scalar(select(Artifact).where(Artifact.task_id==tasks[0].id,Artifact.kind=='content_request'))
    material=json.loads(request.content)
    assert material['audience']=='Владельцы сервисного бизнеса'
    assert 'релевантные обращения' in material['editorial_brief'] and 'dzen' in material['editorial_brief']
    stored=json.loads(row.content)
    assert {s['task_id'] for s in stored['slots']}=={str(tasks[0].id)}
    tasks[0].status=TaskStatus.COMPLETED;db.commit()
    tick_calendar(db,settings,due+timedelta(minutes=1))
    assert all(s['state']=='ready_for_review' for s in json.loads(row.content)['slots'])
    assert list(db.scalars(select(ToolCall)))==list(db.scalars(select(Publication)))==[]
    assert len(list(db.scalars(select(CostRecord))))==1
    assert 'Europe/Moscow' in render_calendar(plan) and 'гипотеза для проверки' in render_calendar(plan)


@pytest.mark.asyncio
async def test_changed_requirements_invalidate_draft_and_pause_blocks_future_jobs(db,tmp_path):
    p,settings=setup(db,tmp_path)
    row,_=await make_plan(db,settings)
    save_preferences(db,p.id,'Есть Дзен, на проверку два часа в неделю.')
    with pytest.raises(ValueError,match='Исходные данные'):
        approve_plan(db,settings,row.id)
    queue_plan(db,p.id)
    row,_=await make_plan(db,settings)
    approve_plan(db,settings,row.id)
    pause_plan(db,row.id)
    due=datetime.fromisoformat(json.loads(row.content)['slots'][0]['prepare_at'])
    assert tick_calendar(db,settings,due)==0
    assert not list(db.scalars(select(Task).where(Task.assigned_agent=='content_factory')))


@pytest.mark.asyncio
async def test_review_queues_new_draft_without_changing_approved_calendar(db,tmp_path):
    p,settings=setup(db,tmp_path)
    row,_=await make_plan(db,settings)
    approve_plan(db,settings,row.id)
    when=datetime.fromisoformat(json.loads(row.content)['review_at'])
    tick_calendar(db,settings,when)
    tick_calendar(db,settings,when)
    tasks=list(db.scalars(select(Task).where(Task.assigned_agent=='editorial_planner',Task.status==TaskStatus.QUEUED)))
    assert len(tasks)==1 and json.loads(row.content)['approval_state']=='active'
    assert all(s['state']=='missed' for s in json.loads(row.content)['slots'])


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['fake_evidence','measured_hour','invalid_format','duplicate_asset'])
async def test_unsupported_claims_fail_without_calendar(db,tmp_path,change):
    p,settings=setup(db,tmp_path)
    value=draft()
    if change=='fake_evidence':value['slots'][0]['evidence_refs']=['invented-study']
    elif change=='measured_hour':value['slots'][0]['timing_basis']='measured_audience'
    elif change=='invalid_format':value['slots'][0]['format']='short_video'
    else:
        extra=copy.deepcopy(value['slots'][0]);extra['day_offset']=8;value['slots'].append(extra)
    row,_=await make_plan(db,settings,value)
    assert row is None
    assert db.scalar(select(Task).where(Task.assigned_agent=='editorial_planner')).status==TaskStatus.FAILED


@pytest.mark.asyncio
async def test_default_planner_guidance_uses_faster_five_channel_sprint(db,tmp_path):
    p,settings=setup(db,tmp_path)
    _,provider=await make_plan(db,settings)
    prompt=provider.requests[0].system_prompt
    assert 'five core package topics across 14 days' in prompt
    assert 'own_site, dzen, telegram, youtube_shorts and instagram_reels' in prompt
    assert 'roughly one new package every three days' in prompt
    assert 'prospect-enrichment/outreach-only gaps' in prompt



def test_latest_snapshots_keep_distinct_urls_and_do_not_sum(db,tmp_path):
    p,settings=setup(db,tmp_path)
    for url,value,days in [('one',10,1),('one',12,0),('two',5,0)]:
        db.add(Metric(product_id=p.id,name='content_views',value=value,source='owner_report',
                      observed_at=datetime.now(timezone.utc)-timedelta(days=days),
                      dimensions={'article_id':'article','channel':'youtube_shorts','url':url,'measurement_type':'cumulative_snapshot'}))
    db.commit()
    context,_=planning_context(db,settings,p.id)
    assert sorted(int(float(o['value'])) for o in context['observations'])==[5,12]
    assert 'Нет' not in context.get('owner_requirements','')


@pytest.mark.asyncio
async def test_new_approved_plan_supersedes_old_and_owner_url_is_report_only(db,tmp_path):
    p,settings=setup(db,tmp_path)
    old,_=await make_plan(db,settings);approve_plan(db,settings,old.id)
    queue_plan(db,p.id);new,_=await make_plan(db,settings)
    approve_plan(db,settings,new.id)
    assert json.loads(old.content)['approval_state']=='superseded'
    due=datetime.fromisoformat(json.loads(new.content)['slots'][0]['prepare_at'])
    tick_calendar(db,settings,due)
    task_id=json.loads(new.content)['slots'][0]['task_id']
    article=Artifact(product_id=p.id,task_id=task_id,kind='article',title='Готово',content='{}')
    # SQLAlchemy UUID expects a UUID, not a string.
    from uuid import UUID
    article.task_id=UUID(task_id);db.add(article);db.flush()
    record_publication(db,article,'dzen','https://example.com/post')
    db.commit()
    slot=next(s for s in json.loads(new.content)['slots'] if s['platform']=='dzen')
    assert slot['publication_reported_by']=='owner' and slot['publication_url'].endswith('/post')


@pytest.mark.asyncio
async def test_telegram_calendar_preferences_preview_and_explicit_approval(db,tmp_path):
    p,settings=setup(db,tmp_path)
    row,_=await make_plan(db,settings)
    class Bot:
        def __init__(self):self.messages=[]
        async def _send(self,chat,text,reply_markup=None):self.messages.append((text,reply_markup))
    bot=Bot();ui=TelegramContent(bot,lambda:Borrow(db),lambda:settings)
    await ui.callback(42,f'content:calendar:{p.id}')
    assert 'Редакторский план' in bot.messages[-1][0]
    await ui.callback(42,f'content:planview:{row.id}')
    assert 'planapprove' in json.dumps(bot.messages[-1][1])
    assert json.loads(row.content)['approval_state']=='draft'
    await ui.callback(42,f'content:planapprove:{row.id}')
    assert json.loads(row.content)['approval_state']=='active'
    for _,keyboard in bot.messages:
        if keyboard:
            assert all(len(button['callback_data'].encode())<=64 for buttons in keyboard['inline_keyboard'] for button in buttons)
    await ui.callback(42,f'content:preferences:{p.id}')
    assert await ui.message({'chat':{'id':42},'text':'Есть Дзен. Проверяю два раза в неделю.'})
    assert db.scalar(select(Artifact).where(Artifact.kind=='editorial_preferences')) is not None


def test_malformed_calendar_cannot_break_worker(db,tmp_path):
    from app.models.domain import ArtifactStatus
    p,settings=setup(db,tmp_path)
    db.add(Artifact(product_id=p.id,kind='editorial_plan',title='Invalid',content='bad json',status=ArtifactStatus.APPROVED))
    db.commit()
    assert tick_calendar(db,settings)==0


@pytest.mark.asyncio
async def test_real_schema_feedback_repairs_once_and_accounts_both_attempts(db,tmp_path):
    p,settings=setup(db,tmp_path)
    bad=draft();bad['slots'][0]['format']='short_video'
    class Provider:
        def __init__(self):self.requests=[]
        async def generate(self,request):
            self.requests.append(request)
            value=bad if len(self.requests)==1 else draft()
            return LLMResponse(content=json.dumps(value),model='kimi-2.7',usage=LLMUsage(input_tokens=12,output_tokens=34))
    provider=Provider();llm=LLMService(privacy_gateway=PrivacyGateway(),provider=provider)
    await run_planner_once(lambda:Borrow(db),settings,llm)
    assert len(provider.requests)==2 and 'validation_errors' in provider.requests[1].messages[-1].content
    assert db.scalar(select(Artifact).where(Artifact.kind=='editorial_plan')) is not None
    assert len(list(db.scalars(select(CostRecord))))==2


def test_interrupted_planner_recovers_once_and_preserves_fresh_calls(db,tmp_path):
    from app.content.calendar import recover_stale_planner
    p,settings=setup(db,tmp_path)
    task=db.scalar(select(Task).where(Task.assigned_agent=='editorial_planner'))
    task.status=TaskStatus.RUNNING
    now=datetime.now(timezone.utc)
    run=AgentRun(task_id=task.id,agent_name='editorial_planner',provider='xai',model='kimi-2.7',
                 status='running',started_at=now-timedelta(minutes=5))
    db.add(run);db.commit()
    assert recover_stale_planner(db,now)==0 and task.status==TaskStatus.RUNNING
    run.started_at=now-timedelta(minutes=13);db.commit()
    assert recover_stale_planner(db,now)==1 and task.status==TaskStatus.QUEUED
    assert run.status=='failed' and run.error=='editorial_plan_interrupted:stale_run'
    assert recover_stale_planner(db,now)==0
    task.status=TaskStatus.RUNNING
    second=AgentRun(task_id=task.id,agent_name='editorial_planner',provider='xai',model='kimi-2.7',
                    status='running',started_at=now-timedelta(minutes=13))
    db.add(second);db.commit()
    assert recover_stale_planner(db,now)==0 and task.status==TaskStatus.FAILED
    assert second.status=='failed' and db.get(AgentRun,run.id).error=='editorial_plan_interrupted:stale_run'


@pytest.mark.asyncio
async def test_invented_evidence_is_repaired_with_exact_allowed_ids(db,tmp_path):
    p,settings=setup(db,tmp_path)
    bad=draft();bad['channels'][0]['evidence_refs']=['https://invented.example/research']
    class Provider:
        def __init__(self):self.requests=[]
        async def generate(self,request):
            self.requests.append(request)
            value=bad if len(self.requests)==1 else draft()
            return LLMResponse(content=json.dumps(value),model='kimi-2.7')
    provider=Provider();llm=LLMService(privacy_gateway=PrivacyGateway(),provider=provider)
    await run_planner_once(lambda:Borrow(db),settings,llm)
    assert len(provider.requests)==2
    feedback=json.loads(provider.requests[1].messages[-1].content)
    allowed=feedback['allowed_evidence_ids']
    assert allowed and 'https://invented.example/research' not in allowed
    schema=provider.requests[0].response_schema
    assert schema['$defs']['ChannelChoice']['properties']['evidence_refs']['items']['enum']==allowed
    assert schema['$defs']['Slot']['properties']['evidence_refs']['items']['enum']==allowed
    assert db.scalar(select(Artifact).where(Artifact.kind=='editorial_plan')) is not None
