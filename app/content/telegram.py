"""Owner-only content queue, review, downloads and manual performance observations."""
import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field
from app.content.render import asset_path
from app.content.service import queue_content, queue_render
from app.models.domain import Artifact, Metric, Product, Task

LABELS = {'own_site':'SEO / сайт', 'dzen':'Дзен', 'tenchat':'TenChat', 'vc_ru':'vc.ru',
          'telegram':'Telegram', 'faq':'FAQ', 'founder':'Пост автора'}

class Observations(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=500)
    views: int | None = Field(default=None, ge=0, strict=True)
    clicks: int | None = Field(default=None, ge=0, strict=True)
    leads: int | None = Field(default=None, ge=0, strict=True)
    activations: int | None = Field(default=None, ge=0, strict=True)
    customers: int | None = Field(default=None, ge=0, strict=True)

class TelegramContent:
    def __init__(self, bot, session_factory, settings_factory):
        self.bot, self.sessions, self.settings = bot, session_factory, settings_factory
        self.topic_product = None
        self.metrics_article = None

    async def menu(self, chat_id):
        self.topic_product = self.metrics_article = None
        with self.sessions() as db:
            products = list(db.scalars(select(Product).order_by(Product.name).limit(20)))
            buttons = [[{'text':p.name, 'callback_data':f'content:product:{p.id}'}] for p in products]
        await self.bot._send(chat_id, '📝 Контент — статьи, адаптации и короткие видео.\nВыбери компанию.',
                             reply_markup={'inline_keyboard':buttons})

    async def product(self, chat_id, product_id):
        with self.sessions() as db:
            product = db.get(Product, product_id)
            if product is None:
                raise LookupError('Компания не найдена')
            tasks = list(db.scalars(select(Task).where(Task.product_id == product.id,
                Task.assigned_agent.in_(['content_factory', 'content_renderer']))
                .order_by(Task.created_at.desc()).limit(8)))
            articles = list(db.scalars(select(Artifact).where(Artifact.product_id == product.id,
                Artifact.kind == 'article').order_by(Artifact.created_at.desc()).limit(8)))
            statuses = {'queued':'в очереди','running':'готовится','completed':'готово', 'failed':'ошибка'}
            text = f'📝 {product.name}\n\nПакет: SEO-статья, Дзен, TenChat, vc.ru, Telegram, FAQ, пост автора и три сценария по 30 секунд.\nРендер MP4 запускается после просмотра сценариев. Публикация — после проверки, вручную до подключения площадок.'
            if tasks:
                text += '\n\nПоследние задачи:\n' + '\n'.join(f'• {t.title[:65]} — {statuses.get(t.status.value, t.status.value)}' for t in tasks)
            buttons = [[{'text':'▶️ Создать пакет', 'callback_data':f'content:queue:{product.id}'},
                        {'text':'✍️ Своя тема', 'callback_data':f'content:topic:{product.id}'}]]
            buttons += [[{'text':a.title[:60], 'callback_data':f'content:article:{a.id}'}] for a in articles]
            buttons.append([{'text':'🔄 Обновить', 'callback_data':f'content:product:{product.id}'}])
        await self.bot._send(chat_id, text, reply_markup={'inline_keyboard':buttons})

    async def message(self, message):
        text = (message.get('text') or '').strip()
        chat = message['chat']['id']
        if text in {'📝 Контент', '/content'}:
            await self.menu(chat)
            return True
        if text.startswith('/') or text in {'📊 Главное','✅ На согласование','🧪 Тесты','🔄 Обновить','🏢 Компания','📇 Контакты','📧 Почта'}:
            self.topic_product = self.metrics_article = None
            return False
        if self.topic_product:
            try:
                with self.sessions() as db:
                    queue_content(db, self.topic_product, text)
                self.topic_product = None
                await self.bot._send(chat, '✅ Тема принята. Пакет поставлен в очередь.')
            except ValueError:
                await self.bot._send(chat, 'Напиши тему одним сообщением, до 300 символов.')
            return True
        if self.metrics_article:
            try:
                observation = Observations.model_validate_json(text)
                with self.sessions() as db:
                    article = db.get(Artifact, self.metrics_article)
                    if article is None or article.kind != 'article':
                        raise LookupError('Статья недоступна')
                    for name in ('views','clicks','leads','activations','customers'):
                        value = getattr(observation, name)
                        if value is not None:
                            db.add(Metric(product_id=article.product_id, name='content_' + name, value=value,
                                unit='count', source='owner_report', observed_at=datetime.now(timezone.utc),
                                dimensions={'article_id':str(article.id), 'channel':observation.channel, 'url':observation.url,
                                    'measurement_type':'cumulative_snapshot', 'verified_externally':False}))
                    db.commit()
                self.metrics_article = None
                await self.bot._send(chat, '✅ Показатели сохранены как наблюдения владельца. Неуказанные значения остаются неизвестными.')
            except (ValueError, LookupError):
                await self.bot._send(chat, 'Не удалось прочитать показатели. Используй показанный JSON; числа — целые и неотрицательные.')
            return True
        return False

    async def article(self, chat, article_id, download=False):
        with self.sessions() as db:
            article = db.get(Artifact, article_id)
            if article is None or article.kind != 'article':
                raise LookupError('Статья не найдена')
            data = json.loads(article.content)
            related = list(db.scalars(select(Artifact).where(Artifact.task_id == article.task_id,
                Artifact.product_id == article.product_id, Artifact.kind.in_(['channel_text','video_script','content_distribution']))))
            renders = []
            for render in db.scalars(select(Artifact).where(Artifact.product_id == article.product_id,
                Artifact.kind == 'video_render')):
                if json.loads(render.content).get('article_id') == str(article.id):
                    renders.append(render)
            if download:
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('seo-article.md', data['full_article'])
                    archive.writestr('seo-and-measurement.json', json.dumps({'seo':data['seo'], 'measurement':data['conversion']['measurement']}, ensure_ascii=False, indent=2))
                    for index, item in enumerate(related):
                        value = json.loads(item.content)
                        if item.kind == 'channel_text':
                            archive.writestr(value['channel'] + '.md', '# ' + value['title'] + '\n\n' + value['body'])
                        else:
                            archive.writestr(f'{item.kind}-{index}.json', json.dumps(value, ensure_ascii=False, indent=2))
                await self.bot.api.upload(chat, 'sendDocument', 'document', 'content-package.zip', stream.getvalue())
                return
            buttons = [[{'text':'📦 Скачать тексты и сценарии', 'callback_data':f'content:download:{article.id}'}]]
            for item in related:
                buttons.append([{'text': (LABELS.get(json.loads(item.content).get('channel')) or item.title)[:60],
                    'callback_data':f'content:asset:{item.id}'}])
            buttons.append([{'text':'🎬 Одобрить рендер трёх MP4', 'callback_data':f'content:render:{article.id}'}])
            for item in renders:
                buttons.append([{'text':'🎞 ' + item.title[:55], 'callback_data':f'content:video:{item.id}'}])
            buttons.append([{'text':'📈 Записать результаты публикации', 'callback_data':f'content:metrics:{article.id}'}])
        await self.bot._send(chat, data['full_article'], reply_markup={'inline_keyboard':buttons})

    async def callback(self, chat, data):
        try:
            _, action, raw = data.split(':',2)
            record_id = UUID(raw)
            if action == 'product':
                await self.product(chat, record_id)
            elif action in {'queue','topic'}:
                if action == 'topic':
                    self.topic_product = record_id
                    await self.bot._send(chat, 'Напиши тему статьи одним сообщением, до 300 символов.')
                else:
                    with self.sessions() as db:
                        queue_content(db, record_id)
                    await self.bot._send(chat, '✅ Пакет контента поставлен в очередь. Результат появится в этом разделе.')
                    await self.product(chat, record_id)
            elif action in {'article','download'}:
                await self.article(chat, record_id, download=action == 'download')
            elif action == 'render':
                with self.sessions() as db:
                    queue_render(db, record_id)
                await self.bot._send(chat, '✅ Рендер одобрен и поставлен в очередь. Три вертикальных MP4 будут доступны в статье. Это ролики с движущимися текстовыми карточками; озвучка и музыка пока не подключены.')
            elif action == 'asset':
                with self.sessions() as db:
                    item = db.get(Artifact, record_id)
                    if item is None or item.kind not in {'channel_text','video_script','content_distribution'}:
                        raise LookupError('Материал не найден')
                    value = json.loads(item.content)
                    if item.kind == 'channel_text':
                        text = '# ' + value['title'] + '\n\n' + value['body']
                    elif item.kind == 'video_script':
                        brief = value['production_brief']
                        text = ('🎬 ' + item.title + '\n\nЗадача: ' + brief['objective']
                                + '\n\nТекст для озвучки:\n' + brief['voiceover_or_dialogue_script']
                                + '\n\nСцены:')
                        for shot in brief['shots']:
                            text += (f"\n\n{shot['start_second']}–{shot['end_second']} сек."
                                     + '\nНа экране: ' + (shot.get('on_screen_text') or '')
                                     + '\nИдея кадра: ' + shot['visual_description'])
                        text += '\n\nОбложка: ' + brief['cover_frame']
                        text += '\n\nПервый рендер использует текстовые карточки. Снятые кадры, голос и музыка в него не входят.'
                    else:
                        text = ('📈 Публикация и измерение\n\nПлощадки: сайт, Дзен, TenChat, vc.ru, Telegram, Shorts, VK Клипы, Reels.'
                                + '\nПосле проверки размести материал вручную и сохрани ссылку. Отмечай просмотры, переходы, обращения, демо/регистрации/записи и клиентов.'
                                + '\nАвтоматические публикации и получение статистики ещё не подключены.')
                await self.bot._send(chat, text)
            elif action == 'video':
                with self.sessions() as db:
                    item = db.get(Artifact, record_id)
                    if item is None or item.kind != 'video_render':
                        raise LookupError('Ролик не найден')
                    value = json.loads(item.content)
                    path = asset_path(self.settings().products_root, item.product_id, UUID(value['script_id']), 'video.mp4')
                await self.bot.api.upload(chat, 'sendVideo', 'video', 'video.mp4', path.read_bytes())
            elif action == 'metrics':
                self.metrics_article = record_id
                await self.bot._send(chat, 'Пришли показатели опубликованного материала. Например:\n{"channel":"dzen","url":"https://...","views":120,"clicks":5,"leads":1}\nНеизвестные показатели не указывай. Это снимок на текущую дату, не прибавление к предыдущим числам.')
            return True
        except (ValueError, LookupError, OSError):
            await self.bot._send(chat, 'Материал недоступен или команда устарела. Открой «Контент» заново.')
            return True
