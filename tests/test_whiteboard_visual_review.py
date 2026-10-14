"""One-pass advice, author authority, immutable approval, billing and cache safety."""
import json
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api import whiteboard as api
from app.content.whiteboard.checkpoints import CheckpointStore, CheckpointError
from app.content.whiteboard.jobs import queue_job, records, owner_task
from app.content.whiteboard.review import VisualIssue, VisualReview, VisualDecision, IssueDecision, OpenRouterVisualCritic, LLMVisualAuthor
from app.content.whiteboard.runtime import configured_models, ReadinessError
from app.content.whiteboard.worker import run_whiteboard_once
from app.content.whiteboard.__main__ import render_checkpoint
from app.llm.schemas import LLMResponse
from app.models.domain import AgentRun, Task, TaskStatus
from test_whiteboard import storyboard, request, PNG
from test_whiteboard_checkpoints import MODELS
from test_whiteboard_jobs import environment, client, fake_factory, approve, ready
from test_whiteboard_render import tone


def issue(index=0, *, severity='major', category='mechanics', confidence='high'):
    return VisualIssue(id=f's{index}-hook', scene_index=index, beat_index=0,
        severity=severity, confidence=confidence, category=category,
        evidence='Крюк нарисован под носом самолёта вместо хвостовой части.',
        correction='Показать крюк под задней частью самолёта, направленный к тросу.')


def review(*issues):
    return VisualReview(checked_scenes=[0, 1, 2], issues=list(issues))


def decision(report, *, accept=True):
    return VisualDecision(decisions=[IssueDecision(issue_id=i.id, accept=accept and i.eligible,
        reason='Это существенное несоответствие исходной механике.' if accept and i.eligible
        else 'Совет не требуется для исходной задачи и не принят.') for i in report.issues])


def pipeline_factory(seen, report, *, fail=False, selected=True):
    def build(settings, payload, store):
        p = fake_factory(settings, payload, store)
        p.visual_review = True; p.max_api_calls = payload.max_api_calls
        original = p.images
        class Images:
            async def generate(self, *args):
                seen.append('image'); return await original.generate(*args)
            async def revise(self, scene, req, corrections):
                seen.append(('revise', corrections[0].scene_index))
                assert all(i.eligible for i in corrections)
                assert scene == storyboard().scenes[corrections[0].scene_index]
                # Distinct valid PNG: only the chosen scene changes.
                from PIL import Image
                import io
                image = Image.new('RGB', (180, 320), '#ddd')
                output = io.BytesIO(); image.save(output, format='PNG'); return output.getvalue()
        class Critic:
            async def review(self, story, images):
                seen.append('critic'); assert len(images) == 3 and seen[:3] == ['image'] * 3
                if fail: raise TimeoutError('billing unknown')
                return report
        class Author:
            async def decide(self, story, advice):
                seen.append('author'); return decision(advice, accept=selected)
        original_regions = p.regions
        class Regions:
            async def detect(self, scene, image):
                seen.append(('regions', image))
                return await original_regions.detect(scene, image)
        p.images = Images(); p.critic = Critic(); p.visual_author = Author(); p.regions = Regions()
        return p
    return build


def reviewed_job(environment, maximum=19):
    factory, settings, product, _ = environment
    with factory() as db:
        task = queue_job(db, request(product_id=product), configured_models(settings),
                         max_api_calls=maximum, visual_review=True)
        return task.id


@pytest.mark.asyncio
async def test_worker_reviews_all_scenes_once_author_selects_one_and_detects_final_images(environment):
    factory, settings, product, _ = environment
    task = reviewed_job(environment); approve(environment, task)
    seen=[]
    report = review(issue(), issue(1, category='style'), issue(2, confidence='uncertain'))
    build = pipeline_factory(seen, report)
    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    assert seen.count('critic') == seen.count('author') == 1
    assert [x for x in seen if isinstance(x, tuple) and x[0]=='revise'] == [('revise',0)]
    regions = [x[1] for x in seen if isinstance(x, tuple) and x[0]=='regions']
    assert regions[0] != regions[1] == regions[2]
    with factory() as db:
        saved_task = db.get(Task, task)
        assert saved_task.status == TaskStatus.COMPLETED
        state = json.loads(records(db, saved_task)[1].content)
        assert state['calls'] == 16  # 13 base + critic + author + one redraw
        assert state['progress_done'] == state['progress_total'] == 17
        audit = state['visual_review_result']
        assert audit['critic_passes']==1 and audit['redrawn_scenes']==[0]
        assert not audit['revised_images_reviewed_again'] and audit['human_review_required']
        for index, hashes in enumerate(audit['images']):
            assert (hashes['original_sha256'] != hashes['final_sha256']) == (index==0)
        runs=list(db.scalars(select(AgentRun)))
        assert sum(r.agent_name=='whiteboard_image_revision' for r in runs)==1
        assert sum(r.agent_name=='whiteboard_visual_review' for r in runs)==1
        assert sum(r.agent_name=='whiteboard_visual_decision' for r in runs)==1
        directory=store_path(settings, product, task)
        _,_,_,payload=records(db,saved_task)
    before=list(seen)
    store=CheckpointStore(directory,payload.request,models=payload.models.model_dump(),visual_review=True)
    replay=build(settings,payload,store)
    await replay.produce(payload.request,directory.parent/'replay.mp4',approval_verified=True)
    assert seen==before  # No repeated paid review or redraw, even on an offline replay.
    with client() as browser:
        page=browser.get(f'/studio/products/{product}/whiteboard/{task}')
        assert 'Крюк нарисован' in page.text and 'Решение автора' in page.text
        assert 'Второго прохода критика' in page.text


def store_path(settings, product, task):
    from app.content.whiteboard.jobs import job_directory
    return job_directory(settings.products_root,product,task)/'stages'


@pytest.mark.asyncio
@pytest.mark.parametrize('report', [review(), review(issue(severity='minor')), review(issue(category='style')), review(issue(confidence='uncertain'))])
async def test_no_essential_advice_skips_author_and_all_redraws(environment, report):
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task)
    seen=[]
    await run_whiteboard_once(factory,settings,factory=pipeline_factory(seen,report),readiness=ready)
    assert seen.count('critic')==1 and 'author' not in seen
    assert not any(isinstance(x,tuple) and x[0]=='revise' for x in seen)
    with factory() as db:
        t=db.get(Task,task);assert t.status==TaskStatus.COMPLETED
        assert json.loads(records(db,t)[1].content)['calls']==14


@pytest.mark.asyncio
async def test_author_can_reject_major_advice_without_redrawing(environment):
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task);seen=[]
    await run_whiteboard_once(factory,settings,factory=pipeline_factory(seen,review(issue()),selected=False),readiness=ready)
    assert seen.count('author')==1 and not any(isinstance(x,tuple) and x[0]=='revise' for x in seen)
    with factory() as db:
        assert json.loads(records(db,db.get(Task,task))[1].content)['calls']==15


@pytest.mark.asyncio
async def test_all_three_redraws_are_bounded_and_charged_once(environment):
    factory,settings,product,_=environment;task=reviewed_job(environment,maximum=18);approve(environment,task);seen=[]
    await run_whiteboard_once(factory,settings,factory=pipeline_factory(seen,review(*(issue(i) for i in range(3)))),readiness=ready)
    assert seen.count('critic')==1 and seen.count('author')==1
    assert len([x for x in seen if isinstance(x,tuple) and x[0]=='revise'])==3
    with factory() as db:
        t=db.get(Task,task);assert t.status==TaskStatus.COMPLETED
        assert json.loads(records(db,t)[1].content)['calls']==18


@pytest.mark.asyncio
async def test_failed_critic_stops_before_speech_and_is_not_retried(environment):
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task);seen=[]
    build=pipeline_factory(seen,review(),fail=True)
    await run_whiteboard_once(factory,settings,factory=build,readiness=ready)
    assert await run_whiteboard_once(factory,settings,factory=build,readiness=ready)==0
    assert seen==['image','image','image','critic']
    with factory() as db:
        t=db.get(Task,task);state=json.loads(records(db,t)[1].content)
        assert t.status==TaskStatus.FAILED and state['calls']==5 and state['failed_stage']=='visual_review'


def test_review_is_immutable_and_insufficient_budget_is_rejected_before_queue(environment):
    factory,settings,product,_=environment
    with client() as browser:
        index=browser.get(f'/studio/products/{product}/whiteboard')
        assert 'value="once" selected' in index.text and 'value="19" selected' in index.text
        rejected=browser.post(f'/products/{product}/whiteboard/jobs',json={'topic':'Почему самолёт садится на авианосец','max_api_calls':13,'visual_review':True})
        assert rejected.status_code==422
    with factory() as db:assert list(db.scalars(select(Task)))==[]
    task=reviewed_job(environment)
    with factory() as db:
        t=owner_task(db,product,task);source,_,_,payload=records(db,t)
        assert payload.visual_review
        source.content=source.content.replace('"visual_review":true','"visual_review":false');db.commit()
        with pytest.raises(ReadinessError,match='approval_invalid'):records(db,t)


def test_policy_change_cannot_reuse_old_approved_media(tmp_path):
    req=request();CheckpointStore(tmp_path/'old',req,models=MODELS)
    with pytest.raises(CheckpointError):CheckpointStore(tmp_path/'old',req,models=MODELS,visual_review=True)


def test_author_cannot_accept_minor_or_unknown_advice():
    for item in [issue(severity='minor'),issue(category='style'),issue(confidence='uncertain')]:
        result=VisualDecision(decisions=[IssueDecision(issue_id=item.id,accept=True,reason='Неверное решение в тесте должно быть остановлено.')])
        with pytest.raises(ValueError):result.check_for(review(item))
    with pytest.raises(ValueError):VisualDecision(decisions=[]).check_for(review(issue()))
    with pytest.raises(ValidationError):review(issue(),issue())


def test_substantial_issues_are_not_limited_to_three_or_twelve():
    report=review(*[issue().model_copy(update={'id':f's0-hook-{i}'}) for i in range(15)])
    result=decision(report)
    assert len(result.accepted(report,0))==15


@pytest.mark.asyncio
async def test_vision_adapter_sends_three_images_in_one_request_and_rejects_incomplete_reply():
    seen=[]
    def handler(req):
        data=json.loads(req.content);seen.append(data)
        assert sum(x['type']=='image_url' for x in data['messages'][0]['content'])==3
        return httpx.Response(200,json={'choices':[{'message':{'content':review(issue()).model_dump_json()}}]})
    async with httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/',transport=httpx.MockTransport(handler)) as c:
        got=await OpenRouterVisualCritic(api_key='test',model='vision',client=c).review(storyboard(),(PNG,)*3)
        assert got.issues[0].category=='mechanics'
    assert len(seen)==1


@pytest.mark.asyncio
async def test_primary_author_uses_own_model_and_cannot_rewrite_story():
    seen=[]
    class Provider:
        async def generate(self, req):
            seen.append(req)
            return LLMResponse(model=req.model,content=decision(review(issue())).model_dump_json())
    author=LLMVisualAuthor(Provider(),model='primary-author')
    got=await author.decide(storyboard(),review(issue()))
    assert got.accepted(review(issue()),0) and seen[0].model=='primary-author'
    assert 'storyboard' in json.loads(seen[0].messages[0].content)


@pytest.mark.asyncio
async def test_real_mp4_and_offline_rerender_keep_the_selected_redraw(environment, tmp_path, tone):
    from PIL import Image
    from app.content.whiteboard.render import MarkerSweepRenderer
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task);seen=[]
    base=pipeline_factory(seen,review(issue()))
    def build(settings,payload,store):
        p=base(settings,payload,store)
        class Speech:
            async def synthesize(self,*args,**kwargs):return tone
        p.speech=Speech();p.renderer=MarkerSweepRenderer(width=180,height=320,fps=5)
        return p
    await run_whiteboard_once(factory,settings,factory=build,readiness=ready)
    directory=store_path(settings,product,task)
    with factory() as db:
        t=db.get(Task,task);assert t.status==TaskStatus.COMPLETED
    meta=json.loads((directory.parent/'output/video.json').read_text())
    assert meta['visual_review']['redrawn_scenes']==[0]
    before=list(seen)
    replay=render_checkpoint(directory,tmp_path/'replay.mp4',width=180,height=320,fps=5)
    assert replay['provider_calls']==0 and seen==before
    assert json.loads((tmp_path/'replay.json').read_text())['visual_review']==meta['visual_review']
    with Image.open(tmp_path/'replay.png') as cover:
        assert cover.convert('RGB').getpixel((12,20))==(221,221,221)


@pytest.mark.asyncio
async def test_cancel_during_critic_prevents_primary_decision_and_redraw(environment):
    from app.content.whiteboard.jobs import cancel_job
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task);seen=[]
    base=pipeline_factory(seen,review(issue()))
    def build(settings,payload,store):
        p=base(settings,payload,store);original=p.critic
        class Critic:
            async def review(self,*args):
                result=await original.review(*args)
                with factory() as db:cancel_job(db,product,task)
                return result
        p.critic=Critic();return p
    await run_whiteboard_once(factory,settings,factory=build,readiness=ready)
    assert seen==['image','image','image','critic']
    with factory() as db:
        t=db.get(Task,task);assert t.status==TaskStatus.CANCELLED
        assert json.loads(records(db,t)[1].content)['calls']==5


@pytest.mark.asyncio
async def test_adversarial_author_cannot_expand_scope_into_stylistic_redraws(environment):
    factory,settings,product,_=environment;task=reviewed_job(environment);approve(environment,task);seen=[]
    report=review(issue(),issue(1,category='style'))
    base=pipeline_factory(seen,report)
    def build(settings,payload,store):
        p=base(settings,payload,store)
        class Author:
            async def decide(self,*args):
                seen.append('author')
                return VisualDecision(decisions=[IssueDecision(issue_id=i.id,accept=True,
                    reason='Попытка принять всё должна остановить платную перерисовку.') for i in report.issues])
        p.visual_author=Author();return p
    await run_whiteboard_once(factory,settings,factory=build,readiness=ready)
    assert seen==['image','image','image','critic','author']
    with factory() as db:
        t=db.get(Task,task);assert t.status==TaskStatus.FAILED
        assert json.loads(records(db,t)[1].content)['calls']==6
