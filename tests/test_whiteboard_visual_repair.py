"""Visual-only repair retains every original MP3 and refuses new speech."""
import hashlib
import json

import pytest
from sqlalchemy import select
from app.api import whiteboard as api

from app.content.whiteboard.jobs import records, owner_task, job_directory, resume_job, digest
from app.content.whiteboard.repair import queue_visual_repair
from app.content.whiteboard.runtime import ReadinessError
from app.content.whiteboard.worker import run_whiteboard_once
from app.models.domain import Task,TaskStatus
from test_whiteboard_jobs import environment,new_job,approve,ready,fake_factory,client
from test_whiteboard_visual_review import issue,review,decision


@pytest.mark.asyncio
async def test_visual_only_repair_generates_new_images_without_story_or_tts_and_preserves_audio(environment):
    factory,settings,product,_=environment
    source_id=new_job(environment,maximum=25);approve(environment,source_id)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    original=job_directory(settings.products_root,product,source_id)/'stages'
    original_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in original.glob('*speech.data')}
    with factory() as db:
        repair_id=queue_visual_repair(db,settings,product,source_id).id
        _,state,approval,payload=records(db,owner_task(db,product,repair_id))
        assert payload.visual_repair_from==source_id and payload.inherited_calls==13
        assert payload.repair_regenerate_images and payload.max_api_calls==13
        assert json.loads(state.content)['calls']==0 and approval.status=='pending'
        assert json.loads(state.content)['attempts_started']==0
        assert queue_visual_repair(db,settings,product,source_id).id==repair_id
    with client() as browser:
        page=browser.get(f'/studio/products/{product}/whiteboard/{repair_id}')
        assert 'Новая озвучка запрещена' in page.text and 'В исходном задании было 13 API-вызовов' in page.text
    approve(environment,repair_id)
    seen=[]
    report=review(*(issue(i) for i in range(3)))
    def build(settings,payload,store):
        p=fake_factory(settings,payload,store);p.visual_review=True;p.max_api_calls=payload.max_api_calls
        initial=p.images
        class Images:
            async def generate(self,*args):seen.append('new image');return await initial.generate(*args)
            async def revise(self,*args):seen.append('redraw');return await initial.generate(*args[:2])
        class Planner:
            async def plan(self,*args):pytest.fail('Original spoken story must never be regenerated')
        class Speech:
            async def synthesize(self,*args,**kwargs):pytest.fail('TTS must never run in a visual repair')
        class Critic:
            async def review(self,*args):seen.append('critic');return report
        class Author:
            async def decide(self,*args):seen.append('author');return decision(report)
        renderer=p.renderer
        class Renderer:
            async def render(self,scenes,*args):
                actual=[hashlib.sha256(audio).hexdigest() for s in scenes for audio in s.narration_chunks]
                assert sorted(actual)==sorted(original_hashes.values())
                return await renderer.render(scenes,*args)
        p.images=Images();p.storyboard=Planner();p.speech=Speech();p.critic=Critic();p.visual_author=Author();p.renderer=Renderer()
        return p
    await run_whiteboard_once(factory,settings,factory=build,readiness=ready)
    assert seen==['new image','new image','new image','critic','author','redraw','redraw','redraw']
    new=job_directory(settings.products_root,product,repair_id)/'stages'
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in new.glob('*speech.data')}==original_hashes
    assert (new/'storyboard.data').read_bytes()==(original/'storyboard.data').read_bytes()
    with factory() as db:
        t=owner_task(db,product,repair_id);assert t.status==TaskStatus.COMPLETED
        assert json.loads(records(db,t)[1].content)['calls']==11  # 3 new + 3 regions + critic + author + 3 revisions
        next_repair=queue_visual_repair(db,settings,product,source_id)
        assert next_repair.id != repair_id and next_repair.status == TaskStatus.WAITING_APPROVAL
        with pytest.raises(ReadinessError):queue_visual_repair(db,settings,product,repair_id)
    assert len(original_hashes)==6


@pytest.mark.asyncio
async def test_missing_original_audio_blocks_before_any_new_provider(environment):
    factory,settings,product,_=environment;source=new_job(environment,maximum=25);approve(environment,source)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    with factory() as db:repair=queue_visual_repair(db,settings,product,source).id
    approve(environment,repair)
    path=job_directory(settings.products_root,product,source)/'stages/scene-00-beat-00-speech.data'
    path.write_bytes(b'corrupt')
    def never(*args):pytest.fail('Corrupt audio must block before constructing any media provider')
    await run_whiteboard_once(factory,settings,factory=never,readiness=ready)
    with factory() as db:
        t=owner_task(db,product,repair);assert t.status==TaskStatus.FAILED
        assert json.loads(records(db,t)[1].content)['calls']==0


@pytest.mark.asyncio
async def test_repair_endpoint_is_owner_scoped_and_creates_only_one_pending_approval(environment):
    factory,settings,product,other=environment;source=new_job(environment,maximum=25);approve(environment,source)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    with client() as browser:
        assert browser.post(f'/studio/products/{other}/whiteboard/{source}/repair').status_code==404
        path=f'/studio/products/{product}/whiteboard/{source}/repair'
        a=browser.post(path,follow_redirects=False);b=browser.post(path,follow_redirects=False)
        assert a.status_code==b.status_code==303 and a.headers['location']==b.headers['location']
        repair_page=browser.get(a.headers['location'])
        assert repair_page.status_code==200
        assert 'Задание на перерисовку создано' in repair_page.text
        assert 'Подтвердить перерисовку' in repair_page.text
        assert 'Новая озвучка запрещена' in repair_page.text
        jobs=browser.get(f'/studio/products/{product}/whiteboard/jobs.json').json()
        repair_job=next(item for item in jobs if item.get('visual_repair_from'))
        assert repair_job['repair_regenerate_images'] is True
        assert repair_job['status']=='waiting_approval' and repair_job['calls']==0
        index=browser.get(f'/studio/products/{product}/whiteboard')
        assert 'Перерисовка ·' in index.text and 'Подтвердить перерисовку' in index.text
    with factory() as db:
        tasks=list(db.scalars(select(Task)));assert len(tasks)==2
        pending=next(t for t in tasks if t.id!=source)
        assert pending.status==TaskStatus.WAITING_APPROVAL
        assert records(db,pending)[2].status=='pending'


@pytest.mark.asyncio
async def test_visual_repair_has_a_new_separately_approved_budget_despite_spent_source_attempts(environment):
    factory,settings,product,_=environment
    source=new_job(environment,maximum=25)
    approve(environment,source)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    with factory() as db:
        task=owner_task(db,product,source)
        _,state,_,_=records(db,task)
        value=json.loads(state.content)
        assert value['calls']==13
        # Simulate extra begun provider calls that received HTTP 502,
        # then were refunded only by the historical logical ledger.
        value['attempts_started']=23
        state.content=json.dumps(value)
        db.commit()
        fresh=queue_visual_repair(db,settings,product,source)
        assert fresh.status==TaskStatus.WAITING_APPROVAL
        _,state,approval,payload=records(db,fresh)
        assert payload.inherited_calls==13 and payload.max_api_calls==13
        assert json.loads(state.content)['attempts_started']==0
        assert approval.status=='pending'


@pytest.mark.asyncio
async def test_failed_new_repair_is_reopened_from_saved_checkpoints_instead_of_requeued(environment):
    factory, settings, product, _ = environment
    source = new_job(environment, maximum=25)
    approve(environment, source)
    await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready)
    with factory() as db:
        repair = queue_visual_repair(db, settings, product, source).id
    approve(environment, repair)
    with factory() as db:
        task = owner_task(db, product, repair)
        _, state, _, payload = records(db, task)
        assert payload.repair_regenerate_images
        value = json.loads(state.content)
        value.update(calls=1, attempts_started=1, error='provider_server_error',
                     stage='image', failed_stage='image')
        state.content = json.dumps(value)
        task.status = TaskStatus.FAILED
        db.commit()
        assert queue_visual_repair(db, settings, product, source).id == repair
        resumed = resume_job(db, product, repair, confirmed=True, decided_by='owner')
        assert resumed.status == TaskStatus.QUEUED
        assert json.loads(records(db, resumed)[1].content)['calls'] == 1


@pytest.mark.asyncio
async def test_html_approval_shows_queued_repair_without_starting_paid_calls(environment, monkeypatch):
    factory, settings, product, _ = environment
    source = new_job(environment, maximum=25)
    approve(environment, source)
    await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready)
    with factory() as db:
        repair = queue_visual_repair(db, settings, product, source).id
        _, state, _, payload = records(db, owner_task(db, product, repair))
        request_digest = json.loads(state.content)['request_digest']
        assert payload.repair_regenerate_images
    observed = []
    async def fake_preflight(*args, **kwargs):
        observed.append(kwargs.get('skip_speech'))
        return {'ready': True}
    monkeypatch.setattr(api, 'preflight', fake_preflight)
    with client() as browser:
        response = browser.post(
            f'/studio/products/{product}/whiteboard/{repair}/approve',
            data={'request_digest': request_digest, 'confirmed': 'yes'},
            follow_redirects=False)
        assert response.status_code == 303
        page = browser.get(response.headers['location'])
        assert page.status_code == 200
        assert 'В очереди' in page.text and 'Перерисовка изображений' in page.text
    assert observed == [True]
    with factory() as db:
        task = owner_task(db, product, repair)
        assert task.status == TaskStatus.QUEUED
        _, state, approval, _ = records(db, task)
        assert approval.status == 'approved'
        assert json.loads(state.content)['calls'] == 0


@pytest.mark.asyncio
async def test_unapproved_legacy_repair_does_not_hide_new_regeneration(environment):
    """An old pending repair may not intercept the new image-generation button."""
    factory, settings, product, _ = environment
    source = new_job(environment, maximum=25)
    approve(environment, source)
    await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready)
    with factory() as db:
        old_id = queue_visual_repair(db, settings, product, source).id
        old_task = owner_task(db, product, old_id)
        old_request, old_state, old_approval, old_payload = records(db, old_task)
        _, _, _, source_payload = records(db, owner_task(db, product, source))
        old_request.content = old_payload.model_copy(update={
            'repair_regenerate_images': False,
            'max_api_calls': 25,
            'models': old_payload.models.model_copy(
                update={'regions': source_payload.models.regions}),
        }).model_dump_json()
        old_approval.reason = 'sha256:' + digest(old_request.content)
        old_task.title = 'WHITEBOARD-VISUAL-REPAIR: ' + str(source)
        db.commit()

        created = queue_visual_repair(db, settings, product, source)
        assert created.id != old_id
        assert created.status == TaskStatus.WAITING_APPROVAL
        _, state, approval, request = records(db, created)
        assert request.repair_regenerate_images is True
        assert approval.status == 'pending'
        assert json.loads(state.content)['calls'] == 0

        old_task = owner_task(db, product, old_id)
        _, old_state, old_approval, _ = records(db, old_task)
        assert old_task.status == TaskStatus.CANCELLED
        assert old_approval.status == 'rejected'
        assert json.loads(old_state.content)['superseded_by_new_repair'] is True
        # Further clicks must return the pending new request, never the legacy one.
        assert queue_visual_repair(db, settings, product, source).id == created.id


@pytest.mark.asyncio
async def test_source_with_approved_extra_calls_can_still_regenerate_images(environment):
    factory, settings, product, _ = environment
    source = new_job(environment, maximum=60)
    approve(environment, source)
    await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready)
    with factory() as db:
        task = owner_task(db, product, source)
        _, state, _, _ = records(db, task)
        value = json.loads(state.content)
        # A legitimate source may have used up to 60 + 25 confirmed calls.
        value.update(calls=61, attempts_started=61, extra_calls=5)
        state.content = json.dumps(value)
        db.commit()

        repair = queue_visual_repair(db, settings, product, source)
        _, repair_state, approval, payload = records(db, repair)
        assert payload.inherited_calls == 61
        assert payload.max_api_calls == 13
        assert approval.status == 'pending'
        assert json.loads(repair_state.content)['calls'] == 0
