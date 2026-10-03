"""One reviewed email per prospect. Durable reservation before SMTP, never auto-retry."""
import asyncio
import hashlib
import hmac
import json
import smtplib
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import select
from app.company_profile import load_company_profile, require_profile_ready
from app.control.decisions import approval_review_token, decide_approval
from app.models.domain import Approval, Artifact, Experiment, Product, Prospect, ToolCall
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactService
from app.services.outreach import OutreachPackageService
from app.communications.mail import MailStore, send_email


def eligible(db, prospect_id, root, channel=None):
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise LookupError('Контакт не найден')
    product = db.get(Product, prospect.product_id)
    experiment = db.get(Experiment, prospect.experiment_id)
    if not product or not experiment or experiment.product_id != product.id or experiment.status != 'running':
        raise ValueError('Эксперимент не запущен')
    profile = require_profile_ready(load_company_profile(root, product.slug), 'outreach')
    channel = channel or prospect.preferred_channel
    if channel not in profile.outreach.allowed_channels or channel not in {'email', 'telegram', 'whatsapp'}:
        raise ValueError('Канал не разрешён в профиле компании')
    target = getattr(prospect, channel)
    if prospect.validation_status != 'validated' or not target or prospect.outreach_status != 'not_contacted':
        raise ValueError('Контакт не проверен или обращение уже зафиксировано')
    if not prospect.source_checked_at or prospect.source_checked_at < date.today() - timedelta(days=30):
        raise ValueError('Нужно повторно проверить актуальность контакта')
    if not any(e.get('field') == channel and e.get('value') == target for e in prospect.evidence or []):
        raise ValueError('Нет источника подтверждения этого канала')
    terms = [prospect.business_name, prospect.lead_key, target]
    if any(term.casefold().strip() in ' '.join(terms).casefold()
           for term in profile.outreach.do_not_contact if term.strip()):
        raise ValueError('Контакт входит в список исключений')
    return prospect, profile


def draft_for(prospect, profile, channel):
    from types import SimpleNamespace
    data = {key: getattr(prospect, key) for key in ('id', 'lead_key', 'business_name', 'category',
        'notes', 'tracking_url', 'email', 'telegram', 'whatsapp', 'evidence')}
    return OutreachPackageService._draft(SimpleNamespace(preferred_channel=channel, **data), profile)


def mail_revision(store, product_id):
    with store.locked():
        path = store.path(product_id)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def prepare_email(db, prospect_id, root):
    prospect, profile = eligible(db, prospect_id, root, 'email')
    store = MailStore(root)
    config = store.load(prospect.product_id)
    if not config:
        raise ValueError('Сначала подключи почту в разделе «Почта»')
    data = draft_for(prospect, profile, 'email')
    data['from_email'], data['from_name'] = config.from_email, config.from_name
    data['mail_revision'] = mail_revision(store, prospect.product_id)
    content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    artifact = db.scalar(select(Artifact).where(Artifact.product_id == prospect.product_id,
        Artifact.kind == 'outreach_message', Artifact.content == content))
    if artifact is None:
        artifact = ArtifactService(db).create(product_id=prospect.product_id, kind='outreach_message',
            title='Письмо: ' + prospect.business_name, content=content, status='awaiting_approval')
    approval = db.scalar(select(Approval).where(Approval.artifact_id == artifact.id,
        Approval.action_type == 'send_outreach_message', Approval.status.in_(['pending', 'approved'])))
    if approval is None:
        approval = ApprovalService(db).create(product_id=prospect.product_id, artifact_id=artifact.id,
            action_type='send_outreach_message', risk_level='YELLOW', requested_by='owner:contact_card',
            reason='Отправить одно письмо именно этому адресату с показанными темой, текстом и отправителем.')
    token = approval_review_token(db, approval)
    db.commit()
    return approval.id, token, data

async def execute_email(session_factory, root, approval_id, token, actor, sender=send_email):
    with session_factory() as db:
        approval = db.get(Approval, approval_id)
        if approval is None or approval.action_type != 'send_outreach_message':
            raise LookupError('Письмо не найдено')
        if approval.status not in {'pending', 'approved'} or not hmac.compare_digest(token, approval_review_token(db, approval)):
            raise ValueError('Письмо изменилось или отклонено. Открой карточку заново')
        artifact = db.get(Artifact, approval.artifact_id)
        data = json.loads(artifact.content)
        prospect_id = UUID(data['prospect_id'])
        prospect = db.scalar(select(Prospect).where(Prospect.id == prospect_id).with_for_update())
        if prospect is None or prospect.product_id != approval.product_id:
            raise ValueError('Неверная компания контакта')
        approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update()
            .execution_options(populate_existing=True))
        if approval.status not in {'pending', 'approved'} or not hmac.compare_digest(token, approval_review_token(db, approval)):
            raise ValueError('Письмо изменилось или отклонено. Открой карточку заново')
        prospect, profile = eligible(db, prospect_id, root, 'email')
        current = draft_for(prospect, profile, 'email')
        if any(data.get(key) != value for key, value in current.items()):
            raise ValueError('Данные контакта или профиль изменились. Открой карточку заново')
        store = MailStore(root)
        config = store.load(prospect.product_id)
        if not config or data['mail_revision'] != mail_revision(store, prospect.product_id):
            raise ValueError('Настройки почты изменились. Открой карточку заново')
        # This exact-message click is its own authorization; never approves a batch.
        approval.status, approval.decided_by = 'approved', actor
        approval.decided_at = datetime.now(timezone.utc)
        prospect.outreach_status = 'sending'
        call = ToolCall(product_id=prospect.product_id, approval_id=approval.id, tool_name='smtp',
            operation='send_outreach_message', request_data={'prospect_id': str(prospect.id),
                'artifact_id': str(artifact.id), 'recipient': data['target'], 'actor': actor}, status='sending')
        db.add(call)
        db.flush()
        call_id = call.id
        message_id = f'<growth-{call_id}@{config.from_email.rsplit("@",1)[1]}>'
        db.commit()
    try:
        await asyncio.to_thread(sender, config, recipient=data['target'], subject=data['subject'],
                                body=data['body'], message_id=message_id)
    except (Exception, asyncio.CancelledError) as exc:
        definitely_not_sent = isinstance(exc, (smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError,
            smtplib.SMTPHeloError, smtplib.SMTPNotSupportedError, smtplib.SMTPSenderRefused,
            smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError))
        with session_factory() as db:
            call = db.get(ToolCall, call_id)
            call.status, call.error = ('not_sent' if definitely_not_sent else 'delivery_unknown'), type(exc).__name__
            db.get(Prospect, prospect_id).outreach_status = 'not_contacted' if definitely_not_sent else 'delivery_unknown'
            db.commit()
        if definitely_not_sent:
            return 'Почтовый сервер отказал в отправке. Письмо не принято. Проверь подключение; после исправления можно открыть карточку и повторить.'
        if isinstance(exc, asyncio.CancelledError):
            raise
        return 'Не могу подтвердить отправку. Проверь почту: повторная отправка заблокирована, чтобы не продублировать письмо.'
    with session_factory() as db:
        call = db.get(ToolCall, call_id)
        call.status, call.executed_at = 'smtp_accepted', datetime.now(timezone.utc)
        call.response_data = {'message_id': message_id, 'accepted': True, 'delivery_confirmed': False}
        prospect = db.get(Prospect, prospect_id)
        prospect.outreach_status, prospect.last_contact_at = 'contacted', datetime.now(timezone.utc)
        db.commit()
    return '✅ Почтовый сервер принял письмо. Это подтверждение отправки, а не прочтения или доставки во входящие.'
