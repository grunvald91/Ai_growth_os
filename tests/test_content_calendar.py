import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.content.calendar import (EditorialPlan, _expand_anchor_slots, approve_plan, pause_plan, plan_record, planning_context,
    queue_plan, record_publication, render_calendar, run_planner_once, save_preferences, tick_calendar, reschedule_slot)
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


def test_compact_anchor_expands_across_tested_channels():
    value=draft()
    value['channels']=[
        {'platform':p,'decision':'test','rationale':'Проверить канал на одном общем пакете без обещания результата.','evidence_refs':[]}
        for p in ['own_site','dzen','telegram','youtube_shorts','instagram_reels']
    ]
    value['slots']=[copy.deepcopy(value['slots'][0])]
    value['slots'][0]['day_offset']=3
    plan=_expand_anchor_slots(EditorialPlan.model_validate(value))
    assert len(plan.slots)==5
    by_platform={slot.platform:slot for slot in plan.slots}
    assert set(by_platform)=={'own_site','dzen','telegram','youtube_shorts','instagram_reels'}
    assert by_platform['own_site'].day_offset==3
    assert by_platform['dzen'].day_offset==3
    assert by_platform['telegram'].day_offset==4
    assert by_platform['youtube_shorts'].format=='short_video'
    assert by_platform['instagram_reels'].day_offset==5
    assert by_platform['instagram_reels'].prepare_lead_hours==72


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
async def test_viral_research_runs_before_editorial_planner_and_enters_evidence(db,tmp_path,monkeypatch):
    p,settings=setup(db,tmp_path)
    settings.viral_research_mode='youtube_public'
    events=[]

    async def fake_viral_research(*, db, settings, llm, task, context):
        events.append('viral')
        row=Artifact(
            product_id=task.product_id,
            task_id=task.id,
            kind='viral_content_research',
            title='Viral/trend content research before editorial plan',
            content=json.dumps({
                'source_status':'evidence_available',
                'analysis_status':'completed',
                'research_quality':'strong_multisource',
                'niche':'салоны красоты',
                'summary':'Повторяется боль долгой переписки до записи.',
                'video_candidates':[{'source_id':'yt:demo','title':'Сколько сообщений до записи?','view_count':500000,'outlier_ratio':3.2,'viral_score':88,'age_days':3,'evidence_confidence':'high'}],
                'article_candidates':[{'source_id':'article:1','title':'Как сократить ручную запись','publisher':'Example','cross_source_hits':3,'trend_score':82,'age_days':2,'evidence_confidence':'high'}],
                'audience_pains':[{'pain':'Долгая переписка','source_ids':['yt:demo'],'why_it_matters':'Клиент ждёт','product_match':'hypothesis','product_fact_id':None,'product_match_explanation':'Нужно проверить','presentation_angle':'Показать путь','humor_angle':'Чат как CRM'}],
                'hook_patterns':[{'pattern':'Сколько сообщений до записи?', 'source_ids':['yt:demo']}],
                'seo_topic_seeds':['как сократить переписку при записи'],
            },ensure_ascii=False),
        )
        db.add(row);db.commit();db.refresh(row)
        return row

    monkeypatch.setattr('app.content.calendar.run_viral_content_research', fake_viral_research)

    class Provider:
        def __init__(self): self.requests=[]
        async def generate(self,request):
            events.append('planner')
            self.requests.append(request)
            return LLMResponse(content=json.dumps(draft(),ensure_ascii=False),model='kimi-2.7',
                               usage=LLMUsage(input_tokens=12,output_tokens=34))

    provider=Provider()
    llm=LLMService(privacy_gateway=PrivacyGateway(),provider=provider)
    assert await run_planner_once(lambda:Borrow(db),settings,llm)==1

    assert events[:2]==['viral','planner']
    payload=json.loads(provider.requests[0].messages[0].content)
    research=[item for item in payload['research'] if item['kind']=='viral_content_research']
    assert research and 'долгой переписки' in research[0]['bounded_excerpt']
    plan=db.scalar(select(Artifact).where(Artifact.kind=='editorial_plan'))
    stored=json.loads(plan.content)
    assert any(source.get('kind')=='viral_content_research' for source in stored['sources'])


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
    assert 'ONE anchor slot per package' in prompt
    assert 'viral_content_research' in prompt
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
    run.started_at=now-timedelta(minutes=21);db.commit()
    assert recover_stale_planner(db,now)==1 and task.status==TaskStatus.QUEUED
    assert run.status=='failed' and run.error=='editorial_plan_interrupted:stale_run'
    assert recover_stale_planner(db,now)==0
    task.status=TaskStatus.RUNNING
    second=AgentRun(task_id=task.id,agent_name='editorial_planner',provider='xai',model='kimi-2.7',
                    status='running',started_at=now-timedelta(minutes=21))
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


def test_reschedule_one_slot_preserves_package_and_rejects_collisions(db):
    company = Product(name='Кафе', slug='cafe-calendar-move')
    db.add(company)
    db.flush()
    original = datetime(2026, 10, 15, 12, tzinfo=timezone(timedelta(hours=3)))
    base = {
        'generated_at':'2026-10-11T00:00:00+03:00',
        'approval_state':'active', 'timezone':'Europe/Moscow', 'horizon_days':14,
        'slots':[
            {
                'slot_id':'1', 'package_key':'campaign-1', 'platform':'telegram',
                'topic':'Сезонное меню', 'publish_at':original.isoformat(),
                'prepare_at':(original-timedelta(hours=24)).isoformat(),
                'day_offset':3, 'hour':12, 'minute':0, 'prepare_lead_hours':24,
                'state':'ready_for_review', 'task_id':None,
            },
            {
                'slot_id':'2', 'package_key':'campaign-2', 'platform':'telegram',
                'topic':'Кофе', 'publish_at':(original+timedelta(days=2)).isoformat(),
                'prepare_at':(original+timedelta(days=1)).isoformat(),
                'day_offset':5, 'hour':12, 'minute':0, 'prepare_lead_hours':24,
                'state':'planned', 'task_id':None,
            },
        ],
    }
    row = Artifact(product_id=company.id, kind='editorial_plan', title='План',
                   content=json.dumps(base), status='approved')
    db.add(row)
    db.commit()
    fixed_now = datetime(2026, 10, 11, 8, tzinfo=timezone.utc)
    dest = datetime(2026, 10, 16, 15, tzinfo=timezone(timedelta(hours=3)))
    moved = reschedule_slot(db, row.id, '1', dest, now=fixed_now)
    assert moved['publish_at'] == dest.isoformat()
    assert moved['day_offset'] == 4
    assert moved['hour'] == 15
    assert moved['prepare_at'] == (dest-timedelta(hours=24)).isoformat()
    assert moved['topic'] == 'Сезонное меню'
    assert moved['state'] == 'ready_for_review'
    assert json.loads(row.content)['slots'][1]['topic'] == 'Кофе'
    with pytest.raises(ValueError, match='уже есть материал'):
        reschedule_slot(
            db, row.id, '1', original+timedelta(days=2), now=fixed_now,
        )
    with pytest.raises(ValueError, match='периода этого плана'):
        reschedule_slot(
            db, row.id, '1', original+timedelta(days=20), now=fixed_now,
        )
    with pytest.raises(ValueError, match='в будущем'):
        reschedule_slot(
            db, row.id, '1', original-timedelta(days=5), now=fixed_now,
        )


def test_planning_context_knows_verified_telegram_channels_without_credentials(db, tmp_path):
    company = Product(name='Пекарня', slug='bakery-channels')
    db.add(company)
    db.flush()
    db.add(Artifact(
        product_id=company.id, kind='telegram_channel_connection',
        title='Новости пекарни', status='approved',
        content=json.dumps({
            'chat_id': '-1001234567890', 'title': 'Новости пекарни',
            'verified_at': '2026-10-11T00:00:00+00:00',
        }, ensure_ascii=False),
    ))
    db.commit()
    context, digest = planning_context(
        db, Settings(products_root=tmp_path), company.id,
    )
    assert context['verified_telegram_channels'][0]['name'] == 'Новости пекарни'
    assert context['verified_telegram_channels'][0]['scheduled_publication_requires_owner_approval'] is True
    assert 'chat_id' not in context['verified_telegram_channels'][0]
    assert isinstance(digest, str) and len(digest) == 64


@pytest.mark.asyncio
async def test_planner_critic_keeps_advisory_findings_and_unknown_tokens(db, tmp_path):
    product, settings = setup(db, tmp_path)
    settings.agent_critic_enabled = True
    task = db.scalar(select(Task).where(
        Task.assigned_agent == 'editorial_planner',
        Task.status == TaskStatus.QUEUED,
    ))

    class PlannerWithCritic:
        def __init__(self):
            self.requests = []
        async def generate(self, request):
            self.requests.append(request)
            if request.metadata.get('task_type') == 'quality_review':
                payload = {'findings': [{
                    'id': 'important1', 'severity': 'major',
                    'problem': 'The proposed calendar omits a necessary editorial constraint',
                    'evidence_in_draft': 'No restriction on the requested channel',
                    'proposed_fix': 'Keep the company-required channel restriction',
                    'link_to_original_goal': 'Original owner request requires a restricted channel',
                }]}
                # Intentionally no usage despite successful response.
                return LLMResponse(content=json.dumps(payload), model='critic-no-usage',
                                   usage=None, stop_reason='end_turn')
            return LLMResponse(content=json.dumps(draft(), ensure_ascii=False),
                               model='planner', usage=LLMUsage(input_tokens=24, output_tokens=34),
                               stop_reason='end_turn')

    provider = PlannerWithCritic()
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                     critic_enabled=True)
    assert await run_planner_once(lambda: Borrow(db), settings, llm) == 1
    assert len(provider.requests) == 2
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
    plan = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == 'editorial_plan',
    ))
    assert plan is not None
    reviews = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == 'agent_quality_review',
    )))
    assert len(reviews) == 1
    review = json.loads(reviews[0].content)
    assert review['status'] == 'advisory_only_no_domain_validator'
    assert review['findings'][0]['id'] == 'important1'
    assert review['extra_usage_by_model'][0]['input_tokens'] is None
    run = db.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    assert {'kind': 'artifact', 'id': str(reviews[0].id)} in run.output_references
    costs = list(db.scalars(select(CostRecord).where(CostRecord.agent_run_id == run.id)))
    assert len(costs) == 2
    author = next(c for c in costs if c.details.get("llm_stage") == "author")
    critic = next(c for c in costs if c.details.get("llm_stage") == "critic")
    assert author.input_tokens == 24
    assert critic.input_tokens is None
    assert critic.details["usage_incomplete"] is True

@pytest.mark.asyncio
async def test_planner_timeout_keeps_attempt_audit_with_null_not_zero_tokens(db, tmp_path):
    product, settings = setup(db, tmp_path)
    settings.agent_critic_enabled = True
    task = db.scalar(select(Task).where(
        Task.assigned_agent == 'editorial_planner',
        Task.status == TaskStatus.QUEUED,
    ))

    class TimedOutProvider:
        def __init__(self):
            self.calls = 0
        async def generate(self, request):
            self.calls += 1
            raise TimeoutError('Simulated provider timeout')

    provider = TimedOutProvider()
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                     critic_enabled=True)
    assert await run_planner_once(lambda: Borrow(db), settings, llm) == 1
    assert provider.calls == 1
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    events = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == 'agent_quality_review',
    )))
    assert len(events) == 1
    audit = json.loads(events[0].content)
    assert audit['status'] == 'provider_timeout'
    assert audit['usage_known'] is False
    run = db.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    assert {'kind': 'artifact', 'id': str(events[0].id)} in run.output_references
    records = list(db.scalars(select(CostRecord).where(CostRecord.agent_run_id == run.id)))
    assert len(records) == 1
    assert records[0].input_tokens is None and records[0].output_tokens is None
    assert records[0].details['usage_incomplete'] is True


@pytest.mark.asyncio
async def test_cancelled_second_planner_attempt_preserves_first_critic_audit_and_cost(db, tmp_path):
    """Regression: cancellation after reviewer major finding must not discard paid calls."""
    import asyncio

    product, settings = setup(db, tmp_path)
    settings.agent_critic_enabled = True
    task = db.scalar(select(Task).where(
        Task.assigned_agent == 'editorial_planner',
        Task.status == TaskStatus.QUEUED,
    ))

    invalid_plan = draft()
    invalid_plan['slots'][0]['day_offset'] = 14  # Must be < horizon_days (14).
    finding = {
        'findings': [{
            'id': 'calendar-14', 'severity': 'major',
            'problem': 'Scheduled content falls outside the requested planning horizon',
            'evidence_in_draft': 'day_offset=14 with a 14-day horizon',
            'proposed_fix': 'Move the slot inside the 14-day horizon',
            'link_to_original_goal': 'The approved plan must stay within fourteen days',
        }],
    }

    class CancelDuringRetry:
        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                assert request.metadata['task_type'] == 'content_calendar'
                return LLMResponse(
                    content=json.dumps(invalid_plan, ensure_ascii=False),
                    model='planner', usage=LLMUsage(input_tokens=24, output_tokens=34),
                    stop_reason='end_turn',
                )
            if len(self.requests) == 2:
                assert request.metadata['task_type'] == 'quality_review'
                return LLMResponse(
                    content=json.dumps(finding), model='critic',
                    usage=LLMUsage(input_tokens=6, output_tokens=8),
                    stop_reason='end_turn',
                )
            assert len(self.requests) == 3
            assert request.metadata['task_type'] == 'content_calendar'
            raise asyncio.CancelledError()

    provider = CancelDuringRetry()
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                     critic_enabled=True)

    with pytest.raises(asyncio.CancelledError):
        await run_planner_once(lambda: Borrow(db), settings, llm)

    assert len(provider.requests) == 3
    assert 'validation_errors' in provider.requests[2].messages[-1].content
    db.refresh(task)
    assert task.status == TaskStatus.FAILED
    assert db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == 'editorial_plan',
    )) is None

    run = db.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    assert run is not None and run.status == 'failed'
    assert run.finished_at is not None

    audit_artifacts = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == 'agent_quality_review',
    )))
    assert len(audit_artifacts) == 2
    audits = [json.loads(row.content) for row in audit_artifacts]
    review = next(a for a in audits if a['status'] == 'advisory_only_no_domain_validator')
    cancelled = next(a for a in audits if a['status'] == 'provider_cancelled')
    assert review['findings'][0]['id'] == 'calendar-14'
    assert review['extra_usage_by_model'][0]['model'] == 'critic'
    assert cancelled['attempt'] == 2 and cancelled['usage_known'] is False
    assert all({'kind': 'artifact', 'id': str(a.id)} in run.output_references
               for a in audit_artifacts)

    costs = list(db.scalars(select(CostRecord).where(CostRecord.agent_run_id == run.id)))
    assert len(costs) == 3
    author = next(r for r in costs if r.details.get("llm_stage") == "author")
    critic = next(r for r in costs if r.details.get("llm_stage") == "critic")
    pending = next(r for r in costs if r.details.get("review_status") == "provider_cancelled")
    assert author.input_tokens == 24 and author.output_tokens == 34
    assert critic.input_tokens == 6 and critic.output_tokens == 8
    assert pending.input_tokens is None and pending.output_tokens is None
    assert pending.details["usage_incomplete"] is True
