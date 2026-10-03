"""Owner-only contact cards and SMTP setup. No background email campaigns."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from app.communications.links import chat_link
from app.communications.mail import SMTPConfig, MailStore, check_connection, email_address
from app.communications.outreach import eligible, draft_for, prepare_email, execute_email
from app.company_profile import CompanyProfileNotReady
from app.intake.telegram import LABELS
from app.models.domain import Product, Prospect

NAV = {'📊 Главное','✅ На согласование','🧪 Тесты','🔄 Обновить','🏢 Компания','📝 Контент','📇 Контакты','📧 Почта'}

class TelegramCommunications:
    def __init__(self, bot, session_factory, settings_factory):
        self.bot, self.sessions, self.settings = bot, session_factory, settings_factory
        self.mail_product = None
        self.mail_step = None
        self.mail_draft = {}

    def store(self):
        return MailStore(self.settings().products_root)

    async def contacts(self, chat, offset=0):
        if offset < 0 or offset > 10000:
            raise ValueError('Неверная страница')
        with self.sessions() as db:
            prospects = list(db.scalars(select(Prospect).order_by(Prospect.product_id, Prospect.lead_key, Prospect.id)
                                        .offset(offset).limit(9)))
            buttons = [[{'text': f'{p.lead_key} · {p.business_name}'[:60],
                        'callback_data':f'contact:open:{p.id}'}] for p in prospects[:8]]
            if offset:
                buttons.append([{'text':'⬅️ Назад', 'callback_data':f'contact:list:{max(0,offset-8)}'}])
            if len(prospects) > 8:
                buttons.append([{'text':'➡️ Далее', 'callback_data':f'contact:list:{offset+8}'}])
        await self.bot._send(chat, '📇 Найденные компании. Выбери контакт — покажу источники, предложение и доступные действия.' if prospects else 'Контактов пока нет.', reply_markup={'inline_keyboard':buttons})

    async def contact(self, chat, prospect_id, channel=None):
        with self.sessions() as db:
            p = db.get(Prospect, prospect_id)
            if p is None:
                raise LookupError('Контакт не найден')
            product = db.get(Product, p.product_id)
            states = {'not_contacted':'обращение не зафиксировано', 'contacted':'обращение зафиксировано',
                      'sending':'отправка начата; повтор заблокирован', 'delivery_unknown':'результат отправки требует проверки'}
            text = f'📇 {p.business_name}\nПроект: {product.name}\n{p.area or ""} · {p.category or ""}\n{p.address or ""}\n\n{p.notes or ""}\n\nПроверено: {p.source_checked_at or "дата неизвестна"}\nСостояние: {states.get(p.outreach_status,p.outreach_status)}'
            for key in ('telegram','whatsapp','email','website'):
                if getattr(p,key):
                    text += f'\n{key}: {getattr(p,key)}'
            sources = list(dict.fromkeys(e.get('source_url') for e in p.evidence or [] if e.get('source_url')))
            text += '\n\nИсточники:\n' + '\n'.join(sources[:5])
            buttons = []
            for key, label in [('telegram','💬 Telegram'),('whatsapp','💚 WhatsApp'),('email','✉️ Email')]:
                if getattr(p,key):
                    buttons.append([{'text':label+' — посмотреть текст', 'callback_data':f'contact:{key}:{p.id}'}])
            if channel:
                prospect, profile = eligible(db, p.id, self.settings().products_root, channel)
                if channel == 'email':
                    approval_id, token, data = prepare_email(db, p.id, self.settings().products_root)
                    text += f'\n\nОт: {data["from_name"]} <{data["from_email"]}>\nКому: {data["target"]}\nТема: {data["subject"]}\n\n{data["body"]}'
                    # e:UUID:16char => 55 bytes, under Telegram's 64-byte limit.
                    buttons.insert(0,[{'text':'📤 Отправить это письмо', 'callback_data':f'e:{approval_id}:{token}'}])
                else:
                    data = draft_for(prospect, profile, channel)
                    text += '\n\nПредлагаемый текст:\n' + data['body']
                    buttons.insert(0,[{'text':'↗️ Открыть чат с этим текстом', 'url':chat_link(channel,data['target'],data['body'])}])
                    buttons.insert(1,[{'text':'✅ Я отправил сообщение', 'callback_data':f'contact:mark:{p.id}'}])
                    text += '\n\nПереход откроет черновик. После отправки отметь её отдельной кнопкой.'
            buttons.append([{'text':'📧 Настройки почты', 'callback_data':f'mail:open:{p.product_id}'}])
            buttons.append([{'text':'📇 Ко всем контактам', 'callback_data':'contact:list:0'}])
        await self.bot._send(chat, text, reply_markup={'inline_keyboard':buttons})

    async def mail_menu(self, chat):
        self.mail_step, self.mail_draft = None, {}
        with self.sessions() as db:
            products = list(db.scalars(select(Product).order_by(Product.name).limit(20)))
            buttons = [[{'text':p.name, 'callback_data':f'mail:open:{p.id}'}] for p in products]
        await self.bot._send(chat, '📧 Для какой компании настроить почту?', reply_markup={'inline_keyboard':buttons})

    async def mail_settings(self, chat, product_id):
        self.mail_step, self.mail_draft = None, {}
        with self.sessions() as db:
            product = db.get(Product, product_id)
            if product is None:
                raise LookupError('Компания не найдена')
            name = product.name
        config = self.store().load(product_id)
        text = f'📧 Почта · {name}\n\n'
        if config:
            text += f'Отправитель: {config.from_name} <{config.from_email}>\nСервер: {config.host}:{config.port}\nПароль скрыт.\n'
        else:
            text += 'Почта не подключена.\n'
        text += '\nБот отправляет отдельное письмо по кнопке в карточке контакта. Проверка подключения писем не отправляет.'
        buttons = [[{'text':'⚙️ Подключить / изменить', 'callback_data':f'mail:setup:{product_id}'}]]
        if config:
            buttons += [[{'text':'🔌 Проверить подключение', 'callback_data':f'mail:test:{product_id}'}],
                        [{'text':'Отключить почту', 'callback_data':f'mail:remove:{product_id}'}]]
        await self.bot._send(chat,text,reply_markup={'inline_keyboard':buttons})

    async def message(self, message):
        text = (message.get('text') or '').strip()
        chat = message['chat']['id']
        if text in NAV or text.startswith('/'):
            self.mail_step, self.mail_draft = None, {}
            if text in {'📇 Контакты','/contacts'}:
                await self.contacts(chat)
                return True
            if text in {'📧 Почта','/mail'}:
                await self.mail_menu(chat)
                return True
            return False
        if not self.mail_step:
            return False
        try:
            if self.mail_step == 'server':
                parts = text.split()
                if len(parts) != 2:
                    raise ValueError('host port')
                self.mail_draft['host'], self.mail_draft['port'] = parts[0], int(parts[1])
                # Validate public-style hostname and TLS port before collecting a secret.
                SMTPConfig(host=parts[0], port=int(parts[1]), username='placeholder', password='placeholder', from_email='placeholder@example.com')
                self.mail_step = 'address'
                await self.bot._send(chat,'Напиши email, с которого будут отправляться письма. Обычно он же является логином SMTP.')
            elif self.mail_step == 'address':
                self.mail_draft['from_email'] = email_address(text)
                self.mail_draft['username'] = text
                self.mail_step = 'username'
                await self.bot._send(chat,'Напиши логин SMTP. Если он совпадает с email, напиши «тот же».')
            elif self.mail_step == 'username':
                if text.casefold() != 'тот же':
                    self.mail_draft['username'] = text
                self.mail_step = 'name'
                await self.bot._send(chat,'Как подписывать отправителя? Например: Артур GrunvaldLab.')
            elif self.mail_step == 'name':
                self.mail_draft['from_name'] = text
                self.mail_step = 'password'
                await self.bot._send(chat,'Теперь пришли пароль приложения для SMTP, не основной пароль аккаунта. Бот удалит это сообщение перед сохранением и не передаст пароль ИИ. Сам Telegram не является хранилищем с сквозным шифрованием. Для отмены — /start.')
            elif self.mail_step == 'password':
                # Do not retain credentials if the sensitive chat message cannot be removed.
                await self.bot.api.call('deleteMessage', {'chat_id':chat,'message_id':message['message_id']})
                config = SMTPConfig(**self.mail_draft, password=text)
                self.store().save(self.mail_product,config)
                product_id = self.mail_product
                self.mail_step, self.mail_draft = None, {}
                await self.bot._send(chat,'✅ Настройки сохранены. Пароль зашифрован на сервере. Подключение ещё не проверено.')
                await self.mail_settings(chat,product_id)
            return True
        except Exception:
            await self.bot._send(chat,'Не удалось сохранить этот шаг. Проверь формат данных; пароль и подробности ошибки не выводятся. Для отмены — /start.')
            return True

    async def callback(self, chat, data):
        try:
            prefix, action, raw = data.split(':',2)
            if prefix == 'e':
                result = await execute_email(self.sessions,self.settings().products_root,UUID(action),raw,
                                             f'telegram:{self.bot.owner_chat_id}')
                await self.bot._send(chat,result)
                return True
            if prefix == 'contact':
                if action == 'list':
                    await self.contacts(chat,int(raw))
                elif action == 'mark':
                    with self.sessions() as db:
                        p = db.scalar(select(Prospect).where(Prospect.id == UUID(raw)).with_for_update())
                        if not p or p.outreach_status != 'not_contacted':
                            raise ValueError('Отправка уже зафиксирована или недоступна')
                        p.outreach_status, p.last_contact_at = 'contacted', datetime.now(timezone.utc)
                        db.commit()
                    await self.bot._send(chat,'✅ Отправка отмечена по твоему подтверждению. Бот не проверяет сообщения в личных чатах.')
                else:
                    await self.contact(chat,UUID(raw),channel=action if action in {'telegram','whatsapp','email'} else None)
            elif prefix == 'mail':
                product_id = UUID(raw)
                with self.sessions() as db:
                    if db.get(Product,product_id) is None:
                        raise LookupError('Компания не найдена')
                if action == 'open':
                    await self.mail_settings(chat,product_id)
                elif action == 'setup':
                    self.mail_product, self.mail_step, self.mail_draft = product_id, 'server', {}
                    await self.bot._send(chat,'Напиши SMTP-сервер и порт через пробел.\nНапример: smtp.gmail.com 465\nПоддерживаются защищённые порты 465 и 587.')
                elif action == 'remove':
                    self.store().delete(product_id)
                    await self.mail_settings(chat,product_id)
                elif action == 'test':
                    config = self.store().load(product_id)
                    if config is None:
                        raise ValueError('Сначала подключи почту')
                    try:
                        await asyncio.to_thread(check_connection,config)
                        await self.bot._send(chat,'✅ TLS и вход в SMTP проверены. Тестовых писем не отправляли.')
                    except Exception:
                        await self.bot._send(chat,'Не удалось проверить SMTP. Проверь сервер, логин и пароль приложения. Подробности ошибки скрыты.')
            return True
        except CompanyProfileNotReady as exc:
            fields = '\n'.join('• '+LABELS.get(k,k) for k in exc.missing_fields)
            await self.bot._send(chat,'Для подготовки обращения нужно заполнить профиль компании:\n'+fields+'\nОткрой раздел «🏢 Компания». Найденные контакты сохранены.')
            return True
        except (ValueError, LookupError, OSError):
            await self.bot._send(chat,'Действие недоступно. Проверь профиль компании, почту и состояние контакта; затем открой карточку заново.')
            return True
