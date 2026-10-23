"""Gemini image transport is pinned to ClaudeHub; no silent model/provider fallback."""
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.api import whiteboard as api
from app.content.whiteboard.adapters import ClaudeHubGeminiImageProvider
from app.content.whiteboard.jobs import owner_task,records
from app.content.whiteboard.runtime import generation_profile,scoped_settings,preflight,ReadinessError
from app.content.whiteboard.worker import build_pipeline
from app.content.whiteboard.checkpoints import CheckpointStore
from app.models.domain import Task
from test_whiteboard import scene,request
from test_whiteboard_jobs import environment,client


def png():
    image=Image.new('RGB',(1024,1792),'#123456');buf=io.BytesIO();image.save(buf,format='PNG');return buf.getvalue()


@pytest.mark.asyncio
async def test_one_documented_claudehub_image_request_and_padding_preserve_every_pixel():
    seen=[]
    def handler(req):
        seen.append(req)
        assert str(req.url)=='https://api.claudehub.fun/v1/images/generations'
        body=json.loads(req.content)
        assert set(body)=={'model','prompt','size'}
        assert body['model']=='gemini-3.1-flash-image' and body['size']=='1024x1792'
        assert req.headers['authorization']=='Bearer test-hub-key'
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png()).decode()}]})
    async with httpx.AsyncClient(base_url='https://api.claudehub.fun/v1/',transport=httpx.MockTransport(handler)) as c:
        output=await ClaudeHubGeminiImageProvider(api_key='test-hub-key',base_url='https://api.claudehub.fun/v1',client=c).generate(scene(),request())
    assert len(seen)==1
    with Image.open(io.BytesIO(output)) as image:
        assert image.size==(1026,1824) and image.width*16==image.height*9
        assert image.getpixel((1,16))==(18,52,86) and image.getpixel((1024,1807))==(18,52,86)
        assert image.getpixel((0,0))==(255,255,255)


@pytest.mark.asyncio
async def test_failed_claudehub_generation_is_not_retried_or_sent_to_openrouter():
    seen=[]
    def handler(req):seen.append(str(req.url));return httpx.Response(502,json={'error':'upstream'})
    async with httpx.AsyncClient(base_url='https://api.claudehub.fun/v1/',transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await ClaudeHubGeminiImageProvider(api_key='test',base_url='https://api.claudehub.fun/v1',client=c).generate(scene(),request())
    assert seen==['https://api.claudehub.fun/v1/images/generations']


def test_image_adapter_refuses_other_models_and_hosts():
    for options in [dict(model='black-forest-labs/flux.2-pro',base_url='https://api.claudehub.fun/v1'),
                    dict(model='gemini-3.1-flash-image',base_url='https://openrouter.ai/api/v1')]:
        with pytest.raises(ValueError):ClaudeHubGeminiImageProvider(api_key='test',**options)


def test_both_new_profiles_pin_image_model_and_provider_in_approval(environment):
    factory,settings,product,_=environment
    with client() as browser:
        for profile in ['configured','gemini_ru']:
            state=browser.post(f'/products/{product}/whiteboard/jobs',json={'topic':'Почему самолёт садится на авианосец '+profile,'profile':profile}).json()
            assert state['models']['image']=='gemini-3.1-flash-image' and state['image_provider']=='claudehub'
            from uuid import UUID
            with factory() as db:
                _,_,_,payload=records(db,owner_task(db,product,UUID(state['task_id'])))
                store=CheckpointStore(settings.products_root/'test-build'/state['task_id'],payload.request,
                    models=payload.models.model_dump(),image_provider='claudehub')
                p=build_pipeline(scoped_settings(settings,payload.models,payload.request.voice),payload,store)
                assert isinstance(p.images,ClaudeHubGeminiImageProvider)
        assert browser.post(f'/products/{product}/whiteboard/jobs',json={'topic':'Тест запрета прежнего профиля','profile':'flux_ru'}).status_code==422


@pytest.mark.asyncio
async def test_readiness_uses_claudehub_for_image_catalogue_and_openrouter_only_for_voice_and_vision(environment):
    _,settings,_,_=environment
    models,voice=generation_profile(settings,'gemini_ru');scoped=scoped_settings(settings,models,voice)
    seen=[]
    def hub(req):
        seen.append(('hub',req.method,str(req.url)))
        return httpx.Response(200,json={'data':[{'id':'gemini-3.1-flash-image'},{'id':models.regions}]})
    def router(req):
        seen.append(('router',req.method,str(req.url)))
        if req.url.path.endswith('/key'):return httpx.Response(200,json={'data':{}})
        return httpx.Response(200,json={'data':[{'id':models.speech,'supported_voices':['eve']}]})
    async with (httpx.AsyncClient(base_url='https://api.claudehub.fun/v1/',transport=httpx.MockTransport(hub)) as h,
        httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/',transport=httpx.MockTransport(router)) as r):
        assert (await preflight(scoped,models,voice,client=r,claudehub_client=h))['ready']
    assert all(method=='GET' for _,method,_ in seen) and len(seen)==3
    assert [who for who,_,url in seen if 'models' in url and who=='router']==['router'] and not any('vision' in url for _,_,url in seen)
    assert not any('/images' in url for _,_,url in seen)
