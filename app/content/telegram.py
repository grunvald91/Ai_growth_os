"""Owner-only content queue, individual material delivery and observations."""
import json
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field
from app.content.render import asset_path
from app.content.presentation import (
    PLATFORM_LABELS, material_markdown, material_platform, payload, plain_text,
)
from app.content.service import queue_content
from app.models.domain import Artifact, Metric, Product, Task

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

    async def menu(self, chat_id):
        self.topic_product = self.metrics_article = self.plan_product = None
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
            text = f'📝 {product.name}\n\nПакет: SEO-статья и адаптации, включая Дзен и Telegram, плюс отдельные production-ready сценарии/раскадровки для YouTube Shorts и Instagram Reels.\nВидео здесь не генерируется и не рендерится. Публикация — после проверки, вручную до подключения площадок.'
            if tasks:
                text += '\n\nПоследние задачи:\n' + '\n'.join(f'• {t.title[:65]} — {statuses.get(t.status.value, t.status.value)}' for t in tasks)
            buttons = [[{'text':'▶️ Создать пакет', 'callback_data':f'content:queue:{product.id}'},
                        {'text':'✍️ Своя тема', 'callback_data':f'content:topic:{product.id}'}]]
            buttons.append([{'text':'📅 Контент-план', 'callback_data':f'content:calendar:{product.id}'}])
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
            self.topic_product = self.metrics_article = self.plan_product = None
            return False
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
        # Old download callbacks also open the current menu, never a ZIP archive.
        with self.sessions() as db:
            article = db.get(Artifact, article_id)
            if article is None or article.kind != 'article':
                raise LookupError('Статья не найдена')
            related = []
            if article.task_id is not None:
                candidates = list(db.scalars(select(Artifact).where(
                    Artifact.task_id == article.task_id,
                    Artifact.product_id == article.product_id,
                    Artifact.kind.in_(['channel_text', 'video_script', 'content_distribution',
                        'dzen_brief', 'telegram_brief', 'short_video_brief', 'story_brief', 'static_image_brief'])
                ).order_by(Artifact.created_at, Artifact.id)))
                for item in candidates:
                    source = payload(item).get('article_id')
                    if source is None or source == str(article.id):
                        related.append(item)
            buttons = [[{'text': '📄 SEO-статья / сайт', 'callback_data': f'content:asset:{article.id}'}]]
            ready_channels = {payload(item).get('channel') for item in related if item.kind == 'channel_text'}
            has_video_scripts = any(item.kind == 'video_script' for item in related)
            video_index = 0
            for item in related:
                platform = material_platform(item)
                label = PLATFORM_LABELS.get(platform, '')
                if item.kind == 'channel_text':
                    label = '📝 ' + (label or item.title)
                elif item.kind in {'video_script', 'short_video_brief'}:
                    if item.kind == 'short_video_brief' and has_video_scripts:
                        continue
                    video_index += 1
                    label = f'🎬 {label or "Видео"} · сценарий {video_index}'
                elif item.kind == 'content_distribution':
                    label = '📈 План публикации и измерения'
                else:
                    if platform in ready_channels and item.kind in {'dzen_brief', 'telegram_brief'}:
                        continue
                    label = {'story_brief': '📱 План Stories', 'static_image_brief': '🖼 План изображения'}.get(
                        item.kind, '📋 ' + (label or item.title))
                buttons.append([{'text': label[:60], 'callback_data': f'content:asset:{item.id}'}])
            for item in db.scalars(select(Artifact).where(
                    Artifact.product_id == article.product_id, Artifact.kind == 'video_render')):
                if payload(item).get('article_id') == str(article.id):
                    buttons.append([{'text': '🎞 ' + item.title[:55], 'callback_data': f'content:video:{item.id}'}])
            buttons.append([{'text': '📈 Записать результаты публикации', 'callback_data': f'content:metrics:{article.id}'}])
            buttons.append([{'text': '↩️ К компании', 'callback_data': f'content:product:{article.product_id}'}])
            title = article.title
        await self.bot._send(chat, f'📦 {title}\n\nВыберите материал. Бот пришлёт его отдельными сообщениями — текст можно сразу скопировать.',
                             reply_markup={'inline_keyboard': buttons})

    async def asset(self, chat, record_id):
        with self.sessions() as db:
            item = db.get(Artifact, record_id)
            if item is None or item.kind not in {'article', 'channel_text', 'video_script', 'content_distribution',
                    'dzen_brief', 'telegram_brief', 'short_video_brief', 'story_brief', 'static_image_brief'}:
                raise LookupError('Материал не найден')
            value = payload(item)
            copy = plain_text(material_markdown(item, value))
            if not copy:
                raise LookupError('Текст материала пока не подготовлен')
            source = value.get('article_id')
            article_id = item.id if item.kind == 'article' else None
            if source:
                try:
                    source_id = UUID(source)
                except (ValueError, TypeError, AttributeError):
                    source_id = None
                source_article = db.get(Artifact, source_id) if source_id else None
                if (source_article is not None and source_article.kind == 'article'
                        and source_article.product_id == item.product_id and source_article.task_id == item.task_id):
                    article_id = source_article.id
            buttons = [[{'text': '↩️ Материалы пакета', 'callback_data': f'content:article:{article_id}'}]] if article_id else [
                [{'text': '↩️ К компании', 'callback_data': f'content:product:{item.product_id}'}]]
        # _send splits long copy at Telegram's UTF-16 limit without truncating it.
        await self.bot._send(chat, copy, reply_markup={'inline_keyboard': buttons})

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
            text = '📅 Контент-план: ' + product.name + '\nРедактор выбирает площадки, частоту и форматы, назначает подготовку заранее. Расписание запускается после подтверждения. Публикация пока вручную.'
            if tasks:
                text += '\nПоследняя работа редактора: ' + {'queued':'в очереди', 'running':'готовит план', 'completed':'план готов', 'failed':'ошибка; можно повторить'}.get(tasks[0].status.value, tasks[0].status.value)
            if plans:
                latest = json.loads(plans[0].content)
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
            record_id = UUID(raw)
            if action == 'calendar':
                await self.calendar(chat, record_id)
            elif action == 'plan':
                from app.content.calendar import queue_plan
                with self.sessions() as db:
                    queue_plan(db, record_id)
                await self.bot._send(chat, '✅ Редактору поручен план. Он учтёт профиль, доступные исследования и записанные результаты. Открой календарь и нажми «Обновить».')
            elif action == 'preferences':
                self.topic_product = self.metrics_article = None
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
                    self.topic_product = record_id
                    await self.bot._send(chat, 'Напиши тему статьи одним сообщением, до 300 символов.')
                else:
                    with self.sessions() as db:
                        queue_content(db, record_id)
                    await self.bot._send(chat, '✅ Пакет контента поставлен в очередь. Результат появится в этом разделе.')
                    await self.product(chat, record_id)
            elif action in {'article','download'}:
                await self.article(chat, record_id, download=action == 'download')
            elif action == 'asset':
                await self.asset(chat, record_id)
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
        except (ValueError, LookupError, OSError) as exc:
            await self.bot._send(chat, str(exc) if action in {'planapprove','planpause','preferences'} and isinstance(exc, (ValueError, LookupError)) else 'Материал недоступен. Открой «Контент» заново.')
            return True
