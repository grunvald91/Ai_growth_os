"""Two authenticated identities, one account. No phone/email matching or user deletion."""

from datetime_utils import utc_now
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import or_, select, update, text

from database.models import (User, EmailIdentity, EmailChallenge, TelegramLinkRequest, UserAccountMerge,
    Business, BusinessOwner, Booking, ClientSubscription, PushSubscription, SalesConversation,
    YandexIdentity, YandexTicket, CRMContact)


class AccountLinkError(ValueError):
    pass


def phone_key(phone):
    """Digits of a Russian-style number in one spelling: +7 (918) 000-11-22, 89180001122 -> 79180001122."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) >= 10 else ""


def phones_match(first, second):
    return bool(phone_key(first)) and phone_key(first) == phone_key(second)


def phone_spellings(phone):
    """How the same number may be stored in users.phone (bot contacts come without "+")."""
    key = phone_key(phone)
    if not key:
        return set()
    spellings = {key, "+" + key}
    if len(key) == 11 and key.startswith("7"):
        spellings.add("8" + key[1:])
    return spellings


def link_token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def telegram_claim(db, token, telegram):
    request = await db.scalar(select(TelegramLinkRequest).where(TelegramLinkRequest.token_hash == link_token_hash(token)).with_for_update())
    if not request or request.consumed or request.expires_at <= utc_now():
        raise AccountLinkError("Ссылка недействительна или истекла. Запросите новую в кабинете")
    # Only an authenticated Telegram update calls this function, never a browser-supplied ID.
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": telegram.id})
    target = await db.scalar(select(User).where(User.telegram_id == telegram.id))
    if target is None:
        target = User(telegram_id=telegram.id, username=telegram.username, full_name=telegram.full_name)
        db.add(target); await db.flush()
    if request.telegram_user_id and request.telegram_user_id != target.id:
        raise AccountLinkError("Эта ссылка уже открыта другим Telegram-аккаунтом. Запросите новую")
    request.telegram_user_id = target.id
    return request


async def telegram_confirm(db, request_id, telegram_id):
    request = await db.scalar(select(TelegramLinkRequest).where(TelegramLinkRequest.id == request_id).with_for_update())
    if not request or request.consumed or request.expires_at <= utc_now():
        raise AccountLinkError("Запрос уже завершён или истёк")
    target = await db.get(User, request.telegram_user_id) if request.telegram_user_id else None
    if target is None or target.telegram_id != telegram_id:
        raise AccountLinkError("Подтверждать должен тот же Telegram-аккаунт, который открыл ссылку")
    request.telegram_confirmed = True
    return request


async def merge_email_into_telegram(db, request, source_user_id):
    """Called only after email password re-authentication AND Telegram confirmation."""
    if request.user_id != source_user_id or request.consumed or request.expires_at <= utc_now() or not request.telegram_confirmed:
        raise AccountLinkError("Привязка не подтверждена или истекла")
    users = list((await db.execute(select(User).where(User.id.in_([source_user_id, request.telegram_user_id]))
        .order_by(User.id).with_for_update().execution_options(populate_existing=True))).scalars())
    by_id = {u.id: u for u in users}
    source, target = by_id.get(source_user_id), by_id.get(request.telegram_user_id)
    if source is None or target is None or source.id == target.id or source.telegram_id > 0 or target.telegram_id <= 0:
        raise AccountLinkError("Этот аккаунт уже связан с Telegram")
    identities = list((await db.execute(select(EmailIdentity).where(EmailIdentity.user_id.in_([source.id, target.id]))
        .order_by(EmailIdentity.email).with_for_update().execution_options(populate_existing=True))).scalars())
    identity = next((i for i in identities if i.user_id == source.id), None)
    if identity is None or identity.session_version != request.email_session_version:
        raise AccountLinkError("Сессия email изменилась. Войдите заново и запросите новую привязку")
    if any(i.user_id == target.id for i in identities):
        raise AccountLinkError("Telegram уже связан с другой подтверждённой почтой. Обратитесь в поддержку")
    if await db.get(UserAccountMerge, source.id) or await db.get(UserAccountMerge, target.id):
        raise AccountLinkError("Аккаунт уже объединён. Войдите заново")
    await move_account_data(db, source, target)
    target.email = identity.email
    identity.user_id = target.id
    identity.session_version += 1
    await db.execute(update(EmailChallenge).where(EmailChallenge.user_id == source.id).values(consumed=True))
    await db.execute(update(TelegramLinkRequest).where(TelegramLinkRequest.user_id == source.id).values(consumed=True))
    request.consumed = True
    await db.flush()
    return identity


async def move_crm_contacts(db, source, target):
    """Keep tenant-local CRM data when identities join; never silently discard conflicts."""
    contacts = list((await db.scalars(select(CRMContact).where(
        CRMContact.user_id.in_([source.id, target.id])
    ).order_by(CRMContact.id).with_for_update())).all())
    targets = {c.business_id: c for c in contacts if c.user_id == target.id}
    pending = []
    for contact in contacts:
        if contact.user_id != source.id:
            continue
        existing = targets.get(contact.business_id)
        if existing is None:
            pending.append((contact, None, {}))
            continue
        changes = {}
        for field in ("name", "phone", "email", "stage", "follow_up_on"):
            old, new = getattr(contact, field), getattr(existing, field)
            empty = "new" if field == "stage" else None
            if old not in (empty, "") and new not in (empty, "") and old != new:
                raise AccountLinkError(
                    "Карточки CRM содержат разные данные. Согласуйте их в бизнесе "
                    f"{contact.business_id} перед объединением аккаунтов"
                )
            if new in (empty, "") and old not in (empty, ""):
                changes[field] = old
        notes = existing.notes or ""
        if contact.notes and contact.notes != notes:
            notes = "\n\n".join(filter(None, (notes, contact.notes)))
        if len(notes) > 5000:
            raise AccountLinkError("Объединённая заметка CRM превышает 5000 символов. Сократите заметки перед объединением")
        changes["notes"] = notes
        changes["created_at"] = min(contact.created_at, existing.created_at)
        pending.append((contact, existing, changes))
    # Validate every tenant before changing any contact.
    for contact, existing, changes in pending:
        if existing is None:
            contact.user_id = target.id
        else:
            for field, value in changes.items():
                setattr(existing, field, value)
            await db.delete(contact)


async def move_account_data(db, source, target):
    """Move businesses, bookings, subscriptions and chats from source to target and record the merge.

    Both users must already be locked by the caller. Source stays as a redirect to target."""
    await move_crm_contacts(db, source, target)
    # Preserve an existing paid/manual-owner entitlement; don't guess between two paid plans.
    if source.subscription_status != "none" or source.is_manual_owner:
        if target.subscription_status != "none" or target.is_manual_owner:
            raise AccountLinkError("У обоих аккаунтов есть подписки. Объединение должен проверить оператор")
        for name in ("tariff", "max_businesses", "subscription_status", "subscription_expires", "is_manual_owner"):
            setattr(target, name, getattr(source, name))
    target.is_admin = target.is_admin or source.is_admin
    if not target.phone:
        target.phone = source.phone

    # Assets keep their IDs/statuses; only the owning user reference changes.
    for model, column in ((Business, Business.owner_id), (Booking, Booking.client_id),
                          (ClientSubscription, ClientSubscription.client_id), (PushSubscription, PushSubscription.user_id)):
        await db.execute(update(model).where(column == source.id).values({column.key: target.id}))
    memberships = list((await db.execute(select(BusinessOwner).where(BusinessOwner.user_id.in_([source.id, target.id]))
        .order_by(BusinessOwner.id).with_for_update())).scalars())
    target_memberships = {o.business_id: o for o in memberships if o.user_id == target.id}
    for member in [o for o in memberships if o.user_id == source.id]:
        existing = target_memberships.get(member.business_id)
        if existing:
            if member.role == "owner": existing.role = "owner"
            await db.delete(member)
        else:
            member.user_id = target.id
            target_memberships[member.business_id] = member
    threads = list((await db.execute(select(SalesConversation).where(SalesConversation.principal.in_([
        f"user:{source.id}", f"user:{target.id}"])).order_by(SalesConversation.id).with_for_update())).scalars())
    target_businesses = {t.business_id for t in threads if t.principal == f"user:{target.id}"}
    for thread in [t for t in threads if t.principal == f"user:{source.id}"]:
        thread.principal = f"archive:merged:{source.id}:{uuid.uuid4().hex}" if thread.business_id in target_businesses else f"user:{target.id}"
        # Invalidate an unconfirmed card whose booking principal changed; preserve conversation text.
        if thread.action and thread.action.get("status") != "completed":
            thread.action = None; thread.action_id = None
    db.add(UserAccountMerge(source_user_id=source.id, target_user_id=target.id))


async def merge_yandex_account(db, source_user_id, target_user_id):
    """Join an account created by Yandex sign-in (no Telegram, no email password) into another account.

    Call only after the same browser proved both sides just now: the Yandex account (its session or a fresh
    Yandex login) and the target (a fresh Telegram login or its session)."""
    users = list((await db.execute(select(User).where(User.id.in_([source_user_id, target_user_id]))
        .order_by(User.id).with_for_update().execution_options(populate_existing=True))).scalars())
    by_id = {u.id: u for u in users}
    source, target = by_id.get(source_user_id), by_id.get(target_user_id)
    if source is None or target is None or source.id == target.id or source.telegram_id > 0:
        raise AccountLinkError("Эти аккаунты нельзя объединить")
    yandex = await db.scalar(select(YandexIdentity).where(YandexIdentity.user_id == source.id).with_for_update())
    if yandex is None:
        raise AccountLinkError("Аккаунт не создан через Яндекс")
    if await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == source.id)):
        raise AccountLinkError("У аккаунта есть вход по почте: объедините его через привязку Telegram в профиле")
    if await db.scalar(select(YandexIdentity).where(YandexIdentity.user_id == target.id)):
        raise AccountLinkError("К аккаунту уже привязан другой Яндекс")
    if await db.get(UserAccountMerge, source.id) or await db.get(UserAccountMerge, target.id):
        raise AccountLinkError("Аккаунт уже объединён. Войдите заново")
    await move_account_data(db, source, target)
    for name in ("email", "birthday"):
        if not getattr(target, name) and getattr(source, name):
            setattr(target, name, getattr(source, name))
    yandex.user_id = target.id
    await db.execute(update(YandexTicket).where(or_(YandexTicket.link_user_id == source.id,
                                                    YandexTicket.code_user_id == source.id)).values(consumed=True))
    await db.flush()
    return target
