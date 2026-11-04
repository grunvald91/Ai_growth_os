from uuid import uuid4
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Artifact, Product, Task, TaskStatus


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
    assert 'id="sg-create-settings"' in new.text
    assert 'id="sg-create-audience"' in new.text
    assert 'id="sg-create-brief"' in new.text
    assert 'id="sg-create-platform"' in new.text
    assert 'id="sg-create-count"' in new.text
    assert f'action="{base}/create"' not in new.text
    assert 'id="sg-create-form"' not in new.text
    assert f'action="{base}/create"' in classic.text
    assert 'private, no-store' == new.headers['cache-control']
    assert 'Cookie' in new.headers['vary']
    assert 'data-growth-design="v4"' in client.get(base+'/work').text
    back = client.get(base+'/work?design=classic')
    assert 'data-growth-design="v4"' not in back.text
    assert 'Новый дизайн' in back.text




def test_daily_drawer_and_compact_navigation_use_v4_real_routes(live):
    client, _, product = live
    base = f"/studio/products/{product.id}"
    page = client.get(base + "?design=v4")
    assert page.status_code == 200
    assert 'id="sgDailyDrawer"' in page.text
    assert 'id="sgDailyHandle"' in page.text
    assert 'id="sgDailyBackdrop"' in page.text
    assert '/studio/assets/growth-daily.js?v=1' in page.text
    assert '/studio/assets/growth-daily.css?v=2' in page.text
    assert 'id="sgOrgMenu" hidden' in page.text
    nav = page.text.split('<nav class="mobile-nav"', 1)[1].split('</nav>', 1)[0]
    assert nav.index(">Календарь</span>") < nav.index(">Ещё</span>")
    assert nav.index("SEO-завод") < nav.index("Контент-план")
    assert nav.index("Контент-план") < nav.index("Библиотека")
    assert "Простое видео" not in nav
    assert "Другая компания" not in nav
    assert client.get("/studio/assets/growth-daily.js").status_code == 200
    assert client.get("/studio/assets/growth-daily.css").status_code == 200


def test_create_has_bottom_dispatcher_and_real_voice_controls(live):
    client, _, product = live
    page = client.get(f'/studio/products/{product.id}/create?design=v4')
    assert page.status_code == 200
    assert 'class="sg-create-screen"' in page.text
    assert 'id="dp-form"' in page.text
    assert 'class="dp-form command-bar"' in page.text
    assert 'id="dp-voice-hitbox"' in page.text
    assert 'id="dp-voice-overlay"' in page.text
    assert 'class="voice-hold-overlay vg"' in page.text
    assert 'id="vgBars"' in page.text
    assert 'id="vgKnob"' in page.text
    assert '/studio/assets/voice-glow.js?v=2' in page.text
    assert 'id="dp-voice-status"' in page.text
    assert 'id="sg-create-suggestions"' in page.text
    assert 'id="sg-create-count"' in page.text
    assert 'id="sg-create-platform"' in page.text
    assert 'id="sg-create-audience"' in page.text
    assert 'id="sg-create-brief"' in page.text
    assert 'id="sg-create-form"' not in page.text
    assert '/studio/assets/create-studio.js?v=history-20261104' in page.text
    assert '/studio/assets/dispatcher.js?v=voice-glow-audio-20261102' in page.text
    assert page.text.count('id="dp-input"') == 1
    assert page.text.index('Что создаём?') < page.text.index('id="dp-input"')
    js = client.get('/studio/assets/dispatcher.js')
    assert js.status_code == 200
    assert "root.classList.contains('sg-create-dispatch')" in js.text
    assert "pending[pending.length - 1]" in js.text



def test_create_has_only_dispatcher_submit_and_reusable_details(live):
    client, _, product = live
    base = f'/studio/products/{product.id}'
    page = client.get(base + '/create?design=v4').text
    # No hidden legacy form may bypass dispatcher confirmation.
    assert 'id="dp-form"' in page and 'id="dp-send"' in page
    assert 'id="sg-create-settings"' in page
    assert 'sg-primary-action' not in page
    assert '<form method="post" action="' + base + '/create"' not in page
    js = client.get('/studio/assets/create-studio.js')
    assert js.status_code == 200
    assert "document.getElementById('sg-create-platform')" in js.text
    assert "document.getElementById('sg-create-count')" in js.text
    assert "Only the lower dispatcher composer can submit" in js.text
    dispatcher = client.get('/studio/assets/dispatcher.js').text
    assert "creation: creationHints()" in dispatcher
    assert "frame_count:selected==='story'" in dispatcher
    assert "editorial_brief:b || null" in dispatcher


def test_create_image_model_is_single_select_and_confirmable(live):
    client, _, product = live
    page = client.get(f'/studio/products/{product.id}/create?design=v4')
    assert page.status_code == 200
    assert 'id="sg-create-image-model"' in page.text
    assert 'value="gemini"' in page.text
    assert 'value="openai"' in page.text
    assert 'Автоматически · одна модель' in page.text
    script = client.get('/studio/assets/dispatcher.js')
    assert script.status_code == 200
    assert "image_model" in script.text
    assert "sg-create-image-model" in script.text


def test_home_keeps_real_dispatcher_and_voice(live):
    client, db, product = live
    page = client.get(f'/studio/products/{product.id}?design=v4')
    assert page.status_code == 200
    assert f'data-api="/studio/products/{product.id}/dispatch"' in page.text
    assert 'data-voice-url="/studio/voice/dictate"' in page.text
    assert 'id="dp-state"' in page.text
    assert 'id="dp-voice-overlay"' in page.text
    assert 'class="voice-hold-overlay vg"' in page.text
    assert 'id="vgBars"' in page.text
    assert 'id="vgKnob"' in page.text
    assert '/studio/assets/voice-glow.js?v=2' in page.text
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
    assert '/studio/assets/growth-live.css?v=18' in page.text
    assert '/studio/assets/dispatcher.js?v=voice-glow-audio-20261102' in page.text
    assert '/studio/assets/growth-live.js?v=10' in page.text
    # A video is a creation format, not a standalone main-menu section.
    nav = page.text.split('<nav class="mobile-nav"', 1)[1].split('</nav>', 1)[0]
    assert 'Простое видео' not in nav
    assert 'hero-intro' in page.text
    assert 'Добрый день, Марина' not in page.text
    assert 'initialTasks' not in page.text
    assert 'function homeV4' not in page.text
    assert 'Демо · API не подключены' not in page.text
    assert 'https://servgo.ru/mail' not in page.text
    assert 'Кампании' in page.text


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
    assert 'Подключить SEO-завод' in page.text
    assert 'command=seo_factory' not in page.text  # company has not explicitly opted in
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



def test_campaign_real_telegram_connection_requires_selection_and_stays_scoped(live):
    import json
    from app.models.domain import ArtifactStatus, Experiment
    client, db, product = live
    other = Product(name='Сторонний бизнес', slug='another-owner-channel')
    db.add(other)
    db.flush()
    campaign = Experiment(product_id=product.id, experiment_key='EXP-CHANNEL-LINK-1',
                          name='Проверяем Telegram', hypothesis='Проверка канала',
                          channel='telegram', status='running')
    db.add(campaign)
    db.flush()
    own_connection = Artifact(
        product_id=product.id, kind='telegram_channel_connection',
        title='Наш проверенный канал', content=json.dumps({'title': 'Наш проверенный канал'}),
        status=ArtifactStatus.APPROVED)
    foreign_connection = Artifact(
        product_id=other.id, kind='telegram_channel_connection',
        title='Чужой секретный канал', content='{}',
        status=ArtifactStatus.APPROVED)
    db.add_all([own_connection, foreign_connection])
    db.commit()
    path = f'/studio/products/{product.id}/campaigns/{campaign.id}'
    index = client.get(f'/studio/products/{product.id}/campaigns')
    assert 'Выбрать канал' in index.text
    assert 'Чужой секретный канал' not in index.text
    detail = client.get(path+'?design=v4')
    assert detail.status_code == 200
    assert 'Наш проверенный канал' in detail.text
    assert 'Чужой секретный канал' not in detail.text
    action = path+'/resources/channel'
    assert client.post(action, data={'connection_id': str(foreign_connection.id)}).status_code == 404
    assert client.post(action, data={'connection_id': 'broken'}).status_code == 422
    assert client.post(action, data={'connection_id': str(own_connection.id)},
                       follow_redirects=False).status_code == 303
    detail = client.get(path)
    assert 'выбран ранее проверенный канал' in detail.text
    assert 'Наш проверенный канал' in detail.text
    assert 'Пока ничего не требуется' in detail.text
    own_connection.status = ArtifactStatus.REJECTED
    db.commit()
    detail = client.get(path)
    assert 'Подключить Telegram' in detail.text
    assert 'выбран ранее проверенный канал' not in detail.text


def test_campaign_only_own_linked_material_requires_review_and_link_is_idempotent(live):
    from sqlalchemy import select
    from app.models.domain import ArtifactStatus, CostRecord, Experiment, Publication
    client, db, product = live
    other = Product(name='Сторонняя фирма', slug='foreign-owner-material')
    db.add(other)
    db.flush()
    campaign = Experiment(product_id=product.id, experiment_key='EXP-REVIEW-LINK-1',
                          name='Публикация', hypothesis='Рост заявок', status='running')
    db.add(campaign)
    db.flush()
    own = Artifact(product_id=product.id, kind='channel_text',
                   title='Наш новый материал', content='{}',
                   status=ArtifactStatus.AWAITING_APPROVAL)
    foreign = Artifact(product_id=other.id, kind='channel_text',
                       title='Чужой материал', content='{}',
                       status=ArtifactStatus.AWAITING_APPROVAL)
    other_own = Artifact(product_id=product.id, kind='channel_text',
                         title='Непривязанный материал', content='{}',
                         status=ArtifactStatus.AWAITING_APPROVAL)
    db.add_all([own, foreign, other_own])
    db.commit()
    path = f'/studio/products/{product.id}/campaigns/{campaign.id}'
    action = path + '/resources/material'
    assert 'Готов материал этой кампании' not in client.get(path).text
    assert client.post(action, data={'artifact_id': str(foreign.id)}).status_code == 404
    assert client.post(action, data={'artifact_id': 'not-a-uuid'}).status_code == 422
    for _ in range(2):
        assert client.post(action, data={'artifact_id': str(own.id)},
                           follow_redirects=False).status_code == 303
    detail = client.get(path+'?design=v4')
    assert 'Готов материал этой кампании' in detail.text
    assert 'Посмотреть материал' in detail.text
    assert 'Наш новый материал' in detail.text
    assert 'Чужой материал' not in detail.text
    # Unlinked pending material does not create a false campaign-specific approval.
    assert 'Непривязанный материал</a>' not in detail.text
    assert detail.text.count(f'href="/studio/products/{product.id}/materials/{own.id}"') >= 1
    own.status = ArtifactStatus.APPROVED
    db.commit()
    assert 'Готов материал этой кампании' not in client.get(path).text
    links = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.kind == 'campaign_workspace')))
    assert len(links) == 1
    import json
    assert json.loads(links[0].content)['material_ids'] == [str(own.id)]
    assert list(db.scalars(select(Publication))) == []
    assert list(db.scalars(select(CostRecord))) == []


def test_campaign_auto_associates_only_verified_task_brief_outputs(live):
    import json
    from app.models.domain import ArtifactStatus, Experiment, Task
    client, db, product = live
    campaign = Experiment(product_id=product.id, experiment_key='EXP-TASK-AUTO-1',
                          name='Автоматический результат', hypothesis='Тест', status='running')
    db.add(campaign)
    db.flush()
    task = Task(product_id=product.id, title='Подготовка эксперимента')
    db.add(task)
    db.flush()
    db.add_all([
        Artifact(product_id=product.id, task_id=task.id, kind='task_brief',
                 title='EXP-TASK-AUTO-1: prepare experiment',
                 content=json.dumps({'experiment_id': str(campaign.id)})),
        Artifact(product_id=product.id, task_id=task.id, kind='channel_text',
                 title='Пост из задания кампании', content='{}',
                 status=ArtifactStatus.AWAITING_APPROVAL),
    ])
    db.commit()
    path = f'/studio/products/{product.id}/campaigns/{campaign.id}'
    detail = client.get(path)
    assert 'Пост из задания кампании' in detail.text
    assert 'Готов материал этой кампании' in detail.text
    index = client.get(f'/studio/products/{product.id}/campaigns')
    assert 'Посмотреть материал' in index.text


def test_campaign_resource_post_origin_and_company_guards(live, monkeypatch, tmp_path):
    from app.models.domain import ArtifactStatus, Experiment
    from app.core.studio_accounts import create_account
    client, db, product = live
    foreign_product = Product(name='Чужая организация', slug='company-campaign-resources')
    db.add(foreign_product)
    db.flush()
    row = Experiment(product_id=product.id, experiment_key='EXP-RESOURCE-CSRF-1',
                     name='Protected campaign', hypothesis='A', status='running')
    own = Artifact(product_id=product.id, kind='channel_text',
                   title='Own draft', content='{}', status=ArtifactStatus.DRAFT)
    db.add_all([row, own])
    db.commit()
    settings = Settings(app_env='production', products_root=tmp_path,
                        studio_admin_username='owner', studio_admin_password='fixture-only',
                        studio_public_origin='https://growth.servgo.ru', _env_file=None)
    create_account(settings, username='campaign-resource-user',
                   password='local-fixture-password', role='company',
                   product_ids=[str(product.id)], created_by='test')
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    client.auth = ('campaign-resource-user', 'local-fixture-password')
    base = f'/studio/products/{product.id}/campaigns/{row.id}/resources/material'
    assert client.post(base, data={'artifact_id': str(own.id)}).status_code == 403
    assert client.post(base, data={'artifact_id': str(own.id)},
                       headers={'Origin': 'https://invalid.example'}).status_code == 403
    assert client.post(base.replace(str(product.id), str(foreign_product.id)),
                       data={'artifact_id': str(own.id)},
                       headers={'Origin': 'https://growth.servgo.ru'}).status_code == 404
    assert client.post(base, data={'artifact_id': str(own.id)},
                       headers={'Origin': 'https://growth.servgo.ru'},
                       follow_redirects=False).status_code == 303



def test_campaign_manual_measurements_compare_real_values_without_success_claim(live):
    from sqlalchemy import select
    from app.models.domain import CostRecord, Experiment, Metric, Publication, Task
    client, db, product = live
    row = Experiment(
        product_id=product.id, experiment_key='EXP-MEASUREMENT-001',
        name='Повторные записи', hypothesis='Возврат клиентов',
        target_metric='Повторные записи', success_condition='20 дополнительных записей',
        baseline_summary='Исходное значение не проверялось автоматически',
        status='running',
    )
    db.add(row)
    db.commit()
    base = f'/studio/products/{product.id}/campaigns/{row.id}'
    details = client.get(base+'?design=v4')
    assert details.status_code == 200
    assert 'Измерений пока нет — оценить эффективность ещё нельзя.' in details.text
    assert f'action="{base}/measurements"' in details.text
    samples = [
        ('baseline', '10', '2026-10-01'),
        ('result', '14', '2026-10-15'),
    ]
    for stage, amount, date in samples:
        result = client.post(base+'/measurements', data={
            'stage': stage, 'value': amount, 'unit': 'шт.',
            'observed_on': date, 'source': 'CRM (со слов владельца)',
        }, follow_redirects=False)
        assert result.status_code == 303
        assert result.headers['location'].endswith('#campaign-results')
    html = client.get(base+'?design=v4').text
    assert 'Изменение:' in html
    assert '+4' in html and '+40%' in html
    assert '2026-10-01' in html and '2026-10-15' in html
    assert 'не доказывает' in html
    assert 'источник не проверен автоматически' in html
    assert '20 дополнительных записей' in html  # hypothesis/condition, not a claim of achievement
    assert 'Условие выполнено' not in html
    readings = list(db.scalars(select(Metric).where(
        Metric.product_id == product.id, Metric.experiment_id == row.id)))
    assert len(readings) == 2
    assert {m.dimensions['stage'] for m in readings} == {'baseline', 'result'}
    assert all(m.dimensions['entry_method'] == 'owner_manual' for m in readings)
    assert list(db.scalars(select(Publication))) == []
    assert list(db.scalars(select(CostRecord))) == []
    assert list(db.scalars(select(Task))) == []


def test_campaign_comparison_requires_matching_unit_and_known_baseline(live):
    from app.models.domain import Experiment
    client, db, product = live
    row = Experiment(
        product_id=product.id, experiment_key='EXP-MEASUREMENT-002',
        name='Проверка единиц', hypothesis='Тест',
        target_metric='Заявки', status='completed',
    )
    db.add(row)
    db.commit()
    base = f'/studio/products/{product.id}/campaigns/{row.id}/measurements'
    for stage, unit, amount, date in (
        ('baseline', 'шт.', '0', '2026-09-01'),
        ('result', 'руб.', '5', '2026-10-01'),
    ):
        assert client.post(base, data={
            'stage': stage, 'value': amount, 'unit': unit,
            'observed_on': date, 'source': 'Отчёт',
        }, follow_redirects=False).status_code == 303
    output = client.get(f'/studio/products/{product.id}/campaigns/{row.id}').text
    assert 'Единицы измерения не совпадают' in output
    assert 'Изменение:' not in output
    assert client.post(base, data={
        'stage': 'result', 'value': '5', 'unit': 'шт.',
        'observed_on': '2026-10-01', 'source': 'CRM',
    }, follow_redirects=False).status_code == 303
    output = client.get(f'/studio/products/{product.id}/campaigns/{row.id}').text
    assert 'Изменение:' in output
    assert '+5' in output
    assert 'не доказывает' in output
    assert '+Infinity%' not in output
    # The CSS contains percentages; inspect the computed result, not arbitrary page text.
    assert 'Изменение: <strong>+5 шт.</strong>' in output
    assert 'Infinity%' not in output


def test_campaign_measurement_validation_company_isolation_and_no_side_effects(live):
    from sqlalchemy import select
    from app.models.domain import Experiment, Metric, Publication, Task
    client, db, product = live
    other = Product(name='Чужой бизнес для показателей', slug='metric-foreign-company')
    db.add(other)
    db.flush()
    own = Experiment(
        product_id=product.id, experiment_key='EXP-MEASUREMENT-003',
        name='Own metrics', hypothesis='Test', target_metric='Заявки', status='running',
    )
    foreign = Experiment(
        product_id=other.id, experiment_key='EXP-MEASUREMENT-004',
        name='Other metrics', hypothesis='Test', target_metric='Заявки', status='running',
    )
    db.add_all([own, foreign])
    db.commit()
    base = f'/studio/products/{product.id}/campaigns'
    valid = {'stage': 'baseline', 'value': '12,5', 'unit': 'шт.',
             'observed_on': '2026-10-10', 'source': 'CRM'}
    for modified in [
        {'stage': 'invalid'}, {'value': 'NaN'}, {'value': '1e4'},
        {'value': '1.00001'}, {'value': '100000000000000'},
        {'observed_on': '2099-01-01'}, {'observed_on': 'n/a'},
        {'source': ' '}, {'unit': 'X'*51},
    ]:
        resp = client.post(f'{base}/{own.id}/measurements', data={**valid, **modified})
        assert resp.status_code == 422
    assert client.post(f'{base}/{foreign.id}/measurements', data=valid).status_code == 404
    assert client.post(f'/studio/products/{other.id}/campaigns/{own.id}/measurements',
                       data=valid).status_code == 404
    assert list(db.scalars(select(Metric))) == []
    assert client.post(f'{base}/{own.id}/measurements', data=valid,
                       follow_redirects=False).status_code == 303
    data = list(db.scalars(select(Metric)))
    assert len(data) == 1
    assert str(data[0].value) == '12.5000'
    assert data[0].product_id == product.id
    assert data[0].experiment_id == own.id
    assert list(db.scalars(select(Task))) == []
    assert list(db.scalars(select(Publication))) == []


def test_campaign_measurement_post_company_auth_and_origin(live, monkeypatch, tmp_path):
    from app.core.studio_accounts import create_account
    from sqlalchemy import select
    from app.models.domain import Experiment, Metric
    client, db, product = live
    other = Product(name='Чужая фирма', slug='foreign-metric-access')
    db.add(other)
    db.flush()
    campaign = Experiment(
        product_id=product.id, experiment_key='EXP-MEASUREMENT-CSRF-1',
        name='Sensitive measurement', hypothesis='Test',
        target_metric='Заявки', status='running',
    )
    db.add(campaign)
    db.commit()
    settings = Settings(app_env='production', products_root=tmp_path,
                        studio_admin_username='owner', studio_admin_password='fixture-only',
                        studio_public_origin='https://growth.servgo.ru', _env_file=None)
    create_account(settings, username='metric-user', password='local-fixture-password',
                   role='company', product_ids=[str(product.id)], created_by='test')
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    client.auth = ('metric-user', 'local-fixture-password')
    data = {'stage': 'result', 'value': '2', 'unit': 'шт.',
            'observed_on': '2026-10-20', 'source': 'CRM'}
    base = f'/studio/products/{product.id}/campaigns/{campaign.id}/measurements'
    assert client.post(base, data=data).status_code == 403
    assert client.post(base, data=data,
                       headers={'Origin': 'https://attacker.invalid'}).status_code == 403
    foreign_path = f'/studio/products/{other.id}/campaigns/{campaign.id}/measurements'
    assert client.post(foreign_path, data=data,
                       headers={'Origin': 'https://growth.servgo.ru'}).status_code == 404
    assert list(db.scalars(select(Metric))) == []
    assert client.post(base, data=data, headers={'Origin': 'https://growth.servgo.ru'},
                       follow_redirects=False).status_code == 303
    assert len(list(db.scalars(select(Metric)))) == 1



def test_campaign_stop_action_is_real_scoped_and_idempotent(live):
    import json
    from sqlalchemy import select
    from app.models.domain import Experiment, Product, Task, TaskStatus
    client, db, product = live
    foreign_product = Product(name="Чужой бизнес", slug="stop-foreign-campaign")
    db.add(foreign_product)
    db.flush()
    row = Experiment(product_id=product.id, experiment_key="EXP-STOP-HTTP-1",
                     name="Кампания на остановку", hypothesis="Гипотеза", status="running")
    other = Experiment(product_id=foreign_product.id, experiment_key="EXP-STOP-HTTP-2",
                       name="Чужая кампания", hypothesis="Тест", status="running")
    db.add_all([row, other])
    db.flush()
    task = Task(product_id=product.id, title="EXP-STOP-HTTP-1: prepare experiment",
                status=TaskStatus.QUEUED)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product.id, task_id=task.id, kind="task_brief",
                    title=task.title, content=json.dumps({"experiment_id": str(row.id)})))
    db.commit()
    base = f"/studio/products/{product.id}/campaigns/{row.id}"
    page = client.get(base+"?design=v4")
    assert page.status_code == 200
    assert f'action="{base}/stop"' in page.text
    assert f'action="{base}/finish"' in page.text
    assert client.get(base+"/stop").status_code == 405
    assert client.post(f"/studio/products/{product.id}/campaigns/{other.id}/stop").status_code == 404
    assert client.post(base+"/stop", follow_redirects=False).status_code == 303
    db.refresh(row)
    db.refresh(task)
    assert row.status == "stopped"
    assert task.status == TaskStatus.CANCELLED
    assert client.post(base+"/stop", follow_redirects=False).status_code == 303
    assert client.post(base+"/finish").status_code == 409
    assert db.get(Experiment, other.id).status == "running"
    result = client.get(base)
    assert "Остановлена" in result.text
    assert f'action="{base}/stop"' not in result.text


def test_campaign_pending_stop_reports_inflight_status_and_reconciles(live):
    import json
    from sqlalchemy import select
    from app.models.domain import Experiment, Task, TaskStatus
    from app.services.campaign_lifecycle import reconcile_pending
    client, db, product = live
    row = Experiment(product_id=product.id, experiment_key="EXP-STOP-PENDING-1",
                     name="Ожидание завершения", hypothesis="Тест", status="running")
    db.add(row)
    db.flush()
    task = Task(product_id=product.id, title="EXP-STOP-PENDING-1: prepare experiment",
                status=TaskStatus.RUNNING)
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product.id, task_id=task.id, kind="task_brief",
                    title=task.title, content=json.dumps({"experiment_id": str(row.id)})))
    db.commit()
    url = f"/studio/products/{product.id}/campaigns/{row.id}"
    assert client.post(url+"/stop", follow_redirects=False).status_code == 303
    db.refresh(row)
    assert row.status == "stopping"
    page = client.get(url+"?design=v4")
    assert "Останавливается" in page.text
    assert "Активные задания: 1" in page.text
    assert "Обновить состояние" in page.text
    task.status = TaskStatus.COMPLETED
    db.commit()
    assert reconcile_pending(db) == 1
    db.refresh(row)
    assert row.status == "stopped"
    assert "Остановлена" in client.get(url).text


def test_campaign_finish_post_company_auth_and_origin(live, monkeypatch, tmp_path):
    from app.core.studio_accounts import create_account
    from sqlalchemy import select
    from app.models.domain import Experiment, Decision
    client, db, product = live
    other = Product(name="Чужая организация", slug="foreign-finalization")
    db.add(other)
    db.flush()
    row = Experiment(product_id=product.id, experiment_key="EXP-FINISH-HTTP-1",
                     name="Завершаемый эксперимент", hypothesis="Тест",
                     status="running")
    db.add(row)
    db.commit()
    settings = Settings(app_env="production", products_root=tmp_path,
                        studio_admin_username="owner", studio_admin_password="fixture-only",
                        studio_public_origin="https://growth.servgo.ru", _env_file=None)
    create_account(settings, username="campaign-owner",
                   password="local-fixture-password", role="company",
                   product_ids=[str(product.id)], created_by="test")
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    client.auth = ("campaign-owner", "local-fixture-password")
    url = f"/studio/products/{product.id}/campaigns/{row.id}/finish"
    payload = {"note": "<b>Вывод владельца</b>"}
    assert client.post(url, data=payload).status_code == 403
    assert client.post(url, data=payload,
                       headers={"Origin": "https://invalid.example"}).status_code == 403
    assert client.post(url.replace(str(product.id), str(other.id)),
                       data=payload,
                       headers={"Origin": "https://growth.servgo.ru"}).status_code == 404
    assert client.post(url, data=payload,
                       headers={"Origin": "https://growth.servgo.ru"},
                       follow_redirects=False).status_code == 303
    db.refresh(row)
    assert row.status == "completed"
    assert row.conclusion == "Вывод владельца: <b>Вывод владельца</b>"
    page = client.get(f"/studio/products/{product.id}/campaigns/{row.id}")
    assert "&lt;b&gt;Вывод владельца&lt;/b&gt;" in page.text
    assert "<b>Вывод владельца</b>" not in page.text
    assert list(db.scalars(select(Decision))) == []


def test_approved_growth_home_has_floating_transparent_overlay():
    """Keep the approved #192 layout from regressing to flow/separate grey bands."""
    from app.api.growth_v4 import ASSETS
    css = (ASSETS / "growth-live.css").read_text(encoding="utf-8")
    js = (ASSETS / "growth-live.js").read_text(encoding="utf-8")
    start = css.index("Approved home-screen geometry")
    end = css.index("/* New Topic:", start)
    rules = css[start:end]
    assert "position:absolute;" in rules
    assert "padding:4px 8px calc(var(--v4-dock-height,142px) + 16px) 4px;" in rules
    assert "background:transparent!important;" in rules
    assert "pointer-events:none;" in rules
    assert "border-bottom:0!important;" in rules
    assert "background:var(--card);" in rules
    assert "ResizeObserver(measureDock)" in js
    assert "--v4-dock-height" in js
    assert "position:relative;\n  left:auto;\n  right:auto;" not in rules



def test_canonical_growth_defaults_to_v4_without_changing_legacy_preference(live, monkeypatch):
    client, db, product = live
    config = Settings(app_env="development", studio_public_origin="https://growth.servgo.ru",
                      studio_servgo_gateway_enabled=True, _env_file=None)
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: config)
    # This test checks theme routing, not authentication. The canonical host
    # now strictly requires ServGo SSO, even in development; provide an
    # explicit test-only authenticated request rather than weakening production.
    from fastapi import Request
    from app.api.content_studio_auth import require_studio_auth

    def layout_test_account(request: Request):
        request.state.studio_login = "layout-test-account"
        request.state.studio_account_role = "admin"
        request.state.studio_product_ids = None
        return "layout-test-account"

    monkeypatch.setitem(app.dependency_overrides, require_studio_auth, layout_test_account)
    base = f"/studio/products/{product.id}"
    with TestClient(app, base_url="https://servgo.ru") as canonical:
        page = canonical.get(base)
        assert page.status_code == 200
        assert 'data-growth-design="v4"' in page.text
        assert 'id="dp-state"' in page.text
        assert 'href="https://servgo.ru/mail"' not in page.text
        # Users who choose Classic explicitly keep their preference.
        classic = canonical.get(base + "?design=classic")
        assert classic.status_code == 200
        assert 'data-growth-design="v4"' not in classic.text
        assert canonical.cookies.get("growth_design") == "classic"
    with TestClient(app, base_url="https://growth.servgo.ru") as legacy:
        page = legacy.get(base + "/work")
        assert page.status_code == 200
        assert 'data-growth-design="v4"' not in page.text



def test_explicit_classic_wins_even_if_dispatcher_is_enabled(live, monkeypatch):
    from app.api import dispatcher
    client, db, product = live
    monkeypatch.setattr(dispatcher, "enabled_for", lambda *_: True)
    page = client.get(f"/studio/products/{product.id}?design=classic")
    assert page.status_code == 200
    assert 'data-growth-design="v4"' not in page.text
    assert 'id="dp-state"' not in page.text
    assert 'Новый дизайн' in page.text



def test_mobile_seo_dashboard_prioritizes_status_and_hides_process_details(live):
    client, db, product = live
    page = client.get(f'/studio/products/{product.id}/seo?design=v4')
    assert page.status_code == 200
    assert page.text.index('class="panel seo-activation"') < page.text.index('class="seo-process-overview"')
    assert '<summary>Как создаётся статья <span>6 этапов</span></summary>' in page.text
    assert 'class="seo-dash-stats"' in page.text
    assert 'Запусков пока нет.' in page.text
    assert 'Подключить SEO-завод' in page.text
    assert 'после отдельного серверного выпуска' not in page.text
    assert '/studio/assets/growth-servgo-air.css?v=8' in page.text


def test_notification_bell_is_real_svg_centered_on_every_v4_screen(live):
    client, db, product = live
    from app.api.growth_v4 import Document
    for route in ('', '/seo', '/work', '/create'):
        page = client.get(f'/studio/products/{product.id}{route}?design=v4')
        assert page.status_code == 200
        doc = Document(page.text)
        widget = doc.find(cls='sg-notifications')
        assert widget is not None
        markup = doc.outer(widget)
        assert 'href="#i-bell"' in markup
        assert 'data-sg-count' in markup
        assert 'data-sg-read-all' in markup
    css = client.get('/studio/assets/growth-servgo-air.css').text
    assert '.top-actions .sg-notifications>summary' in css
    assert 'place-items:center' in css
    assert 'width:44px' in css
    assert '.sg-notifications>summary>svg.icon' in css


def test_create_includes_aspect_controls_and_story_vertical_hint(live):
    client, _, product = live
    html = client.get(f"/studio/products/{product.id}/create?design=v4").text
    assert 'id="sg-create-aspect"' in html
    assert 'id="sg-create-story-ratio"' in html
    assert 'value="4:5"' in html and 'value="16:9"' in html
    assert "вертикальные 9:16" in html
    create_script = client.get("/studio/assets/create-studio.js").text
    assert "aspectRow.hidden=key!=='static_image'" in create_script
    dispatcher_script = client.get("/studio/assets/dispatcher.js").text
    assert "aspect_ratio:selected==='story' ? '9:16'" in dispatcher_script
    assert "kind.addEventListener('change',function(){aspect.value='';refreshOptions();});" in dispatcher_script


def test_create_timing_explains_status_without_unverified_estimates(live):
    client, _, product = live
    html = client.get(f"/studio/products/{product.id}/create?design=v4").text
    assert '<details class="sg-create-timing"' in html
    assert "Сколько занимает создание?" in html
    assert "фактическим статусом" in html
    assert "точное время заранее не гарантируется" in html


def test_voice_glow_uses_existing_audio_stream(live):
    client, _, _ = live
    res = client.get('/studio/assets/voice-glow.js')
    assert res.status_code == 200
    assert res.headers['content-type'].startswith('application/javascript')
    assert "createMediaStreamSource(stream)" in res.text
    assert "getUserMedia(" not in res.text
    assert "stream.getTracks().forEach" not in res.text
    assert "getByteTimeDomainData" in res.text
    assert "recognize: recognize" in res.text
    assert "setKnobX: setKnobX" in res.text
    dispatcher = client.get('/studio/assets/dispatcher.js').text
    assert "window.VoiceGlow.start(stream)" in dispatcher
    assert "window.VoiceGlow.recognize()" in dispatcher
    assert "window.VoiceGlow.hide()" in dispatcher
    assert "window.VoiceGlow.arm()" in dispatcher
    assert "function arm()" in res.text
    assert "attempt !== resumeAttempt" in res.text
    assert "Math.sqrt(sum / samples.length) - .003" in res.text


def test_v4_work_board_shows_seo_article_topic_in_russian_for_existing_jobs(live):
    """Old task IDs remain worker deduplication keys; owner never sees them."""
    client, db, product = live
    plan_id = uuid4()
    raw_title = f"SEO-ARTICLE:{plan_id}:a002"
    legacy_description = ("SEO article, channel adaptations, two video scripts. "
                          "No automatic publication or video rendering.")
    task = Task(product_id=product.id, assigned_agent="content_factory",
                title=raw_title, description=legacy_description,
                status=TaskStatus.QUEUED)
    no_request = Task(product_id=product.id, assigned_agent="content_factory",
                      title=f"SEO-ARTICLE:{plan_id}:a003",
                      description=legacy_description, status=TaskStatus.QUEUED)
    unrelated = Product(name="Another tenant", slug=f"another-{uuid4().hex[:8]}")
    db.add_all([task, no_request, unrelated])
    db.flush()
    foreign = Task(product_id=unrelated.id, assigned_agent="content_factory",
                   title=f"SEO-ARTICLE:{plan_id}:a004", status=TaskStatus.QUEUED)
    request = Artifact(product_id=product.id, task_id=task.id, kind="content_request",
                       title="Пакет контента", content=json.dumps({
                           "topic": "Как увеличить запись клиентов в сервис",
                           "seo_brief": {"primary_query": "онлайн запись для сервиса"},
                       }, ensure_ascii=False))
    db.add_all([request, foreign])
    db.commit()

    base = f"/studio/products/{product.id}"
    response = client.get(base + "/v4/data?design=v4")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    tasks = {row["id"]: row for row in response.json()["tasks"]}
    assert str(foreign.id) not in tasks
    assert tasks[str(task.id)]["title"] == "SEO-статья: Как увеличить запись клиентов в сервис"
    assert tasks[str(task.id)]["detail"] == "Создание SEO-статьи по контент-плану."
    assert tasks[str(no_request.id)]["title"] == "SEO-статья по контент-плану"
    assert tasks[str(no_request.id)]["detail"] == "Создание SEO-статьи по контент-плану."
    assert all("SEO-ARTICLE:" not in row["title"] for row in tasks.values())
    assert all("No automatic publication" not in row["detail"] for row in tasks.values())
    # Changing UI labels must never break deduplication or task links.
    assert db.get(Task, task.id).title == raw_title
    detail = client.get(f"{base}/tasks/{task.id}?design=classic")
    assert detail.status_code == 200
    assert "SEO-статья: Как увеличить запись клиентов в сервис" in detail.text
    assert raw_title not in detail.text


def test_new_content_task_descriptions_are_russian_and_seo_specific(live):
    from app.content.service import queue_content
    _, db, product = live
    package = queue_content(db, product.id, "Как клиент записывается онлайн")
    seo_title = f"SEO-ARTICLE:{uuid4()}:a001"
    seo = queue_content(db, product.id, "Как привлечь клиентов через поиск",
                        dedupe_key=seo_title)
    assert "Подготовка статьи, адаптаций" in package.description
    assert "SEO article" not in package.description
    assert seo.description == "Создание SEO-статьи по контент-плану."
    assert seo.title == seo_title
