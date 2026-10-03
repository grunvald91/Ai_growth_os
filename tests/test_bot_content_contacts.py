import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.telegram_control_bot import TelegramControlBot
from app.models.domain import Product, Task, Artifact

class FakeAPI:
    def __init__(self):self.calls=[];self.uploads=[]
    async def call(self,method,payload=None):self.calls.append((method,payload));return {}
    async def upload(self,*args):self.uploads.append(args)

def setup(db,tmp_path,monkeypatch):
    @contextmanager
    def factory():yield db
    monkeypatch.setattr('app.telegram_control_bot.SessionLocal',factory)
    monkeypatch.setattr('app.telegram_control_bot.get_settings',lambda:SimpleNamespace(products_root=tmp_path))
    api=FakeAPI();bot=TelegramControlBot(api,123)
    p=Product(name='ServGo',slug='servgo');db.add(p);db.commit()
    return bot,api,p

def message(text,user=123):return {'message':{'from':{'id':user},'chat':{'id':user},'message_id':10,'text':text}}
def callback(data,user=123):return {'callback_query':{'id':'callback','from':{'id':user},'message':{'chat':{'id':user}},'data':data}}

@pytest.mark.asyncio
async def test_owner_content_queue_from_bot_and_foreign_user_blocked(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    await bot.handle_update(message('📝 Контент',999));assert api.calls==[]
    await bot.handle_update(message('📝 Контент'))
    assert 'Контент' in api.calls[-1][1]['text']
    await bot.handle_update(callback(f'content:queue:{p.id}'))
    assert db.query(Task).filter(Task.assigned_agent=='content_factory').count()==1
    await bot.handle_update(callback(f'content:queue:{p.id}'))
    assert db.query(Task).filter(Task.assigned_agent=='content_factory').count()==1

@pytest.mark.asyncio
async def test_smtp_wizard_deletes_secret_before_save_never_leaks(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    await bot.handle_update(callback(f'mail:setup:{p.id}'))
    for text in ['smtp.gmail.com 465','artur@example.com','тот же','Артур GrunvaldLab','app-password-secret']:
        await bot.handle_update(message(text))
    assert any(m=='deleteMessage' for m,_ in api.calls)
    assert 'app-password-secret' not in json.dumps(api.calls,ensure_ascii=False)
    from app.communications.mail import MailStore
    assert MailStore(tmp_path).load(p.id).password.get_secret_value()=='app-password-secret'

@pytest.mark.asyncio
async def test_secret_not_saved_if_sensitive_message_cannot_be_deleted(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    await bot.handle_update(callback(f'mail:setup:{p.id}'))
    for text in ['smtp.gmail.com 465','artur@example.com','тот же','Артур']:
        await bot.handle_update(message(text))
    original=api.call
    async def failing(method,payload=None):
        if method=='deleteMessage':raise RuntimeError('Cannot delete')
        return await original(method,payload)
    api.call=failing
    await bot.handle_update(message('do-not-persist'))
    from app.communications.mail import MailStore
    assert MailStore(tmp_path).load(p.id) is None
    assert 'do-not-persist' not in json.dumps(api.calls,ensure_ascii=False)

@pytest.mark.asyncio
async def test_telegram_long_unicode_copy_is_split_at_utf16_limit(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    await bot._send(123,'😀'*6000,{'inline_keyboard':[]})
    sends=[v for k,v in api.calls if k=='sendMessage']
    assert ''.join(v['text'] for v in sends)=='😀'*6000
    assert all(len(v['text'].encode('utf-16-le'))//2<=4096 for v in sends)
    assert all('reply_markup' not in v for v in sends[:-1])
    assert 'reply_markup' in sends[-1]
