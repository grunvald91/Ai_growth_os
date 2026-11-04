"""End-to-end API checks for free immutable Story revisions and paid consent."""
from __future__ import annotations

import io
import json
import zipfile
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import require_studio_auth
from app.brand_kit.contracts import BrandStyle
from app.brand_kit.service import store_asset, save_draft, approve_version
from app.content.creative import CreativeRequest, queue_creative, CreativeDraft
from app.content.brand_story_pipeline import (
    compose_saved_stories, visible_images, verified_gallery_image,
)
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product


def logo():
    stream = io.BytesIO()
    Image.new("RGB", (420, 130), (180, 70, 190)).save(stream, "PNG")
    return stream.getvalue()


def draft():
    return CreativeDraft.model_validate({
        "format":"story", "platform":"telegram",
        "title":"Советы о сервисе", "hook":"Как записаться",
        "cta":"Запишитесь сегодня", "cover_text":"Запись онлайн",
        "visual_style":"Профессиональный фирменный контент с мягким освещением",
        "caption":"Советы, которые помогают клиентам пользоваться онлайн записью.",
        "frames":[{
            "number":n, "start_second":(n-1)*5, "end_second":n*5,
            "purpose":"Клиентская запись без звонка",
            "visual_prompt":"Красивое рабочее место в аккуратном современном салоне",
            "on_screen_text":"Записаться онлайн можно быстро",
            "action_or_animation":"Камера плавно приближается к рабочему месту",
        } for n in (1,2,3)],
    })


@pytest.fixture
def setup(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread":False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        a,b = Product(name="Студия",slug="edit-studio-a"), Product(name="Другая",slug="edit-studio-b")
        db.add_all([a,b]);db.commit()
        asset=store_asset(db,tmp_path,a.id,"original",logo())
        style=BrandStyle.model_validate({"logos":{"original":str(asset.id)}})
        version=save_draft(db,tmp_path,a.id,style.model_dump(mode="json"))
        approve_version(db,tmp_path,a.id,version.id)
        task=queue_creative(db,CreativeRequest(
            mode="creative",product_id=a.id,topic="Как записаться онлайн",
            platform="telegram",format="story",brand_mode="brand",image_model="none"),
            settings=Settings(_env_file=None, brand_stories_enabled=True,
                              brand_story_canary_product_ids=str(a.id)))
        saved=db.scalar(select(Artifact).where(
            Artifact.task_id==task.id,Artifact.kind=="creative_request"))
        command=CreativeRequest.model_validate_json(saved.content)
        assert compose_saved_stories(db,tmp_path,command,draft(),task.id)
        ArtifactService = __import__("app.services.artifacts",fromlist=["ArtifactService"]).ArtifactService
        ArtifactService(db).create(
            product_id=a.id,task_id=task.id,kind="creative_brief",title="Brief",
            content=json.dumps(draft().model_dump(mode="json"),ensure_ascii=False))
        db.commit()
        def operator(request:Request):
            request.state.studio_product_ids=None
            return "editor-test"
        def session():yield db
        app.dependency_overrides[require_studio_auth]=operator
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[get_settings]=lambda:Settings(
            _env_file=None,products_root=tmp_path,xai_api_key="test-key",
            studio_public_origin="https://growth.servgo.ru",
            brand_stories_enabled=True, brand_story_canary_product_ids=str(a.id))
        with TestClient(app,headers={"Origin":"https://growth.servgo.ru"}) as client:
            yield db,client,a,b,task,tmp_path,style
        app.dependency_overrides.clear()
    engine.dispose()


def edit_payload(style, *, token=None):
    return {
        "token":str(token or uuid4()),
        "heading":"Другой заголовок без дополнительного платного запроса",
        "body":"Текст обновлён бесплатно",
        "cta":"",
        "heading_font":"Manrope", "body_font":"Manrope",
        "logo_position":"top-right", "logo_variant":"original",
        "image_position":"full", "text_align":"center",
        "crop_x":"80", "crop_y":"10",
        **{key:getattr(style.palette,key) for key in (
            "primary","secondary","accent","background","text")},
    }


def test_free_edits_are_new_versions_and_idempotent(setup):
    db,c,a,b,task,root,style=setup
    originals=visible_images(db,a.id,task.id)
    assert len(originals)==3
    first=originals[0]
    url=f"/studio/products/{a.id}/tasks/{task.id}/stories/{first.id}/edit"
    first_sha=json.loads(first.content)["sha256"]
    page=c.get(url)
    assert page.status_code==200,page.text
    assert "Сохранить изменения · бесплатно" in page.text
    data=edit_payload(style)
    saved=c.post(url,data=data,follow_redirects=False)
    assert saved.status_code==303,saved.text
    originals_after=visible_images(db,a.id,task.id)
    assert len(originals_after)==3
    result=originals_after[0]
    assert result.id!=first.id
    assert json.loads(result.content)["revision"]==1
    assert json.loads(result.content)["sha256"]!=first_sha
    verified_gallery_image(db,root,a.id,task.id,result)
    # The exact same confirmation token must not add or bill another edit.
    repeat=c.post(url,data=data,follow_redirects=False)
    assert repeat.status_code==303
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.kind=="creative_story_render",Artifact.task_id==task.id))))==4
    # Cross-company IDs must not return the original or revised material.
    cross=c.get(f"/studio/products/{b.id}/tasks/{task.id}/stories/{result.id}/edit")
    assert cross.status_code==404


def test_zip_contains_latest_verified_final_frames_not_paid_backgrounds(setup):
    db,c,a,b,task,root,style=setup
    first=visible_images(db,a.id,task.id)[0]
    base=f"/studio/products/{a.id}/tasks/{task.id}"
    assert c.post(f"{base}/stories/{first.id}/edit",
                  data=edit_payload(style),follow_redirects=False).status_code==303
    r=c.get(base+"/images.zip")
    assert r.status_code==200,r.text[:200] if r.status_code!=200 else ""
    with zipfile.ZipFile(io.BytesIO(r.content)) as zipped:
        assert set(zipped.namelist())=={
            "frame-01.png","frame-02.png","frame-03.png","metadata.json"}
        with Image.open(io.BytesIO(zipped.read("frame-01.png"))) as image:
            assert image.size==(900,1600)
    assert c.get(f"/studio/products/{b.id}/tasks/{task.id}/images.zip").status_code==404


@pytest.mark.asyncio
async def test_paid_regeneration_requires_explicit_consent_and_deduplicates_token(setup,monkeypatch):
    db,c,a,b,task,root,style=setup
    first=visible_images(db,a.id,task.id)[0]
    url=f"/studio/products/{a.id}/tasks/{task.id}/stories/{first.id}/regenerate-background"
    assert "Подтвердить платный запрос" in c.get(url).text
    no=c.post(url,data={"token":str(uuid4()),"model":"gemini"})
    assert no.status_code==422
    calls=[]
    async def fake_request(settings,model,prompt,ratio):
        calls.append((model,prompt,ratio))
        stream=io.BytesIO()
        Image.new("RGB",(600,1000),(17,111,211)).save(stream,"PNG")
        return stream.getvalue()
    monkeypatch.setattr("app.content.creative_images.request_image",fake_request)
    token=str(uuid4())
    allowed=c.post(url,data={"token":token,"model":"gemini","confirm":"yes"},
                   follow_redirects=False)
    assert allowed.status_code==303,allowed.text
    assert len(calls)==1
    assert len(visible_images(db,a.id,task.id))==3
    again=c.post(url,data={"token":token,"model":"gemini","confirm":"yes"},
                 follow_redirects=False)
    assert again.status_code==409
    assert len(calls)==1
    assert c.post(f"/studio/products/{b.id}/tasks/{task.id}/stories/{first.id}/regenerate-background",
                  data={"token":str(uuid4()),"model":"gemini","confirm":"yes"}).status_code==404


def test_invalid_color_does_not_mutate_existing_image(setup):
    db,c,a,b,task,root,style=setup
    first=visible_images(db,a.id,task.id)[0]
    url=f"/studio/products/{a.id}/tasks/{task.id}/stories/{first.id}/edit"
    payload=edit_payload(style)
    payload["text"]="#000000"  # Same color as white? background is white default; this is valid
    payload["background"]="#000000"  # Now identical text & background; contrast violation
    response=c.post(url,data=payload)
    assert response.status_code in {200,422}
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.kind=="creative_story_render",Artifact.task_id==task.id))))==3



def test_history_lists_previous_verified_versions(setup):
    db, client, company, _, task, root, style = setup
    initial = visible_images(db, company.id, task.id)[0]
    route = f"/studio/products/{company.id}/tasks/{task.id}/stories/{initial.id}/edit"
    assert client.post(route, data=edit_payload(style),
                       follow_redirects=False).status_code == 303
    current = visible_images(db, company.id, task.id)[0]
    page = client.get(
        f"/studio/products/{company.id}/tasks/{task.id}/stories/{current.id}/edit")
    assert page.status_code == 200
    assert "История версий" in page.text and "Версия 1" in page.text
    assert f"/stories/{initial.id}/edit" in page.text
    assert f"/images/{initial.id}?download=true" in page.text


def test_zip_rejects_incomplete_or_corrupted_final_frame(setup):
    db, client, company, _, task, root, _ = setup
    record = visible_images(db, company.id, task.id)[0]
    path = verified_gallery_image(db, root, company.id, task.id, record)
    path.unlink()
    res = client.get(f"/studio/products/{company.id}/tasks/{task.id}/images.zip")
    assert res.status_code == 409
    assert "полный ZIP" in res.text


def test_form_requires_consent_for_paid_backgrounds(setup):
    db, client, company, _, task, root, style = setup
    uri = f"/studio/products/{company.id}/create"
    html = client.get(uri)
    assert html.status_code == 200
    assert 'name="paid_confirm"' in html.text
    assert '<option value="auto" selected>' in html.text
    fields = {
        "format": "story", "channel": "telegram",
        "topic": "Создай три новых Stories", "brand_mode": "auto",
        "image_model": "auto",
    }
    no = client.post(uri, data=fields, follow_redirects=False)
    assert no.status_code == 422
    assert "Подтвердите платную генерацию" in no.text
    fields["paid_confirm"] = "yes"
    yes = client.post(uri, data=fields, follow_redirects=False)
    assert yes.status_code == 303



def test_noncanary_creation_omits_hidden_brand_controls():
    from app.api.content_studio_ui import create_content_html
    from uuid import uuid4
    no = create_content_html(uuid4(), brand_canary=False)
    yes = create_content_html(uuid4(), brand_canary=True)
    assert 'name="brand_mode"' not in no
    assert 'name="image_model"' not in no
    assert 'name="paid_confirm"' not in no
    assert 'name="brand_mode"' in yes
    assert 'name="paid_confirm"' in yes


def test_paid_nonce_checked_after_more_than_200_prior_confirmations(setup, monkeypatch):
    from app.services.artifacts import ArtifactService
    db, client, company, _, task, _, _ = setup
    for index in range(220):
        ArtifactService(db).create(
            product_id=company.id, task_id=task.id,
            kind="creative_background_regen", title=f"Older paid event {index}",
            content=json.dumps({"token": str(uuid4()), "state": "completed"}))
    db.commit()
    from app.content import creative_images
    calls = []
    async def fake_image(settings, model, prompt, ratio):
        calls.append(prompt)
        stream = io.BytesIO()
        Image.new("RGB", (600, 1000), (40, 80, 120)).save(stream, "PNG")
        return stream.getvalue()
    monkeypatch.setattr(creative_images, "request_image", fake_image)
    source = visible_images(db, company.id, task.id)[0]
    url = (f"/studio/products/{company.id}/tasks/{task.id}"
           f"/stories/{source.id}/regenerate-background")
    confirmation = {"token": str(uuid4()), "model": "gemini", "confirm": "yes"}
    assert client.post(url, data=confirmation, follow_redirects=False).status_code == 303
    assert client.post(url, data=confirmation, follow_redirects=False).status_code == 409
    assert len(calls) == 1
