"""Owner-only content queue, review, downloads and manual performance observations."""
import io
import json
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import UUID
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field
from app.content.render import asset_path
from app.content.service import queue_content
from app.content.revisions import queue_post_edit, post_history
from app.content.calendar import PLATFORMS
from app.content.creative import CreativeRequest, queue_creative
from app.content.telegram_publishing import (verify_channel, save_verified_channel,
    connected_channels, schedule_post, cancel_scheduled_post)
from app.models.domain import Artifact, ArtifactStatus, Metric, Product, Publication, Task, TaskStatus

LABELS = {'own_site':'SEO / сайт', 'dzen':'Дзен', 'tenchat':'TenChat', 'vc_ru':'vc.ru',
          'telegram':'Telegram', 'vk':'ВКонтакте', 'faq':'FAQ', 'founder':'Пост автора'}

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
        self.plan_product = None
        self.post_target = None  # (product_id, channel) for a single post
        self.post_edit_target = None  # immutable Artifact ID under revision
        self.calendar_target = None  # (plan_id, slot_id)
        self.channel_connect_product = None
        self.schedule_post_id = None
        self.schedule_target = None  # (post_id, connection_id)
        self.creative_target = None  # (company_id, format, platform)
        self.calendar_publish_slot = None  # (plan_id, slot_id)

    async def menu(self, chat_id):
        self.topic_product = self.metrics_article = self.plan_product = self.post_target = self.post_edit_target = self.calendar_target = self.channel_connect_product = self.schedule_post_id = self.schedule_target = self.creative_target = self.calendar_publish_slot = None
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
            text = (f'📝 {product.name}\n\n'
                    'Один пост — быстрая подготовка текста для выбранной площадки. '
                    'Полный пакет — SEO-статья, адаптации и видеосценарии. '
                    'Проверка и утверждение обязательны. '
                    'Видео автоматически не создаётся; публикация отдельно, вручную до подключения площадок.')
            if tasks:
                text += '\n\nПоследние задачи:\n' + '\n'.join(f'• {t.title[:65]} — {statuses.get(t.status.value, t.status.value)}' for t in tasks)
            buttons = [[{'text':'✍️ Один пост', 'callback_data':f'content:postchannels:{product.id}'},
                        {'text':'📦 Полный пакет', 'callback_data':f'content:queue:{product.id}'}],
                       [{'text':'✏️ Тема полного пакета', 'callback_data':f'content:topic:{product.id}'}]]
            buttons.append([{'text':'🎬 Ролики, Stories, изображения', 'callback_data':f'content:creativeformats:{product.id}'}])
            buttons.append([{'text':'📅 Контент-план', 'callback_data':f'content:calendar:{product.id}'}])
            buttons.append([{'text':'📣 Telegram-каналы и публикации', 'callback_data':f'content:channels:{product.id}'}])
            recent_posts = list(db.scalars(select(Artifact).where(
                Artifact.product_id == product.id, Artifact.kind == 'channel_text'
            ).order_by(Artifact.created_at.desc()).limit(10)))
            for post in recent_posts:
                try:
                    meta = json.loads(post.content)
                except (ValueError, TypeError):
                    continue
                if meta.get('origin') in {'quick_post', 'owner_revision'}:
                    buttons.append([{'text':'📝 ' + post.title[:50], 'callback_data':f'content:asset:{post.id}'}])
            recent_creatives = list(db.scalars(select(Artifact).where(
                Artifact.product_id == product.id,
                Artifact.kind == 'creative_brief',
            ).order_by(Artifact.created_at.desc()).limit(8)))
            buttons += [[{'text':'🎬 ' + item.title[:47], 'callback_data':f'content:asset:{item.id}'}]
                        for item in recent_creatives]
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
            self.topic_product = self.metrics_article = self.plan_product = self.post_target = self.post_edit_target = self.calendar_target = self.channel_connect_product = self.schedule_post_id = self.schedule_target = self.creative_target = self.calendar_publish_slot = None
            return False
        if self.creative_target:
            product_id, creative_format, platform = self.creative_target
            try:
                request = CreativeRequest(
                    mode='creative', product_id=product_id,
                    format=creative_format, platform=platform, topic=text,
                )
                with self.sessions() as db:
                    queue_creative(db, request)
                self.creative_target = None
                await self.bot._send(chat,
                    '✅ Бриф поставлен в очередь. ИИ подготовит сценарий или визуальную '
                    'раскадровку на проверку. Видео, фотография и публикация пока не создаются.')
            except ValueError:
                await self.bot._send(chat, 'Напиши тему от 3 до 300 символов.')
            return True
        if self.channel_connect_product:
            try:
                checked = await verify_channel(self.bot.api, text)
                with self.sessions() as db:
                    connection = save_verified_channel(db, self.channel_connect_product, checked)
                self.channel_connect_product = None
                await self.bot._send(chat,
                    '✅ Канал подключён: ' + connection.title +
                    '. Бот проверен как администратор с правом отправки. '
                    'Публикация возможна только после утверждения текста и назначения даты.')
            except (ValueError, LookupError) as exc:
                await self.bot._send(chat, str(exc))
            except Exception:
                await self.bot._send(chat,
                    'Не удалось проверить права бота через Telegram. '
                    'Добавь бота администратором с правом публикации и попробуй ещё раз.')
            return True
        if self.schedule_target:
            post_id, channel_id = self.schedule_target
            try:
                when = datetime.strptime(text, '%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Moscow'))
                with self.sessions() as db:
                    publication = schedule_post(db, post_id, channel_id, when)
                self.schedule_target = self.schedule_post_id = None
                enabled = self.settings().content_telegram_autopost_enabled
                await self.bot._send(chat,
                    '✅ Публикация запланирована на ' + when.strftime('%d.%m.%Y %H:%M') +
                    ' (Москва). ' +
                    ('Автоотправка активна.' if enabled else
                     'Автоотправка пока выключена на сервере; материал не будет отправлен до включения.'))
            except (ValueError, LookupError) as exc:
                await self.bot._send(chat, str(exc))
            return True
        if self.calendar_target:
            plan_id, slot_id = self.calendar_target
            try:
                when = datetime.strptime(text, '%d.%m.%Y %H:%M')
                from app.content.calendar import plan_record, reschedule_slot
                with self.sessions() as db:
                    _, plan = plan_record(db, plan_id)
                    when = when.replace(tzinfo=ZoneInfo(plan['timezone']))
                    result = reschedule_slot(db, plan_id, slot_id, when)
                self.calendar_target = None
                await self.bot._send(
                    chat, '✅ Время изменено: ' +
                    datetime.fromisoformat(result['publish_at']).strftime('%d.%m.%Y %H:%M') +
                    '. Тема и материалы сохранены; новая публикация не отправлялась.'
                )
            except (ValueError, LookupError) as exc:
                await self.bot._send(chat, str(exc))
            return True
        if self.post_edit_target:
            try:
                with self.sessions() as db:
                    queue_post_edit(db, self.post_edit_target, text)
                self.post_edit_target = None
                await self.bot._send(
                    chat, '✅ Замечание принято. ИИ-редактор готовит новую версию. '
                          'Исходный текст сохранён, а новая версия появится на проверку.'
                )
            except (ValueError, LookupError) as exc:
                await self.bot._send(chat, str(exc))
            return True
        if self.plan_product:
            try:
                from app.content.calendar import save_preferences, queue_plan
                with self.sessions() as db:
                    save_preferences(db, self.plan_product, text)
                    queue_plan(db, self.plan_product)
                self.plan_product = None
                await self.bot._send(chat, '✅ Требования сохранены. Редактор готовит новый контент-план для подтверждения.')
            except (ValueError, LookupError) as exc:
                await self.bot._send(chat, str(exc))
            return True
        if self.post_target:
            product_id, channel = self.post_target
            try:
                with self.sessions() as db:
                    queue_content(db, product_id, text, format='post', channel=channel)
                self.post_target = None
                await self.bot._send(chat, '✅ Один пост поставлен в очередь. Материал появится в разделе компании для проверки. Публикация не запускается.')
            except ValueError:
                await self.bot._send(chat, 'Напиши тему одним сообщением, от 3 до 300 символов.')
            return True
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
                    from app.content.calendar import record_publication
                    record_publication(db, article, observation.channel, observation.url)
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
            for item in renders:
                buttons.append([{'text':'🎞 ' + item.title[:55], 'callback_data':f'content:video:{item.id}'}])
            buttons.append([{'text':'📈 Записать результаты публикации', 'callback_data':f'content:metrics:{article.id}'}])
        await self.bot._send(chat, data['full_article'], reply_markup={'inline_keyboard':buttons})

    async def calendar(self, chat, product_id):
        from app.content.calendar import render_calendar
        with self.sessions() as db:
            product = db.get(Product, product_id)
            if product is None:
                raise LookupError('Компания не найдена')
            plans = list(db.scalars(select(Artifact).where(Artifact.product_id == product_id,
                Artifact.kind == 'editorial_plan').order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(5)))
            plans.sort(key=lambda p: json.loads(p.content)['generated_at'], reverse=True)
            tasks = list(db.scalars(select(Task).where(Task.product_id == product_id,
                Task.assigned_agent == 'editorial_planner').order_by(Task.created_at.desc()).limit(1)))
            buttons = [[{'text':'🗓 Составить / пересмотреть', 'callback_data':f'content:plan:{product_id}'}],
                       [{'text':'✏️ Мои площадки и ресурсы', 'callback_data':f'content:preferences:{product_id}'}]]
            text = ('📅 Контент-план: ' + product.name +
                    '\nРедактор выбирает площадки и назначает подготовку заранее. '
                    'После подтверждения запускается подготовка, не публикация. '
                    'Telegram можно отправлять по согласованному расписанию только в подключённые '
                    'каналы и после утверждения текста.')
            if tasks:
                text += '\nПоследняя работа редактора: ' + {'queued':'в очереди', 'running':'готовит план', 'completed':'план готов', 'failed':'ошибка; можно повторить'}.get(tasks[0].status.value, tasks[0].status.value)
            if plans:
                current = next((row for row in plans
                                if json.loads(row.content).get('approval_state') == 'active'), plans[0])
                latest = json.loads(current.content)
                text += '\n\n' + render_calendar(latest)
                for item in plans:
                    value = json.loads(item.content)
                    if value['approval_state'] == 'draft':
                        buttons.append([{'text':'👀 Проверить новый план', 'callback_data':f'content:planview:{item.id}'}])
                    elif value['approval_state'] == 'active':
                        buttons.append([{'text':'📅 Действующий календарь', 'callback_data':f'content:planview:{item.id}'},
                                        {'text':'⏸ Пауза', 'callback_data':f'content:planpause:{item.id}'}])
                task_ids = {slot['task_id'] for item in plans for slot in json.loads(item.content)['slots'] if slot['task_id']}
                if task_ids:
                    for article in db.scalars(select(Artifact).where(Artifact.kind == 'article',
                        Artifact.product_id == product_id, Artifact.task_id.in_([UUID(i) for i in task_ids]))):
                        buttons.append([{'text':'📦 ' + article.title[:50], 'callback_data':f'content:article:{article.id}'}])
            buttons.append([{'text':'🔄 Обновить календарь', 'callback_data':f'content:calendar:{product_id}'}])
        await self.bot._send(chat, text, reply_markup={'inline_keyboard':buttons})

    async def callback(self, chat, data):
        try:
            _, action, raw = data.split(':',2)
            if action in {'slotmove', 'planslot'}:
                raw_plan_id, slot_id = raw.split(':', 1)
                record_id = UUID(raw_plan_id)
            else:
                record_id = UUID(raw)
            # Leaving the quick-post wizard must not steal a later unrelated
            # message (company profile, research parameters, statistics, etc.).
            if action not in {'postchannels', 'posttg', 'postvk', 'postdzen', 'postvc', 'postten'}:
                self.post_target = None
            if action != 'editpost':
                self.post_edit_target = None
            if action != 'slotmove':
                self.calendar_target = None
            if action != 'connecttg':
                self.channel_connect_product = None
            if action not in {'schedulechannels', 'schedtg', 'planpost', 'planslot'}:
                self.schedule_post_id = None
            if action not in {'planslot', 'schedtg'}:
                self.calendar_publish_slot = None
            if action != 'schedtg':
                self.schedule_target = None
            if action not in {'creativeformats', 'creativevideo', 'creativestory', 'creativeimage',
                              'cvyt', 'cvreels', 'cvvk', 'cstg', 'csreels', 'csvk',
                              'citg', 'civk', 'cidzen', 'cireels'}:
                self.creative_target = None
            if action == 'creativeformats':
                with self.sessions() as db:
                    if db.get(Product, record_id) is None:
                        raise LookupError('Компания не найдена')
                buttons = [[{
                    'text':label, 'callback_data':f'content:{key}:{record_id}',
                }] for label,key in [
                    ('🎥 Короткое видео (только сценарий)', 'creativevideo'),
                    ('📱 Stories (раскадровка)', 'creativestory'),
                    ('🖼 Картинка (дизайн-бриф)', 'creativeimage'),
                ]]
                await self.bot._send(chat,
                    'Какой материал создать? Готовим отдельный бриф для одной площадки '
                    'без запуска большого пакета и без рендера медиа.',
                    reply_markup={'inline_keyboard':buttons})
            elif action in {'creativevideo', 'creativestory', 'creativeimage'}:
                choices = {
                    'creativevideo': [('YouTube Shorts','cvyt'), ('Instagram Reels','cvreels'),
                                      ('VK Клипы','cvvk')],
                    'creativestory': [('Telegram','cstg'), ('Instagram Stories','csreels'),
                                      ('VK Истории','csvk')],
                    'creativeimage': [('Telegram','citg'), ('ВКонтакте','civk'),
                                      ('Дзен','cidzen'), ('Instagram','cireels')],
                }[action]
                await self.bot._send(chat, 'Выбери площадку:',
                    reply_markup={'inline_keyboard': [[{
                        'text':label, 'callback_data':f'content:{key}:{record_id}'
                    }] for label,key in choices]})
            elif action in {'cvyt','cvreels','cvvk','cstg','csreels','csvk',
                            'citg','civk','cidzen','cireels'}:
                mapping = {
                    'cvyt': ('short_video','youtube_shorts'),
                    'cvreels': ('short_video','instagram_reels'),
                    'cvvk': ('short_video','vk_clips'),
                    'cstg': ('story','telegram'), 'csreels': ('story','instagram_reels'),
                    'csvk': ('story','vk'),
                    'citg': ('static_image','telegram'), 'civk': ('static_image','vk'),
                    'cidzen': ('static_image','dzen'), 'cireels': ('static_image','instagram_reels'),
                }
                with self.sessions() as db:
                    if db.get(Product, record_id) is None:
                        raise LookupError('Компания не найдена')
                fmt, platform = mapping[action]
                self.topic_product = self.post_target = self.post_edit_target = None
                self.plan_product = self.metrics_article = self.channel_connect_product = None
                self.creative_target = (record_id, fmt, platform)
                await self.bot._send(chat, 'Напиши тему для сценария/брифа одним сообщением.')
            elif action == 'channels':
                with self.sessions() as db:
                    product = db.get(Product, record_id)
                    if product is None:
                        raise LookupError('Компания не найдена')
                    channels = connected_channels(db, record_id)
                    publications = list(db.scalars(select(Publication).where(
                        Publication.product_id == record_id,
                        Publication.channel == 'telegram',
                    ).order_by(Publication.created_at.desc()).limit(12)))
                    actions = [[{
                        'text':'➕ Подключить Telegram-канал',
                        'callback_data':f'content:connecttg:{record_id}',
                    }]]
                    for pub in publications:
                        if pub.status in {'scheduled', 'missed', 'failed_before_send'}:
                            actions.append([{
                                'text':'✖ Снять с расписания ' + str(pub.id)[:8],
                                'callback_data':f'content:canceltg:{pub.id}',
                            }])
                    summary = '\n'.join('• ' + ch.title for ch in channels) or 'Каналы ещё не подключены.'
                    summary += '\n\nПоследние публикации:\n'
                    summary += '\n'.join(
                        '• ' + pub.status + ' · ' + str(pub.artifact_id)[:8]
                        for pub in publications
                    ) or 'Нет запланированных публикаций.'
                await self.bot._send(chat,
                    '📣 Telegram-каналы компании: ' + product.name + '\n\n' + summary +
                    '\n\nДля подключения добавь бота администратором канала с правом публикации. '
                    'Пароль и токены вручную вводить не нужно.',
                    reply_markup={'inline_keyboard':actions})
            elif action == 'connecttg':
                with self.sessions() as db:
                    if db.get(Product, record_id) is None:
                        raise LookupError('Компания не найдена')
                self.topic_product = self.metrics_article = self.plan_product = None
                self.channel_connect_product = record_id
                await self.bot._send(chat,
                    'Добавь управляющего бота администратором канала с правом публикации. '
                    'Затем пришли @username канала или его ID (начинается с -100). '
                    'Я проверю права через официальный Telegram API.')
            elif action == 'planpost':
                with self.sessions() as db:
                    post = db.get(Artifact, record_id)
                    if post is None or post.kind != 'channel_text' or post.status != ArtifactStatus.APPROVED:
                        raise LookupError('Сначала утверди Telegram-пост')
                    if json.loads(post.content).get('channel') != 'telegram':
                        raise ValueError('Это не Telegram-пост')
                    plans = list(db.scalars(select(Artifact).where(
                        Artifact.kind == 'editorial_plan',
                        Artifact.product_id == post.product_id,
                    ).order_by(Artifact.created_at.desc()).limit(30)))
                    matches = []
                    for row in plans:
                        plan = json.loads(row.content)
                        if plan.get('approval_state') == 'active':
                            for slot in plan.get('slots', []):
                                if (slot.get('platform') == 'telegram' and
                                        slot.get('task_id') == str(post.task_id) and
                                        not slot.get('telegram_publication_id')):
                                    matches.append((row, slot))
                self.schedule_post_id = record_id
                buttons = [[{
                    'text': '🕒 ' + slot['publish_at'][5:16].replace('T',' ') +
                            ' · ' + slot['topic'][:22],
                    'callback_data': f'content:planslot:{row.id}:{slot["slot_id"]}',
                }] for row,slot in matches[:10]]
                await self.bot._send(chat,
                    'Выбери Telegram-слот действующего контент-плана. '
                    'Если список пуст, сначала добавь пост в подтверждённый план.',
                    reply_markup={'inline_keyboard':buttons})
            elif action == 'planslot':
                if self.schedule_post_id is None:
                    raise ValueError('Сначала открой утверждённый Telegram-пост')
                from app.content.calendar import plan_record
                with self.sessions() as db:
                    post = db.get(Artifact, self.schedule_post_id)
                    row, plan = plan_record(db, record_id)
                    slot = next((v for v in plan.get('slots', [])
                                 if str(v.get('slot_id')) == str(slot_id)), None)
                    if (post is None or row.product_id != post.product_id or
                            plan.get('approval_state') != 'active' or slot is None or
                            slot.get('platform') != 'telegram' or
                            slot.get('task_id') != str(post.task_id)):
                        raise ValueError('Слот не принадлежит этому утверждённому посту')
                    connections = connected_channels(db, post.product_id)
                self.calendar_publish_slot = (record_id, slot_id)
                buttons = [[{
                    'text': '📣 ' + connection.title[:42],
                    'callback_data': f'content:schedtg:{connection.id}',
                }] for connection in connections]
                await self.bot._send(chat,
                    'Выбери Telegram-канал для публикации в указанный день. '
                    'Дата берётся непосредственно из календаря.',
                    reply_markup={'inline_keyboard':buttons})
            elif action == 'schedulechannels':
                with self.sessions() as db:
                    post = db.get(Artifact, record_id)
                    if post is None or post.kind != 'channel_text' or post.status != ArtifactStatus.APPROVED:
                        raise LookupError('Сначала утверди пост')
                    if json.loads(post.content).get('channel') != 'telegram':
                        raise ValueError('Автопостинг доступен только для Telegram-постов')
                    connections = connected_channels(db, post.product_id)
                self.schedule_post_id = record_id
                actions = [[{
                    'text':'📣 ' + connection.title[:42],
                    'callback_data':f'content:schedtg:{connection.id}',
                }] for connection in connections]
                await self.bot._send(chat,
                    'Выбери канал для утверждённого поста. '
                    'Если каналов нет, сначала открой раздел Telegram-каналов.',
                    reply_markup={'inline_keyboard':actions})
            elif action == 'schedtg':
                if self.schedule_post_id is None:
                    raise ValueError('Сначала открой утверждённый пост и выбери публикацию')
                with self.sessions() as db:
                    post = db.get(Artifact, self.schedule_post_id)
                    connection = db.get(Artifact, record_id)
                    if (post is None or connection is None or post.product_id != connection.product_id
                            or connection.kind != 'telegram_channel_connection'
                            or connection.status != ArtifactStatus.APPROVED):
                        raise ValueError('Канал не принадлежит компании этого поста')
                if self.calendar_publish_slot:
                    from app.content.calendar import plan_record
                    plan_id, slot_key = self.calendar_publish_slot
                    with self.sessions() as db:
                        _, plan = plan_record(db, plan_id)
                        slot = next(v for v in plan['slots']
                                    if str(v['slot_id']) == str(slot_key))
                        when = datetime.fromisoformat(slot['publish_at'])
                        publication = schedule_post(
                            db, self.schedule_post_id, record_id, when,
                            plan_id=plan_id, slot_id=slot_key,
                        )
                    self.schedule_post_id = self.calendar_publish_slot = self.schedule_target = None
                    await self.bot._send(chat,
                        '✅ Публикация привязана к календарю: ' +
                        when.strftime('%d.%m.%Y %H:%M') + '. ' +
                        ('Автоотправка включена.' if self.settings().content_telegram_autopost_enabled
                         else 'Автоотправка выключена на сервере.'))
                else:
                    self.schedule_target = (self.schedule_post_id, record_id)
                    await self.bot._send(chat,
                        'Пришли дату и время публикации по Москве: ДД.ММ.ГГГГ ЧЧ:ММ. '
                        'Например: 15.10.2026 12:30. Перед отправкой пост должен оставаться утверждённым.')
            elif action == 'canceltg':
                with self.sessions() as db:
                    cancel_scheduled_post(db, record_id)
                await self.bot._send(chat, '✅ Публикация отменена до отправки.')
            elif action == 'slotmove':
                from app.content.calendar import plan_record
                with self.sessions() as db:
                    row, plan = plan_record(db, record_id)
                    slot = next((x for x in plan['slots']
                                 if str(x.get('slot_id')) == str(slot_id)), None)
                    if not slot:
                        raise LookupError('Слот не найден')
                    if plan.get('approval_state') not in {'active', 'draft'}:
                        raise ValueError('План недоступен для переноса')
                    if slot.get('publication_url'):
                        raise ValueError('Опубликованный материал нельзя переносить')
                self.topic_product = self.metrics_article = self.plan_product = None
                self.post_target = self.post_edit_target = None
                self.calendar_target = (record_id, slot_id)
                await self.bot._send(chat,
                    'Пришли новое время в формате ДД.ММ.ГГГГ ЧЧ:ММ.\n'
                    f'Площадка: {PLATFORMS[slot["platform"]][0]}. '
                    'Дата должна оставаться в пределах текущего плана. '
                    'Перенос не публикует материал автоматически.')
            elif action == 'postchannels':
                self.topic_product = self.metrics_article = self.plan_product = self.post_target = None
                with self.sessions() as db:
                    if db.get(Product, record_id) is None:
                        raise LookupError('Компания не найдена')
                choices = [
                    ('Telegram', 'posttg'), ('ВКонтакте', 'postvk'),
                    ('Дзен', 'postdzen'), ('vc.ru', 'postvc'), ('TenChat', 'postten'),
                ]
                buttons = [[{'text':label, 'callback_data':f'content:{action_name}:{record_id}'}]
                           for label, action_name in choices]
                await self.bot._send(chat, 'Выбери площадку для одного поста. ИИ создаст текст для проверки, без публикации.',
                                     reply_markup={'inline_keyboard':buttons})
            elif action in {'posttg', 'postvk', 'postdzen', 'postvc', 'postten'}:
                channel = {
                    'posttg': 'telegram', 'postvk': 'vk', 'postdzen': 'dzen',
                    'postvc': 'vc_ru', 'postten': 'tenchat',
                }[action]
                with self.sessions() as db:
                    if db.get(Product, record_id) is None:
                        raise LookupError('Компания не найдена')
                self.post_target = (record_id, channel)
                self.topic_product = self.metrics_article = self.plan_product = None
                await self.bot._send(chat, 'Напиши тему поста одним сообщением. Пример: «3 ошибки при выборе подарочного букета».')
            elif action == 'calendar':
                await self.calendar(chat, record_id)
            elif action == 'plan':
                from app.content.calendar import queue_plan
                with self.sessions() as db:
                    queue_plan(db, record_id)
                await self.bot._send(chat, '✅ Редактору поручен план. Он учтёт профиль, доступные исследования и записанные результаты. Открой календарь и нажми «Обновить».')
            elif action == 'preferences':
                self.topic_product = self.metrics_article = self.post_target = None
                self.plan_product = record_id
                await self.bot._send(chat, 'Напиши своими словами: какие аккаунты уже есть, кого привлекаем, сколько времени есть на проверку, какие форматы доступны, на какой период нужен план. Можно указать желаемую частоту, даты и бюджет. Например: «Есть Telegram и Дзен; Shorts хочу протестировать. На проверку — два часа в неделю. План на 14 дней». Неизвестное можно так и назвать.')
            elif action == 'planview':
                from app.content.calendar import plan_record, render_calendar
                with self.sessions() as db:
                    row, value = plan_record(db, record_id)
                    buttons = [[{'text':'↩️ Календарь', 'callback_data':f'content:calendar:{row.product_id}'}]]
                    if value['approval_state'] == 'draft':
                        buttons.insert(0, [{'text':'✅ Подтвердить и готовить', 'callback_data':f'content:planapprove:{record_id}'}])
                    if value['approval_state'] == 'active':
                        buttons.insert(0, [{'text':'⏸ Пауза', 'callback_data':f'content:planpause:{record_id}'}])
                    if value['approval_state'] in {'active', 'draft'}:
                        for slot in sorted(value['slots'], key=lambda x: x['publish_at'])[:15]:
                            buttons.append([{
                                'text': '🕒 ' + slot['publish_at'][5:16].replace('T', ' ') + ' · ' +
                                        PLATFORMS[slot['platform']][0][:16],
                                'callback_data': f'content:slotmove:{record_id}:{slot["slot_id"]}',
                            }])
                    body = render_calendar(value)
                await self.bot._send(chat, body, reply_markup={'inline_keyboard':buttons})
            elif action == 'planapprove':
                from app.content.calendar import approve_plan
                with self.sessions() as db:
                    approve_plan(db, self.settings(), record_id)
                await self.bot._send(chat, '✅ Расписание подтверждено. Подготовка запускается к указанным срокам. Готовые тексты и видеобрифы — на проверку; публикация остаётся отдельным ручным действием.')
            elif action == 'planpause':
                from app.content.calendar import pause_plan
                with self.sessions() as db:
                    pause_plan(db, record_id)
                await self.bot._send(chat, '⏸ Будущая подготовка остановлена. Уже поставленные задачи продолжаются. Для нового расписания составь и подтверди новый план.')
            elif action == 'product':
                await self.product(chat, record_id)
            elif action in {'queue','topic'}:
                if action == 'topic':
                    self.post_target = None
                    self.topic_product = record_id
                    await self.bot._send(chat, 'Напиши тему статьи одним сообщением, до 300 символов.')
                else:
                    with self.sessions() as db:
                        queue_content(db, record_id)
                    await self.bot._send(chat, '✅ Пакет контента поставлен в очередь. Результат появится в этом разделе.')
                    await self.product(chat, record_id)
            elif action in {'article','download'}:
                await self.article(chat, record_id, download=action == 'download')
            elif action == 'editpost':
                with self.sessions() as db:
                    item = db.get(Artifact, record_id)
                    if (item is None or item.kind != 'channel_text' or
                            item.status not in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}):
                        raise LookupError('Пост недоступен для доработки')
                    current = json.loads(item.content)
                    if not current.get('body'):
                        raise LookupError('Текст поста отсутствует')
                self.topic_product = self.metrics_article = self.plan_product = self.post_target = None
                self.post_edit_target = record_id
                await self.bot._send(
                    chat,
                    'Напиши, что именно изменить в посте. Например: '
                    '«Убери общие фразы, добавь конкретный пример и сократи вступление». '
                    'ИИ сохранит старую версию, а новую отправит на проверку.'
                )
            elif action == 'history':
                with self.sessions() as db:
                    versions = post_history(db, record_id)
                    buttons = []
                    for pos, version in enumerate(versions, 1):
                        label = f'Версия {pos} · {version.title[:36]} · {version.status.value}'
                        buttons.append([{
                            'text': label,
                            'callback_data': f'content:asset:{version.id}',
                        }])
                await self.bot._send(
                    chat,
                    f'📚 История текста — {len(versions)} верс.\n'
                    'Старые варианты доступны для просмотра, но не редактируются поверх оригинала.',
                    reply_markup={'inline_keyboard': buttons},
                )
            elif action == 'approvebrief':
                with self.sessions() as db:
                    brief = db.scalar(select(Artifact).where(
                        Artifact.id == record_id, Artifact.kind == 'creative_brief',
                    ).with_for_update())
                    if brief is None:
                        raise LookupError('Бриф не найден')
                    if brief.status == ArtifactStatus.AWAITING_APPROVAL:
                        brief.status = ArtifactStatus.APPROVED
                        brief.version += 1
                        db.commit()
                        result = '✅ Бриф согласован. Медиафайл и публикация не создавались.'
                    else:
                        result = 'Бриф уже проверен или недоступен для согласования.'
                await self.bot._send(chat, result)
            elif action == 'approvepost':
                with self.sessions() as db:
                    item = db.scalar(select(Artifact).where(
                        Artifact.id == record_id, Artifact.kind == 'channel_text'
                    ).with_for_update())
                    if item is None or not json.loads(item.content).get('body'):
                        raise LookupError('Пост недоступен')
                    if item.status == ArtifactStatus.AWAITING_APPROVAL:
                        pending = db.scalar(select(Task).where(
                            Task.product_id == item.product_id,
                            Task.assigned_agent == 'content_editor',
                            Task.title == 'EDIT: ' + str(item.id),
                            Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                        ))
                        if pending is not None:
                            response = 'Пост сейчас дорабатывается. Проверь новую версию перед утверждением.'
                        else:
                            item.status = ArtifactStatus.APPROVED
                            item.version += 1
                            db.commit()
                            response = '✅ Текст утверждён. Публикация пока только вручную.'
                    elif item.status == ArtifactStatus.APPROVED:
                        response = 'Этот текст уже утверждён. Публикация не запускалась.'
                    else:
                        response = 'Пост нельзя утвердить из текущего состояния.'
                await self.bot._send(chat, response)
            elif action == 'asset':
                reply_markup = None
                with self.sessions() as db:
                    item = db.get(Artifact, record_id)
                    if item is None or item.kind not in {'channel_text','video_script','content_distribution','creative_brief'}:
                        raise LookupError('Материал не найден')
                    value = json.loads(item.content)
                    if item.kind == 'channel_text':
                        status_label = {
                            ArtifactStatus.AWAITING_APPROVAL: 'Ожидает проверки',
                            ArtifactStatus.APPROVED: 'Утверждено',
                        }.get(item.status, item.status.value)
                        text = '# ' + value['title'] + '\n\n' + value['body'] + '\n\nСтатус: ' + status_label
                        warnings = value.get('quality_warnings') or []
                        if warnings:
                            text += '\n\nПеред публикацией проверь:\n• ' + '\n• '.join(warnings[:6])
                        if item.status in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}:
                            buttons = []
                            if (value.get('body') and item.status == ArtifactStatus.AWAITING_APPROVAL):
                                buttons.append([{
                                    'text':'✅ Утвердить текст',
                                    'callback_data':f'content:approvepost:{item.id}',
                                }])
                            buttons.append([{
                                'text':'✏️ Попросить доработку',
                                'callback_data':f'content:editpost:{item.id}',
                            }])
                            reply_markup = {'inline_keyboard': buttons}
                        if (value.get('channel') == 'telegram' and
                                item.status == ArtifactStatus.APPROVED):
                            if reply_markup is None:
                                reply_markup = {'inline_keyboard': []}
                            reply_markup['inline_keyboard'].append([{
                                'text':'📣 Запланировать в Telegram',
                                'callback_data':f'content:schedulechannels:{item.id}',
                            }])
                            reply_markup['inline_keyboard'].append([{
                                'text':'📅 По контент-плану',
                                'callback_data':f'content:planpost:{item.id}',
                            }])
                        if value.get('origin') in {'quick_post', 'owner_revision'}:
                            if reply_markup is None:
                                reply_markup = {'inline_keyboard': []}
                            reply_markup['inline_keyboard'].append([{
                                'text': '📚 История версий',
                                'callback_data': f'content:history:{item.id}',
                            }])
                    elif item.kind == 'creative_brief':
                        text = value.get('preview_text') or 'Сценарий ещё не подготовлен'
                        text += '\n\nСтатус: ' + item.status.value
                        if item.status == ArtifactStatus.AWAITING_APPROVAL:
                            reply_markup = {'inline_keyboard': [[{
                                'text':'✅ Утвердить бриф',
                                'callback_data':f'content:approvebrief:{item.id}',
                            }]]}
                    elif item.kind == 'video_script':
                        brief = value['production_brief']
                        text = ('🎬 ' + item.title
                                + '\nПлощадка: ' + brief.get('target_platform', 'short_video')
                                + '\n\nЗадача: ' + brief['objective']
                                + '\n\nХуки:\n• ' + '\n• '.join(brief.get('hook_variants', []))
                                + '\n\nТекст для озвучки/диалога:\n' + brief['voiceover_or_dialogue_script']
                                + '\n\nСцены:')
                        for shot in brief['shots']:
                            text += (f"\n\n{shot['start_second']}–{shot['end_second']} сек."
                                     + '\nНа экране: ' + (shot.get('on_screen_text') or '')
                                     + '\nКадр: ' + shot['visual_description']
                                     + '\nКамера/движение: ' + shot['camera_and_movement']
                                     + '\nПереход: ' + shot['transition']
                                     + '\nПромпт для будущей генерации: ' + shot.get('generation_prompt', ''))
                        text += '\n\nМонтаж/сведение: ' + brief.get('editing_mix_notes', brief.get('editing_rhythm', ''))
                        text += '\n\nОбложка: ' + brief['cover_frame']
                        text += '\n\nЭто только production-brief. AI Growth OS не генерирует и не рендерит видео.'
                    else:
                        text = ('📈 Публикация и измерение\n\nПлощадки: сайт, Дзен, TenChat, vc.ru, Telegram, Shorts, VK Клипы, Reels.'
                                + '\nПосле проверки размести материал вручную и сохрани ссылку. Отмечай просмотры, переходы, обращения, демо/регистрации/записи и клиентов.'
                                + '\nАвтоматические публикации и получение статистики ещё не подключены.')
                await self.bot._send(chat, text, reply_markup=reply_markup)
            elif action == 'video':
                with self.sessions() as db:
                    item = db.get(Artifact, record_id)
                    if item is None or item.kind != 'video_render':
                        raise LookupError('Ролик не найден')
                    value = json.loads(item.content)
                    path = asset_path(self.settings().products_root, item.product_id, UUID(value['script_id']), 'video.mp4')
                await self.bot.api.upload(chat, 'sendVideo', 'video', 'video.mp4', path.read_bytes())
            elif action == 'metrics':
                self.post_target = None
                self.metrics_article = record_id
                await self.bot._send(chat, 'Пришли показатели опубликованного материала. Например:\n{"channel":"dzen","url":"https://...","views":120,"clicks":5,"leads":1}\nНеизвестные показатели не указывай. Это снимок на текущую дату, не прибавление к предыдущим числам.')
            return True
        except (ValueError, LookupError, OSError) as exc:
            await self.bot._send(chat, str(exc) if action in {'planapprove','planpause','preferences'} and isinstance(exc, (ValueError, LookupError)) else 'Материал недоступен. Открой «Контент» заново.')
            return True
