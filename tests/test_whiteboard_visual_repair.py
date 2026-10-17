"""Visual-only repair retains every original MP3 and refuses new speech."""
import hashlib
import json

import pytest
from sqlalchemy import select

from app.content.whiteboard.jobs import records, owner_task, job_directory
from app.content.whiteboard.repair import queue_visual_repair
from app.content.whiteboard.runtime import ReadinessError
from app.content.whiteboard.worker import run_whiteboard_once
from app.models.domain import Task,TaskStatus
from test_whiteboard_jobs import environment,new_job,approve,ready,fake_factory,client
from test_whiteboard_visual_review import issue,review,decision


@pytest.mark.asyncio
async def test_visual_only_repair_never_calls_story_image_generation_or_tts_and_preserves_audio(environment):
    factory,settings,product,_=environment
    source_id=new_job(environment,maximum=25);approve(environment,source_id)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    original=job_directory(settings.products_root,product,source_id)/'stages'
    original_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in original.glob('*speech.data')}
    with factory() as db:
        repair_id=queue_visual_repair(db,settings,product,source_id).id
        _,state,approval,payload=records(db,owner_task(db,product,repair_id))
        assert payload.visual_repair_from==source_id and payload.inherited_calls==13
        assert json.loads(state.content)['calls']==13 and approval.status=='pending'
        assert queue_visual_repair(db,settings,product,source_id).id==repair_id
    with client() as browser:
        page=browser.get(f'/studio/products/{product}/whiteboard/{repair_id}')
        assert 'Новая озвучка запрещена' in page.text and 'Из исходного задания перенесено вызовов: 13' in page.text
    approve(environment,repair_id)
    seen=[]
    report=review(*(issue(i) for i in range(3)))
    def build(settings,payload,store):
        p=fake_factory(settings,payload,store);p.visual_review=True;p.max_api_calls=payload.max_api_calls
        initial=p.images
        class Images:
            async def generate(self,*args):pytest.fail('Original images must be reused before the critic')
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
    assert seen==['critic','author','redraw','redraw','redraw']
    new=job_directory(settings.products_root,product,repair_id)/'stages'
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in new.glob('*speech.data')}==original_hashes
    assert (new/'storyboard.data').read_bytes()==(original/'storyboard.data').read_bytes()
    with factory() as db:
        t=owner_task(db,product,repair_id);assert t.status==TaskStatus.COMPLETED
        assert json.loads(records(db,t)[1].content)['calls']==21  # inherited 13 + at most 8 visual calls
        assert queue_visual_repair(db,settings,product,source_id).id==repair_id
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
        assert json.loads(records(db,t)[1].content)['calls']==13


@pytest.mark.asyncio
async def test_repair_endpoint_is_owner_scoped_and_creates_only_one_pending_approval(environment):
    factory,settings,product,other=environment;source=new_job(environment,maximum=25);approve(environment,source)
    await run_whiteboard_once(factory,settings,factory=fake_factory,readiness=ready)
    with client() as browser:
        assert browser.post(f'/studio/products/{other}/whiteboard/{source}/repair').status_code==404
        path=f'/studio/products/{product}/whiteboard/{source}/repair'
        a=browser.post(path,follow_redirects=False);b=browser.post(path,follow_redirects=False)
        assert a.status_code==b.status_code==303 and a.headers['location']==b.headers['location']
    with factory() as db:
        tasks=list(db.scalars(select(Task)));assert len(tasks)==2
        pending=next(t for t in tasks if t.id!=source)
        assert pending.status==TaskStatus.WAITING_APPROVAL
        assert records(db,pending)[2].status=='pending'
