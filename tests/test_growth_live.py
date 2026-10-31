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
from app.seo.activation import set_enabled


@pytest.fixture
def live(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name='Live company', slug='servgo')
        db.add(product)
        db.commit()
        set_enabled(db, product.id, enabled=True, actor='fixture-owner')
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
    assert '/studio/assets/dispatcher.js?v=hold-20261031' in page.text
    assert 'id="dp-press-hint"' in page.text
    assert 'Удерживайте строку, чтобы говорить' in page.text
    assert 'id="dp-mic"' not in page.text
    assert 'Простое видео' in page.text
    assert 'hero-intro' in page.text
    assert 'Добрый день, Марина' not in page.text
    assert 'initialTasks' not in page.text
    assert 'function homeV4' not in page.text
    assert 'Демо · API не подключены' not in page.text
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
    for suffix in ('seo','campaigns','v4/data'):
        assert client.get(f'/studio/products/{product.id}/{suffix}?design=v4',
                          auth=('company-user','local-fixture-password')).status_code == 200
        assert client.get(f'/studio/products/{other.id}/{suffix}?design=v4',
                          auth=('company-user','local-fixture-password')).status_code == 404


def test_v4_data_has_only_company_records_and_no_mock_data(live):
    from app.models.domain import Task
    client, db, product = live
    other = Product(name='Other data company',slug='data-other')
    db.add(other)
    db.flush()
    db.add_all([Task(product_id=product.id,title='Настоящее задание'),
                Task(product_id=other.id,title='Чужое задание')])
    db.commit()
    result = client.get(f'/studio/products/{product.id}/v4/data')
    assert result.status_code == 200
    assert [t['title'] for t in result.json()['tasks']] == ['Настоящее задание']
    assert result.headers['cache-control'] == 'private, no-store'


def test_v4_home_is_available_for_other_company_and_hides_technical_artifacts(live):
    client, db, product = live
    product.slug='company-without-dispatcher-flag'
    db.add_all([Artifact(product_id=product.id,kind='internal_checkpoint',title='Internal',content='{}'),
                Artifact(product_id=product.id,kind='article',title='Настоящая статья',content='Текст')])
    db.commit()
    page = client.get(f'/studio/products/{product.id}?design=v4')
    assert 'hero-intro' in page.text and 'data-api=' in page.text
    data=client.get(f'/studio/products/{product.id}/v4/data').json()
    assert [a['title'] for a in data['materials']]==['Настоящая статья']


def test_v4_reuses_reviewed_css_and_keeps_team_access_overlay(live):
    from pathlib import Path
    client, db, product = live
    page=client.get(f'/studio/products/{product.id}?design=v4')
    prototype=Path('app/api/assets/growth_os_v4_preview.html').read_text()
    reviewed_css=prototype.split('<style>',1)[1].split('</style>',1)[0]
    assert reviewed_css in page.text
    assert 'gv-layout' not in page.text
    team=client.get('/studio/team')
    assert team.text.count('id="authForm"')==1
    assert team.text.count('class="topbar"')==1


def test_library_preserves_server_search_filter_and_pagination(live):
    from app.api.growth_v4 import Document
    client, db, product = live
    for i in range(25):
        db.add(Artifact(product_id=product.id,kind='article',title=f'ARTICLE_{i}',content='unique-search'))
    db.add(Artifact(product_id=product.id,kind='channel_text',title='POST_ONLY',content='post'))
    db.commit()
    url=f'/studio/products/{product.id}/materials'
    page=client.get(url+'?design=v4&kind=article&q=unique-search')
    assert 'POST_ONLY' not in page.text
    assert 'Следующая' in page.text
    doc=Document(page.text)
    assert sum('data-material' in e[1] for e in doc.elements)==24
    second=Document(client.get(url+'?kind=article&q=unique-search&page=2').text)
    assert sum('data-material' in e[1] for e in second.elements)==1


def test_notifications_and_nested_tune_navigation_are_preserved(live):
    client, db, product = live
    for suffix in ('','/work','/create','/materials'):
        page=client.get(f'/studio/products/{product.id}{suffix}?design=v4')
        assert page.text.count('data-sg-notifications\n') + page.text.count('data-sg-notifications\r') <= 1
        assert page.text.count('class="sg-notifications"')==1
        assert 'data-sg-read-all' in page.text
    page=client.get('/studio/tune/work?design=v4')
    assert 'Промпты агентов' in page.text
    assert 'Модели и критик' in page.text


def test_campaign_form_creates_real_draft_and_validates(live):
    from sqlalchemy import select
    from app.models.domain import Experiment
    from app.api.growth_campaigns import FIELDS
    client, db, product=live
    url=f'/studio/products/{product.id}/campaigns/new'
    page=client.get(url+'?design=v4')
    assert page.status_code==200
    fields={key:'Описание' for key,_ in FIELDS}
    fields.update(experiment_key='EXP-UI-TEST',observation_window_days='7',time_budget_minutes='60',budget='100',currency='RUB')
    assert client.post(url,data={**fields,'budget':'-1'}).status_code==422
    assert not list(db.scalars(select(Experiment)))
    result=client.post(url,data=fields,follow_redirects=False)
    assert result.status_code==303
    records=list(db.scalars(select(Experiment)))
    assert len(records)==1 and records[0].status=='draft'
    duplicate=client.post(url,data=fields)
    assert duplicate.status_code==409
    assert 'data-growth-design="v4"' in duplicate.text
