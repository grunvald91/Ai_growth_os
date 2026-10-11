"""Owner-only content queue, review, downloads and manual performance observations."""
import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field
from app.content.render import asset_path
from app.content.service import queue_content
from app.content.revisions import queue_post_edit, post_history
from app.models.domain import Artifact, ArtifactStatus, Metric, Product, Task, TaskStatus

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

    async def menu(self, chat_id):
        self.topic_product = self.metrics_article = self.plan_product = self.post_target = self.post_edit_target = None
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
            buttons.append([{'text':'📅 Контент-план', 'callback_data':f'content:calendar:{product.id}'}])
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
            self.topic_product = self.metrics_article = self.plan_product = self.post_target = self.post_edit_target = None
            return False
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
            # Leaving the quick-post wizard must not steal a later unrelated
            # message (company profile, research parameters, statistics, etc.).
            if action not in {'postchannels', 'posttg', 'postvk', 'postdzen', 'postvc', 'postten'}:
                self.post_target = None
            if action != 'editpost':
                self.post_edit_target = None
            if action == 'postchannels':
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
            elif action == 'approvepost':
                with self.sessions() as db:
                    item = db.scalar(select(Artifact).where(
                        Artifact.id == record_id, Artifact.kind == 'channel_text'
                    ).with_for_update())
                    if item is None or json.loads(item.content).get('origin') not in {'quick_post', 'owner_revision'}:
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
                    if item is None or item.kind not in {'channel_text','video_script','content_distribution'}:
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
                            if (value.get('origin') in {'quick_post', 'owner_revision'} and
                                    item.status == ArtifactStatus.AWAITING_APPROVAL):
                                buttons.append([{
                                    'text':'✅ Утвердить текст',
                                    'callback_data':f'content:approvepost:{item.id}',
                                }])
                            buttons.append([{
                                'text':'✏️ Попросить доработку',
                                'callback_data':f'content:editpost:{item.id}',
                            }])
                            reply_markup = {'inline_keyboard': buttons}
                        if value.get('origin') in {'quick_post', 'owner_revision'}:
                            if reply_markup is None:
                                reply_markup = {'inline_keyboard': []}
                            reply_markup['inline_keyboard'].append([{
                                'text': '📚 История версий',
                                'callback_data': f'content:history:{item.id}',
                            }])
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
