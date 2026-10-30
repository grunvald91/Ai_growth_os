from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Artifact, Product


@pytest.fixture
def live(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name='Live company', slug='servgo')
        db.add(product)
        db.commit()
        def session():
            yield db
        monkeypatch.setitem(app.dependency_overrides, get_db, session)
        monkeypatch.setitem(app.dependency_overrides, get_settings,
                            lambda: Settings(app_env='development', _env_file=None))
        with TestClient(app) as client:
            yield client, db, product
    engine.dispose()


def test_switch_both_ways_preserves_query_and_real_forms(live):
    client, db, product = live
    base = f'/studio/products/{product.id}'
    classic = client.get(base+'/create?q=hello')
    assert 'Новый дизайн' in classic.text
    assert 'q=hello&amp;design=v4' in classic.text
    new = client.get(base+'/create?design=v4')
    assert new.status_code == 200
    assert 'data-growth-design="v4"' in new.text
    assert 'Старый дизайн' in new.text
    assert client.cookies['growth_design'] == 'v4'
    assert 'name="topic"' in new.text
    assert f'action="{base}/create"' in new.text
    assert 'private, no-store' == new.headers['cache-control']
    assert 'Cookie' in new.headers['vary']
    assert 'data-growth-design="v4"' in client.get(base+'/work').text
    back = client.get(base+'/work?design=classic')
    assert 'data-growth-design="v4"' not in back.text
    assert 'Новый дизайн' in back.text


def test_home_keeps_real_dispatcher_and_voice(live):
    client, db, product = live
    page = client.get(f'/studio/products/{product.id}?design=v4')
    assert page.status_code == 200
    assert f'data-api="/studio/products/{product.id}/dispatch"' in page.text
    assert 'data-voice-url="/studio/voice/dictate"' in page.text
    assert 'id="dp-state"' in page.text
    assert '/studio/assets/dispatcher.js' in page.text
    assert 'Простое видео' in page.text
    assert 'https://servgo.ru/mail' in page.text


def test_live_screens_read_company_records_only(live):
    client, db, product = live
    other = Product(name='Other', slug='other')
    db.add(other)
    db.flush()
    for pid, title in [(product.id, 'Наш SEO-отчёт'), (other.id, 'Чужой секретный отчёт')]:
        db.add(Artifact(product_id=pid, kind='seo_factory_report', title=title, content='{}'))
    db.commit()
    page = client.get(f'/studio/products/{product.id}/seo?design=v4')
    assert 'Наш SEO-отчёт' in page.text
    assert 'Чужой секретный отчёт' not in page.text
    assert 'command=seo_factory' in page.text
    assert client.get(f'/studio/products/{uuid4()}/seo').status_code == 404
    assert client.get(f'/studio/products/{product.id}/campaigns').status_code == 200


def test_switch_never_bypasses_auth(live, monkeypatch):
    client, db, product = live
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: Settings(
        app_env='production', studio_admin_username='owner', studio_admin_password='fixture-only', _env_file=None))
    for path in [f'/studio/products/{product.id}/seo', f'/studio/products/{product.id}', '/studio/assets/growth-live.js']:
        assert client.get(path+'?design=v4').status_code == 401
    page = client.get(f'/studio/products/{product.id}/seo?design=v4', auth=('owner','fixture-only'))
    assert page.status_code == 200


def test_writes_keep_csrf_and_real_queue_and_validation(live, monkeypatch):
    from app.models.domain import Task
    from sqlalchemy import select
    client, db, product = live
    origin = 'https://growth.servgo.ru'
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: Settings(
        app_env='production', studio_admin_username='owner', studio_admin_password='fixture-only',
        studio_public_origin=origin, _env_file=None))
    client.auth = ('owner', 'fixture-only')
    url = f'/studio/products/{product.id}/create'
    client.get(url+'?design=v4')
    fields = {'format':'post', 'channel':'telegram', 'topic':'Рабочая тема для поста'}
    rejected = client.post(url, data=fields, headers={'Origin':'https://attacker.invalid'})
    assert rejected.status_code == 403
    assert not list(db.scalars(select(Task)))
    invalid = client.post(url, data={**fields,'topic':'x'}, headers={'Origin':origin})
    assert invalid.status_code == 422
    assert 'data-growth-design="v4"' in invalid.text
    accepted = client.post(url, data=fields, headers={'Origin':origin}, follow_redirects=False)
    assert accepted.status_code == 303
    assert '/tasks/' in accepted.headers['location']
    assert len(list(db.scalars(select(Task)))) == 1


def test_company_scope_is_enforced_for_new_screens(live, monkeypatch, tmp_path):
    from app.core.studio_accounts import create_account
    client, db, product = live
    other = Product(name='Other company', slug='other-scope')
    db.add(other)
    db.commit()
    settings = Settings(app_env='production', products_root=tmp_path, _env_file=None)
    create_account(settings, username='company-user', password='local-fixture-password', role='company',
                   product_ids=[str(product.id)], created_by='test')
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    for suffix in ('seo','campaigns'):
        assert client.get(f'/studio/products/{product.id}/{suffix}?design=v4',
                          auth=('company-user','local-fixture-password')).status_code == 200
        assert client.get(f'/studio/products/{other.id}/{suffix}?design=v4',
                          auth=('company-user','local-fixture-password')).status_code == 404
