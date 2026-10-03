import json
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4
from contextlib import contextmanager
from urllib.parse import parse_qs,urlsplit
import pytest
from sqlalchemy import select
from app.communications.links import chat_link
from app.communications.mail import MailStore, SMTPConfig, public_ip, send_email
from app.communications.outreach import prepare_email, execute_email, eligible
from app.models.domain import Product, Experiment, Prospect, ToolCall, Artifact, Approval
from app.services.artifacts import ArtifactService

@pytest.fixture
def contact(db,tmp_path):
    p=Product(name='ServGo',slug='servgo');db.add(p);db.flush()
    e=Experiment(product_id=p.id,name='Test',hypothesis='Demand',status='running');db.add(e);db.flush()
    prospect=Prospect(product_id=p.id,experiment_id=e.id,lead_key='L1',business_name='SPA',
        validation_status='validated',outreach_status='not_contacted',preferred_channel='email',
        email='spa@example.com',tracking_url='https://promo.servgo.ru/?id=L1',source_checked_at=date.today(),
        notes='SPA',evidence=[{'field':'email','value':'spa@example.com','source_url':'https://example.com'}])
    db.add(prospect);db.commit()
    profile={'schema_version':'1.0','identity':{'name':'ServGo','one_liner':'Онлайн-запись','languages':['ru']},
        'offer':{'products_services':[{'name':'Запись','description':'Онлайн-запись'}],'primary_conversion_action':'Посмотреть демо'},
        'customers':{'primary_segments':['Салоны']},'brand':{'prohibited_claims':[]},
        'constraints':{'privacy':[],'legal_compliance':[],'operational':[],'approval_rules':[]},
        'outreach':{'icp_criteria':['Салоны'],'allowed_channels':['email','telegram','whatsapp'],
                    'sender_identity':'Артур','approval_required':True,'do_not_contact':[]}}
    (tmp_path/'servgo').mkdir();(tmp_path/'servgo/company-profile.json').write_text(json.dumps(profile))
    config=SMTPConfig(host='smtp.example.com',username='artur@example.com',password='application-secret',
                      from_email='artur@example.com',from_name='Артур GrunvaldLab')
    MailStore(tmp_path).save(p.id,config)
    return p,e,prospect,config


def test_official_chat_links_preserve_contact_and_exact_cyrillic_draft():
    body='Здравствуйте! Демо: https://example.com/?a=1&b=2\nАртур'
    for channel,target,path in [('telegram','@spa_sochi','/spa_sochi'),('whatsapp','+7 (999) 000-00-00','/79990000000')]:
        url=chat_link(channel,target,body)
        assert urlsplit(url).path==path
        assert parse_qs(urlsplit(url).query)['text']==[body]
    with pytest.raises(ValueError):chat_link('telegram','https://evil.example/x',body)
    with pytest.raises(ValueError):chat_link('whatsapp','https://evil.example',body)


def test_mail_config_encrypted_private_persistent_and_product_scoped(tmp_path):
    store=MailStore(tmp_path);pid=uuid4()
    config=SMTPConfig(host='smtp.example.com',username='user',password='my-sensitive-password',from_email='a@example.com')
    store.save(pid,config)
    assert b'my-sensitive-password' not in store.path(pid).read_bytes()
    assert store.path(pid).stat().st_mode & 0o777==0o600
    assert MailStore(tmp_path).load(pid).password.get_secret_value()=='my-sensitive-password'
    assert store.load(uuid4()) is None
    assert 'my-sensitive-password' not in repr(config)
    store.delete(pid);assert store.load(pid) is None

@pytest.mark.parametrize('host,port,address',[('localhost',465,'a@example.com'),('smtp.example.com',25,'a@example.com'),('smtp.example.com',465,'a@example.com\r\nBcc: x@example.com')])
def test_invalid_smtp_settings_rejected(host,port,address):
    with pytest.raises(ValueError):SMTPConfig(host=host,port=port,username='u',password='p',from_email=address)


def test_private_and_mixed_dns_blocked(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',465)),(2,1,6,'',('1.1.1.1',465))])
    with pytest.raises(ValueError):public_ip('smtp.example.com',465)

@pytest.mark.asyncio
async def test_email_exact_review_one_click_no_duplicate(db,tmp_path,contact):
    p,e,prospect,config=contact
    aid,token,draft=prepare_email(db,prospect.id,tmp_path)
    assert prospect.outreach_status=='not_contacted' and list(db.scalars(select(ToolCall)))==[]
    sent=[]
    @contextmanager
    def sessions():yield db
    def sender(config,**kwargs):sent.append(kwargs)
    result=await execute_email(sessions,tmp_path,aid,token,'telegram:1',sender)
    assert 'принял' in result and sent[0]['body']==draft['body'] and sent[0]['recipient']==prospect.email
    with pytest.raises(ValueError):await execute_email(sessions,tmp_path,aid,token,'telegram:1',sender)
    assert len(sent)==1 and prospect.outreach_status=='contacted'
    call=db.scalar(select(ToolCall));assert call.status=='smtp_accepted'
    assert call.response_data['delivery_confirmed'] is False
    assert 'application-secret' not in json.dumps(call.request_data)

@pytest.mark.asyncio
async def test_changed_target_and_mail_config_block_old_send(db,tmp_path,contact):
    p,e,prospect,config=contact
    aid,token,draft=prepare_email(db,prospect.id,tmp_path)
    MailStore(tmp_path).save(p.id,config)
    @contextmanager
    def sessions():yield db
    with pytest.raises(ValueError,match='Настройки почты'):
        await execute_email(sessions,tmp_path,aid,token,'telegram:1',lambda *a,**k:pytest.fail('Must not send'))
    assert list(db.scalars(select(ToolCall)))==[]

@pytest.mark.asyncio
async def test_unknown_email_delivery_never_retried(db,tmp_path,contact):
    p,e,prospect,config=contact
    aid,token,_=prepare_email(db,prospect.id,tmp_path)
    @contextmanager
    def sessions():yield db
    def timeout(*a,**k):raise TimeoutError('sensitive debug information')
    result=await execute_email(sessions,tmp_path,aid,token,'telegram:1',timeout)
    assert 'Не могу подтвердить' in result and 'sensitive' not in result
    assert prospect.outreach_status=='delivery_unknown'
    with pytest.raises(ValueError):await execute_email(sessions,tmp_path,aid,token,'telegram:1',timeout)


def test_message_immutable_and_stale_contact_rejected(db,tmp_path,contact):
    p,e,prospect,config=contact
    aid,token,_=prepare_email(db,prospect.id,tmp_path)
    approval=db.get(Approval,aid)
    with pytest.raises(ValueError):ArtifactService(db).update(approval.artifact_id,content='changed')
    prospect.source_checked_at=date.today()-timedelta(days=31)
    with pytest.raises(ValueError,match='актуальность'):eligible(db,prospect.id,tmp_path,'email')

@pytest.mark.asyncio
async def test_definite_smtp_rejection_allows_retry_after_configuration_fix(db,tmp_path,contact):
    import smtplib
    p,e,prospect,config=contact
    aid,token,_=prepare_email(db,prospect.id,tmp_path)
    @contextmanager
    def sessions():yield db
    def refused(*a,**k):raise smtplib.SMTPAuthenticationError(535,b'Authentication failed')
    result=await execute_email(sessions,tmp_path,aid,token,'telegram:1',refused)
    assert 'не принято' in result
    assert prospect.outreach_status=='not_contacted'
    assert db.scalar(select(ToolCall)).status=='not_sent'
