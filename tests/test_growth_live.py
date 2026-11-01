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
    assert 'id="dp-voice-overlay"' in page.text
    assert 'id="dp-voice-hitbox"' in page.text
    assert 'id="dp-voice-status"' in page.text
    assert 'placeholder="Нажмите — текст · удерживайте — голос"' in page.text
    assert 'id="dp-mode-mic"' in page.text
    assert 'Скрыть клавиатуру и перейти к голосовому вводу' in page.text
    assert 'id="dp-press-hint"' not in page.text
    assert 'id="dp-mic"' not in page.text
    assert 'class="command-tools"' in page.text
    assert page.text.count('id="dp-new"') == 1
    assert 'class="command-topicbar"' in page.text
    assert page.text.index('id="dp-feed"') < page.text.index('id="dp-new"')
    assert '/studio/assets/growth-live.css?v=12' in page.text
    assert '/studio/assets/dispatcher.js?v=resource-cards-20261101' in page.text
    assert '/studio/assets/growth-live.js?v=6' in page.text
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


def _v4_campaign_fields(key='EXP-EDIT-001', name='Черновик', budget='100'):
    from app.api.growth_campaigns import FIELDS
    fields = {key: 'Проверка' for key, _ in FIELDS}
    fields.update(experiment_key=key, name=name, budget=budget,
                  observation_window_days='7', time_budget_minutes='45', currency='RUB')
    return fields


def test_v4_campaign_detail_and_draft_editor_preserve_non_form_fields(live):
    from sqlalchemy import select
    from app.models.domain import Experiment, CostRecord, Publication, Task
    client, db, product = live
    base = f'/studio/products/{product.id}/campaigns'
    assert client.post(base+'/new', data=_v4_campaign_fields(),
                       follow_redirects=False).status_code == 303
    record = db.scalar(select(Experiment).where(Experiment.product_id == product.id))
    record.secondary_signals = ['Клиенты']
    db.commit()
    assert f'/campaigns/{record.id}' in client.get(base).text
    detail = client.get(f'{base}/{record.id}?design=v4')
    assert detail.status_code == 200
    assert 'data-growth-design="v4"' in detail.text
    assert 'Редактировать черновик' in detail.text
    edit = client.get(f'{base}/{record.id}/edit?design=v4')
    assert edit.status_code == 200 and 'name="hypothesis"' in edit.text
    edited = client.post(f'{base}/{record.id}/edit',
                         data=_v4_campaign_fields(name='Тест <безопасно>', budget='250'),
                         follow_redirects=False)
    assert edited.status_code == 303
    assert edited.headers['location'].endswith(f'/campaigns/{record.id}')
    db.refresh(record)
    assert record.name == 'Тест <безопасно>'
    assert record.budget == 250
    assert record.secondary_signals == ['Клиенты'] and record.status == 'draft'
    html = client.get(f'{base}/{record.id}?design=v4').text
    assert 'Тест &lt;безопасно&gt;' in html and 'Тест <безопасно>' not in html
    for cls in (Task, CostRecord, Publication):
        assert list(db.scalars(select(cls))) == []


def test_v4_campaign_edit_keeps_record_on_validation_and_key_conflict(live):
    from sqlalchemy import select
    from app.models.domain import Experiment
    client, db, product = live
    base = f'/studio/products/{product.id}/campaigns'
    for key in ('EXP-EDIT-001', 'EXP-EDIT-002'):
        assert client.post(base+'/new', data=_v4_campaign_fields(key),
                           follow_redirects=False).status_code == 303
    records = list(db.scalars(select(Experiment).where(Experiment.product_id == product.id)
                              .order_by(Experiment.experiment_key)))
    first, second = records
    path = f'{base}/{first.id}/edit'
    assert client.post(path, data=_v4_campaign_fields(budget='-3')).status_code == 422
    duplicate = client.post(path, data=_v4_campaign_fields(key=second.experiment_key))
    assert duplicate.status_code == 409
    db.refresh(first)
    assert first.experiment_key == 'EXP-EDIT-001' and first.budget == 100


def test_v4_campaign_scope_and_non_draft_protection(live):
    from sqlalchemy import select
    from app.models.domain import Experiment, Product
    client, db, product = live
    other = Product(name='Чужая компания', slug='other-campaign-v4')
    db.add(other)
    db.commit()
    other_base = f'/studio/products/{other.id}/campaigns'
    assert client.post(other_base+'/new', data=_v4_campaign_fields(key='EXP-OTHER-123'),
                       follow_redirects=False).status_code == 303
    foreign = db.scalar(select(Experiment).where(Experiment.product_id == other.id))
    own_base = f'/studio/products/{product.id}/campaigns/{foreign.id}'
    assert client.get(own_base).status_code == 404
    assert client.get(own_base+'/edit').status_code == 404
    assert client.post(own_base+'/edit', data=_v4_campaign_fields()).status_code == 404
    foreign.status = 'running'
    db.commit()
    assert 'Редактировать черновик' not in client.get(f'{other_base}/{foreign.id}').text
    assert client.get(f'{other_base}/{foreign.id}/edit').status_code == 409
    assert client.post(f'{other_base}/{foreign.id}/edit',
                       data=_v4_campaign_fields(name='Не сохранять')).status_code == 409
    db.refresh(foreign)
    assert foreign.status == 'running' and foreign.name == 'Черновик'



def test_campaign_launch_v4_scoped_once_and_without_external_actions(live, monkeypatch, tmp_path):
    import json
    from sqlalchemy import select
    from app.models.domain import Artifact, CostRecord, Decision, Experiment, Product, Publication, Task
    client, db, product = live
    monkeypatch.setitem(app.dependency_overrides, get_settings,
                        lambda: Settings(app_env='development', products_root=tmp_path, _env_file=None))
    other = Product(name='Competitor', slug='another-company')
    db.add(other)
    db.commit()
    own_base = f'/studio/products/{product.id}/campaigns'
    foreign_base = f'/studio/products/{other.id}/campaigns'
    assert client.post(own_base + '/new', data=_v4_campaign_fields(key='EXP-OWNER-001'),
                       follow_redirects=False).status_code == 303
    assert client.post(foreign_base + '/new', data=_v4_campaign_fields(key='EXP-OTHER-001'),
                       follow_redirects=False).status_code == 303
    own = db.scalar(select(Experiment).where(Experiment.product_id == product.id))
    foreign = db.scalar(select(Experiment).where(Experiment.product_id == other.id))
    action = f'{own_base}/{own.id}/launch'
    detail = client.get(f'{own_base}/{own.id}?design=v4')
    assert detail.status_code == 200
    assert f'action="{action}"' in detail.text
    assert 'Запустить подготовку' in detail.text
    assert client.get(action).status_code == 405
    assert client.post(f'{own_base}/{foreign.id}/launch').status_code == 404
    assert db.get(Experiment, foreign.id).status == 'draft'
    first = client.post(action, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers['location'].endswith(f'/campaigns/{own.id}')
    db.refresh(own)
    assert own.status == 'running'
    assert own.starts_at is not None and own.ends_at is not None
    tasks = list(db.scalars(select(Task)))
    decisions = list(db.scalars(select(Decision)))
    assert len(tasks) == len(decisions) == 1
    assert tasks[0].product_id == product.id
    assert decisions[0].product_id == product.id
    assert tasks[0].title.endswith(': prepare experiment')
    brief = db.scalar(select(Artifact).where(Artifact.task_id == tasks[0].id))
    assert brief.product_id == product.id
    assert 'Sea SPA' not in json.dumps(json.loads(brief.content))
    assert list(db.scalars(select(Publication))) == []
    assert list(db.scalars(select(CostRecord))) == []
    assert client.post(action).status_code == 409
    assert len(list(db.scalars(select(Task)))) == 1
    assert len(list(db.scalars(select(Decision)))) == 1
    assert db.get(Experiment, foreign.id).status == 'draft'
    assert 'Запустить подготовку' not in client.get(f'{own_base}/{own.id}').text


def test_campaign_launch_v4_incomplete_contract_keeps_draft(live, monkeypatch, tmp_path):
    from sqlalchemy import select
    from app.models.domain import Decision, Experiment, Task
    client, db, product = live
    monkeypatch.setitem(app.dependency_overrides, get_settings,
                        lambda: Settings(app_env='development', products_root=tmp_path, _env_file=None))
    row = Experiment(product_id=product.id, experiment_key='EXP-INCOMPLETE-001',
                     name='Unfinished test', hypothesis='Need more details', status='draft')
    db.add(row)
    db.commit()
    response = client.post(f'/studio/products/{product.id}/campaigns/{row.id}/launch')
    assert response.status_code == 409
    assert 'baseline_summary' in response.text
    db.refresh(row)
    assert row.status == 'draft' and row.starts_at is None
    assert list(db.scalars(select(Task))) == []
    assert list(db.scalars(select(Decision))) == []


def test_campaign_launch_v4_requires_company_account_and_csrf(live, monkeypatch, tmp_path):
    from sqlalchemy import select
    from app.core.studio_accounts import create_account
    from app.models.domain import Experiment, Product, Task
    client, db, product = live
    other = Product(name='Foreign', slug='foreign-for-launch')
    db.add(other)
    db.commit()
    settings = Settings(app_env='production', products_root=tmp_path,
                        studio_admin_username='owner', studio_admin_password='fixture-only',
                        studio_public_origin='https://growth.servgo.ru', _env_file=None)
    create_account(settings, username='company-user', password='local-fixture-password',
                   role='company', product_ids=[str(product.id)], created_by='test')
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    client.auth = ('owner', 'fixture-only')
    origin = {'Origin': 'https://growth.servgo.ru'}
    base = f'/studio/products/{product.id}/campaigns'
    assert client.post(base + '/new', data=_v4_campaign_fields(key='EXP-CSRF-001'),
                       headers=origin, follow_redirects=False).status_code == 303
    experiment = db.scalar(select(Experiment).where(Experiment.product_id == product.id))
    action = f'{base}/{experiment.id}/launch'
    assert client.post(action).status_code == 403
    assert client.post(action, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert db.get(Experiment, experiment.id).status == 'draft'
    client.auth = ('company-user', 'local-fixture-password')
    foreign_action = f'/studio/products/{other.id}/campaigns/{experiment.id}/launch'
    assert client.post(foreign_action, headers=origin).status_code == 404
    assert client.post(action, headers=origin, follow_redirects=False).status_code == 303
    assert db.get(Experiment, experiment.id).status == 'running'
    assert len(list(db.scalars(select(Task)))) == 1



def test_v4_campaign_owner_brief_has_clear_goal_action_and_single_next_step(live):
    from sqlalchemy import select
    from app.models.domain import Experiment
    client, db, product = live
    base = f'/studio/products/{product.id}/campaigns'
    values = _v4_campaign_fields(key='EXP-OWNER-BRIEF-001',
                                 name='Вернуть клиентов')
    values.update(target_metric='Повторные записи',
                  action='Подготовить предложение и рассылку',
                  hypothesis='Клиенты снова запишутся',
                  success_condition='20 дополнительных записей',
                  attribution_method='Сравнить записи до и после')
    assert client.post(base+'/new', data=values, follow_redirects=False).status_code == 303
    experiment = db.scalar(select(Experiment).where(
        Experiment.product_id == product.id,
        Experiment.experiment_key == 'EXP-OWNER-BRIEF-001'))
    listing = client.get(base+'?design=v4')
    assert listing.status_code == 200
    for expected in ('Вернуть клиентов', 'Повторные записи',
                     'Подготовить предложение и рассылку',
                     '20 дополнительных записей', 'Проверить и запустить',
                     'От вас:'):
        assert expected in listing.text
    assert '<script>alert' not in listing.text
    detail = client.get(f'{base}/{experiment.id}?design=v4')
    assert detail.status_code == 200
    for expected in ('Что хотим улучшить', 'Что сделаем', 'Что проверим',
                     'Какой результат считаем успешным', 'Как измерим',
                     'Что требуется от вас', 'Сравнить записи до и после',
                     'Запустить подготовку', 'Подробнее о плане и условиях'):
        assert expected in detail.text
    assert f'action="{base}/{experiment.id}/launch"' in detail.text
    assert 'Измерений пока нет — оценить эффективность ещё нельзя.' in detail.text
    assert 'Подключить аккаунт' not in detail.text  # no fabricated integration requirement


def test_v4_campaign_no_false_ready_state_and_company_scoped_metrics(live):
    from datetime import datetime, timezone
    from decimal import Decimal
    from sqlalchemy import select
    from app.models.domain import Experiment, Metric, Product
    client, db, product = live
    base = f'/studio/products/{product.id}/campaigns'
    incomplete = Experiment(product_id=product.id, experiment_key='EXP-OWNER-INCOMPLETE-001',
                            name='Требует уточнения', hypothesis='Проверить', status='draft')
    db.add(incomplete)
    db.flush()
    other = Product(name='Другая организация', slug='owner-brief-foreign')
    db.add(other)
    db.flush()
    valid = Experiment(product_id=product.id, experiment_key='EXP-OWNER-METRIC-001',
                       name='Реальные показатели', hypothesis='Проверить', status='running',
                       action='Подготовить рассылку', target_metric='Повторные записи',
                       success_condition='10 дополнительных записей',
                       attribution_method='По CRM')
    db.add(valid)
    db.flush()
    ts = datetime(2026, 11, 1, tzinfo=timezone.utc)
    db.add_all([
        Metric(product_id=product.id, experiment_id=valid.id,
               name='Повторные записи', value=Decimal('4'), unit='шт.',
               source='CRM бизнеса', observed_at=ts),
        Metric(product_id=other.id, experiment_id=valid.id,
               name='ЧУЖОЙ ПОКАЗАТЕЛЬ', value=Decimal('999'), unit='шт.',
               source='ЧУЖОЙ ИСТОЧНИК', observed_at=ts),
    ])
    db.commit()
    index = client.get(base)
    assert 'Дополнить план' in index.text
    assert 'ЧУЖОЙ ПОКАЗАТЕЛЬ' not in index.text
    draft = client.get(f'{base}/{incomplete.id}')
    assert 'Дополнить план' in draft.text
    assert f'action="{base}/{incomplete.id}/launch"' not in draft.text
    running = client.get(f'{base}/{valid.id}?design=v4')
    assert running.status_code == 200
    assert 'Подготовка запущена' in running.text
    assert 'Повторные записи:' in running.text
    assert 'CRM бизнеса' in running.text
    assert 'ЧУЖОЙ ПОКАЗАТЕЛЬ' not in running.text
    assert 'ЧУЖОЙ ИСТОЧНИК' not in running.text
    assert f'action="{base}/{valid.id}/launch"' not in running.text
    assert f'/studio/products/{product.id}/channels' in running.text
    assert f'/studio/products/{product.id}/review' in running.text


def test_v4_campaign_description_escapes_persisted_html(live):
    from app.models.domain import Experiment
    client, db, product = live
    row = Experiment(product_id=product.id, experiment_key='EXP-ESCAPE-OWNER-001',
                     name='<img src=x onerror=alert(1)>',
                     action='<script>alert(1)</script>',
                     hypothesis='Демонстрационный тест',
                     target_metric='<b>Цель</b>', status='draft')
    db.add(row)
    db.commit()
    base = f'/studio/products/{product.id}/campaigns'
    for path in (base, f'{base}/{row.id}'):
        text = client.get(path).text
        assert '&lt;img src=x onerror=alert(1)&gt;' in text
        assert '<img src=x onerror=alert(1)>' not in text
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in text
        assert '<script>alert(1)</script>' not in text
