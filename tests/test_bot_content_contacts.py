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


@pytest.mark.asyncio
async def test_bot_package_buttons_deliver_one_material_without_zip_or_metadata(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    task=Task(product_id=p.id,title='Контент',assigned_agent='content_factory')
    db.add(task);db.flush()
    article=Artifact(product_id=p.id,task_id=task.id,kind='article',title='Пакет',
        content=json.dumps({'full_article':'# Основная статья\n\nТекст статьи','seo':{'internal':'PRIVATE_SEO'}}))
    db.add(article);db.flush()
    post=Artifact(product_id=p.id,task_id=task.id,kind='channel_text',title='Telegram',
        content=json.dumps({'article_id':str(article.id),'channel':'telegram','title':'Заголовок поста',
            'body':'Первый **важный** абзац.\n\nВторой абзац.','editorial_checks':['PRIVATE_CHECK']}))
    video=Artifact(product_id=p.id,task_id=task.id,kind='video_script',title='Reels',
        content=json.dumps({'article_id':str(article.id),'production_brief':{
            'target_platform':'instagram_reels','voiceover_or_dialogue_script':'Речь ведущего',
            'shots':[{'start_second':0,'end_second':5,'visual_description':'Кадр ведущего',
                'on_screen_text':'Надпись','generation_prompt':'PRIVATE_PROMPT'}]}}))
    db.add_all([post,video]);db.commit()

    await bot.handle_update(callback(f'content:article:{article.id}'))
    menu=[v for k,v in api.calls if k=='sendMessage'][-1]
    buttons=[b for row in menu['reply_markup']['inline_keyboard'] for b in row]
    assert any(b['callback_data']==f'content:asset:{article.id}' for b in buttons)
    assert any(b['callback_data']==f'content:asset:{post.id}' and 'Telegram' in b['text'] for b in buttons)
    assert any(b['callback_data']==f'content:asset:{video.id}' and 'Instagram Reels' in b['text'] for b in buttons)
    assert not any(b['callback_data'].startswith('content:download:') for b in buttons)
    assert 'Текст статьи' not in menu['text']
    assert api.uploads==[]

    api.calls.clear()
    await bot.handle_update(callback(f'content:asset:{post.id}'))
    copy=''.join(v['text'] for k,v in api.calls if k=='sendMessage')
    assert copy=='Заголовок поста\n\nПервый важный абзац.\n\nВторой абзац.'
    assert 'PRIVATE_' not in copy and 'article_id' not in copy

    api.calls.clear()
    await bot.handle_update(callback(f'content:asset:{video.id}'))
    copy=''.join(v['text'] for k,v in api.calls if k=='sendMessage')
    assert 'Речь ведущего' in copy and 'Кадр ведущего' in copy and 'Надпись' in copy
    assert 'PRIVATE_PROMPT' not in copy

    api.calls.clear()
    await bot.handle_update(callback(f'content:asset:{article.id}'))
    copy=''.join(v['text'] for k,v in api.calls if k=='sendMessage')
    assert copy=='Основная статья\n\nТекст статьи'
    assert api.uploads==[]

    await bot.handle_update(callback(f'content:download:{article.id}'))
    assert api.uploads==[]
    assert 'Выберите материал' in [v for k,v in api.calls if k=='sendMessage'][-1]['text']


@pytest.mark.asyncio
async def test_selected_long_material_is_complete_and_foreign_user_cannot_receive_it(db,tmp_path,monkeypatch):
    bot,api,p=setup(db,tmp_path,monkeypatch)
    body='Полный текст 😀.\n\n'*1200
    post=Artifact(product_id=p.id,kind='channel_text',title='Большой пост',
        content=json.dumps({'channel':'telegram','body':body},ensure_ascii=False))
    db.add(post);db.commit()
    await bot.handle_update(callback(f'content:asset:{post.id}',999))
    assert api.calls==[]
    await bot.handle_update(callback(f'content:asset:{post.id}'))
    sends=[v for k,v in api.calls if k=='sendMessage']
    assert len(sends)>1
    assert ''.join(v['text'] for v in sends)==body.strip()
    assert all(len(v['text'].encode('utf-16-le'))//2<=4096 for v in sends)
    assert all('reply_markup' not in v for v in sends[:-1])
    assert 'reply_markup' in sends[-1] and api.uploads==[]
