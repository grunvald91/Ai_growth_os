"""
Модели базы данных для системы записи
"""

from datetime_utils import utc_now
from datetime import datetime
from typing import List, Optional
from sqlalchemy import BigInteger, String, DateTime, ForeignKey, Boolean, Integer, Text, Time, Date, UniqueConstraint, JSON, LargeBinary
from sqlalchemy.sql import false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей"""
    pass


class SalesConversation(Base):
    """Durable, tenant-scoped sales thread; no model-supplied identity fields."""
    __tablename__ = "sales_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    principal: Mapped[str] = mapped_column(String(80))  # user:<id> or guest:<cookie digest>
    messages: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(32), default="needs_analysis")
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    mode: Mapped[str] = mapped_column(String(16), default="ai")
    handoff_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True)
    action: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (UniqueConstraint("business_id", "principal", name="uq_sales_business_principal"),)


class CRMContact(Base):
    """Business-local CRM data. Linked users retain their global account data unchanged."""
    __tablename__ = "crm_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL for manually added contacts; unique per business for linked booking users.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    follow_up_on: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (UniqueConstraint("business_id", "user_id", name="uq_crm_business_user"),)


class User(Base):
    """Пользователи бота (клиенты и владельцы бизнеса)"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    # Дата рождения «ГГГГ-ММ-ДД» из Яндекс ID; если год неизвестен, он «0000». Для поздравлений.
    birthday: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # === SUBSCRIPTION / TARIFF FIELDS ===
    # Тариф пользователя: standard (1 бизнес), business (3 бизнеса), network (безлимит)
    tariff: Mapped[str] = mapped_column(String(20), default="standard")
    # Максимум бизнесов по тарифу (0 = безлимит)
    max_businesses: Mapped[int] = mapped_column(Integer, default=1)
    # Статус подписки: trial, active, suspended, none
    subscription_status: Mapped[str] = mapped_column(String(20), default="none")
    # Дата окончания подписки/триала
    subscription_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Добавлен администратором вручную - без ограничений!
    is_manual_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    # Связь с бизнесами (если пользователь - владелец)
    businesses: Mapped[List["Business"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    # Связь с записями
    bookings: Mapped[List["Booking"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    # Связь с абонементами клиента
    client_subscriptions: Mapped[List["ClientSubscription"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class EmailIdentity(Base):
    """Only verified addresses grant login; User.email remains a contact field."""
    __tablename__ = "email_identities"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class YandexIdentity(Base):
    """One Yandex ID account belongs to one ServGo user (and the other way round)."""
    __tablename__ = "yandex_identities"
    yandex_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Email and phone confirmed by Yandex at the last Yandex sign-in: only these are trusted for joining accounts.
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)



class VkIdentity(Base):
    """VK ID provider subject (not a VK community publishing credential)."""
    __tablename__ = "vk_identities"
    vk_user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class VkLoginTicket(Base):
    """One-time, browser-bound handoff after VK OAuth; stores no OAuth tokens."""
    __tablename__ = "vk_login_tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    link_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class YandexTicket(Base):
    """One-time pass from the Yandex callback to the web app. Only a hash of the secret is stored."""
    __tablename__ = "yandex_tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    link_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    code_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    code_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    code_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sends: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailChallenge(Base):
    __tablename__ = "email_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    purpose: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthRateBucket(Base):
    __tablename__ = "auth_rate_buckets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    count: Mapped[int] = mapped_column(Integer, default=0)


class TelegramLinkRequest(Base):
    __tablename__ = "telegram_link_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email_session_version: Mapped[int] = mapped_column(Integer)
    telegram_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class UserAccountMerge(Base):
    """Retain old IDs/audit rows; authentication resolves the canonical account."""
    __tablename__ = "user_account_merges"
    source_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    merged_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class BookingReceipt(Base):
    __tablename__ = "booking_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    mime_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PrepaymentReview(Base):
    __tablename__ = "prepayment_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(16))
    booking_version: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("booking_id", "booking_version", name="uq_prepayment_review_version"),)


class PushSubscription(Base):
    """Web Push subscription for an installed/browser ServGo client."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class NotificationDelivery(Base):
    """Dedupe/audit record for configurable staff reminders and system events."""
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    booking_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    business_id: Mapped[int] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    audience: Mapped[str] = mapped_column(String(32))  # client | owner | master | admin | staff
    event_kind: Mapped[str] = mapped_column(String(64))
    offset_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    telegram_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    webpush_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Business(Base):
    """Бизнесы (салон красоты, автосервис и т.д.)"""
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    business_type: Mapped[str] = mapped_column(String(100))  # salon, auto_service, psychologist и т.д.
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    working_hours_start: Mapped[Optional[datetime.time]] = mapped_column(Time, nullable=True)
    working_hours_end: Mapped[Optional[datetime.time]] = mapped_column(Time, nullable=True)
    break_start: Mapped[Optional[datetime.time]] = mapped_column(Time, nullable=True)  # Начало перерыва
    break_end: Mapped[Optional[datetime.time]] = mapped_column(Time, nullable=True)  # Конец перерыва
    booking_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)  # Шаг записи по умолчанию
    photos: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON массив file_id фотографий
    video: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Telegram file_id видеообзора
    directions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Как добраться
    booking_labels: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: кастомные названия шагов записи
    booking_flow_order: Mapped[str] = mapped_column(String(32), default="service_first")  # service_first | resource_first
    allow_multiple_services: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # клиент может выбрать несколько услуг в одной записи

    # Staff reminder settings. Immediate booking events are mandatory and are
    # intentionally NOT controlled by these switches.
    owner_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_reminder_minutes: Mapped[str] = mapped_column(Text, default="[1440, 120]", nullable=False)
    master_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    master_reminder_minutes: Mapped[str] = mapped_column(Text, default="[1440, 120]", nullable=False)

    # Настройки предоплаты
    prepayment_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # Включена ли предоплата
    prepayment_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Процент предоплаты (100 = полная)
    payment_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # Номер для перевода
    payment_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Дополнительные реквизиты (карта, СБП и т.д.)
    cancellation_policy_photo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Telegram file_id фото условий отмены
    cancellation_policy_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Текст условий отмены
    require_receipt_for_prepayment: Mapped[bool] = mapped_column(Boolean, default=False)  # Требовать чек для подтверждения оплаты

    # Юридические данные оператора персональных данных / исполнителя услуг.
    # Не заполняем их автоматически: владелец бизнеса обязан указать реальные реквизиты.
    legal_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    legal_inn: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    legal_ogrn: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    legal_address: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    legal_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    legal_documents_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Владелец разрешает администраторам заведения входить в маркетинговую студию (growth.servgo.ru).
    studio_admin_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    ai_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI-инструкция для бизнеса
    sales_agent_settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    version: Mapped[int] = mapped_column(Integer, default=1)  # Для оптимистичной блокировки

    # Связи
    owner: Mapped["User"] = relationship(back_populates="businesses")
    owners: Mapped[List["BusinessOwner"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    services: Mapped[List["Service"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    employees: Mapped[List["Employee"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    bookings: Mapped[List["Booking"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    client_subscriptions: Mapped[List["ClientSubscription"]] = relationship(back_populates="business", cascade="all, delete-orphan")


class MarketingLead(Base):
    """Inbound ServGo business lead with minimal growth-attribution data."""
    __tablename__ = "marketing_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    experiment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    utm_content: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referrer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    landing_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    self_reported_source: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    attribution_confidence: Mapped[str] = mapped_column(String(20), default="unverified")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class BusinessOwner(Base):
    """Множественные владельцы бизнеса"""
    __tablename__ = "business_owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner, co_owner
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (UniqueConstraint('business_id', 'user_id', name='uix_business_user'),)

    # Связи
    business: Mapped["Business"] = relationship(back_populates="owners")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    added_by: Mapped[Optional["User"]] = relationship(foreign_keys=[added_by_id])


class Service(Base):
    """Услуги бизнеса"""
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)  # Длительность в минутах
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Цена (можно в копейках)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_additional: Mapped[bool] = mapped_column(Boolean, default=False)  # Дополнительная услуга (для аренды)
    is_hourly_price: Mapped[bool] = mapped_column(Boolean, default=False)  # Цена за час (для доп. услуг venue_rental)
    available_employee_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON список ID сотрудников/залов, где доступна услуга (null = все)
    tariff_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Текст тарифов для контактных услуг (бронь через владельца)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # Порядок отображения (меньше = выше в списке)
    ai_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI-инструкция
    version: Mapped[int] = mapped_column(Integer, default=1)  # Для оптимистичной блокировки
    pricing_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default='fixed')  # 'fixed' | 'per_person' — тип ценообразования
    group_pricing: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: [{"min_people":1,"max_people":3,"price":500}, ...] — тарифы по кол-ву человек

    # Связи
    business: Mapped["Business"] = relationship(back_populates="services")
    bookings: Mapped[List["Booking"]] = relationship(back_populates="service", cascade="all, delete-orphan")
    employees: Mapped[List["EmployeeService"]] = relationship(back_populates="service", cascade="all, delete-orphan")
    subscription_services: Mapped[List["SubscriptionService"]] = relationship(back_populates="service", cascade="all, delete-orphan")
    subscription_usages: Mapped[List["SubscriptionUsage"]] = relationship(back_populates="service", cascade="all, delete-orphan")


class Employee(Base):
    """Сотрудники (мастера, администраторы, механики, специалисты)"""
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    specialization: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # Не unique - один человек может работать в нескольких бизнесах
    # Подтверждённый email мастера для входа на сайт (email-пароль или Яндекс ID с этим адресом).
    access_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    booking_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Индивидуальный шаг записи
    role: Mapped[str] = mapped_column(String(20), default="master")  # master или administrator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Venue rental fields
    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Вместимость (чел.)
    area_sqm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Площадь (м²)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Описание зала
    min_booking_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Минимальное время бронирования
    solo_price_per_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Спецтариф соло (₽/час)
    photos: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON список file_id фото
    primary_photo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # выбранный file_id главного фото
    video: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # file_id видео
    on_duty_date: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)  # Дата дежурства
    duty_schedule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: ["2026-03-25","2026-03-26",...] — график дежурств
    ai_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI-инструкция
    services_text: Mapped[Optional[str]] = mapped_column("services", Text, nullable=True)  # JSON услуги (колонка "services", атрибут services_text чтобы не конфликтовать с relationship)

    # Связи
    business: Mapped["Business"] = relationship(back_populates="employees")
    bookings: Mapped[List["Booking"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    days_off: Mapped[List["EmployeeDayOff"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    employee_services: Mapped[List["EmployeeService"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    time_pricing: Mapped[List["VenueTimePricing"]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class VenueTimePricing(Base):
    """Тарифы аренды помещений по времени суток"""
    __tablename__ = "venue_time_pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    time_from: Mapped[datetime.time] = mapped_column(Time)
    time_to: Mapped[datetime.time] = mapped_column(Time)
    price_per_hour: Mapped[int] = mapped_column(Integer)  # Цена за час
    day_type: Mapped[str] = mapped_column(String(20), default="all")  # all, weekday, weekend

    employee: Mapped["Employee"] = relationship(back_populates="time_pricing")


class Booking(Base):
    """Записи клиентов"""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)

    booking_date: Mapped[datetime.date] = mapped_column(Date)
    booking_time: Mapped[datetime.time] = mapped_column(Time)

    # Статусы: pending, confirmed, cancelled, completed,
    # pending_prepayment (ожидает предоплату), prepayment_sent (клиент отправил), prepayment_confirmed (владелец подтвердил)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Rental bookings have a per-booking duration/quote; ordinary services use service duration/price.
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Immutable snapshot used for resource occupancy. Unlike duration_minutes,
    # this is populated for ordinary services too and must not be used to
    # detect rental bookings.
    resource_duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    occupies_resource: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    estimated_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Предоплата
    prepayment_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Сумма предоплаты
    prepayment_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    prepayment_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Когда подтверждена

    # Доказательство принятия юридических документов в момент записи.
    # Согласие на ПД и условия записи хранятся раздельно.
    personal_data_consent_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    personal_data_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    booking_terms_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    booking_terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Когда запись реально закончили (раньше плановой длительности). Нужен только для показа
    # в расписании; освобождает время сам статус completed. UTC, без часового пояса.
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Deprecated, используем reminder_24h/2h
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Клиенту за 24ч
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Клиенту за 2ч
    staff_reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Владельцу/мастеру за 24ч
    staff_reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Владельцу/мастеру за 2ч
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    version: Mapped[int] = mapped_column(Integer, default=1)  # Для оптимистичной блокировки

    # Deep-link secret для подключения анонимного web-клиента к боту:
    # фронт показывает t.me/<bot>?start=notify_<token>, при /start клиент привязывается.
    notify_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Связь с использованием абонемента (если запись оплачена по абонементу)
    subscription_usage_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscription_usages.id", ondelete="SET NULL"), nullable=True)

    # Связи
    business: Mapped["Business"] = relationship(back_populates="bookings")
    client: Mapped["User"] = relationship(back_populates="bookings")
    service: Mapped["Service"] = relationship(back_populates="bookings")
    employee: Mapped["Employee"] = relationship(back_populates="bookings")
    subscription_usage: Mapped[Optional["SubscriptionUsage"]] = relationship(
        foreign_keys="[Booking.subscription_usage_id]",
        uselist=False
    )



class SeoBookingAttribution(Base):
    """One verified article referral per booking visit, never per service row.

    Created only when a valid signed token came from a published ServGo article.
    No client contact data; status is read from the original Booking row.
    """
    __tablename__ = "seo_booking_attributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    article_slug: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class EmployeeDayOff(Base):
    """Выходные дни сотрудников"""
    __tablename__ = "employee_days_off"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    date: Mapped[datetime.date] = mapped_column(Date)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Причина (отпуск, больничный и т.д.)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Связи
    employee: Mapped["Employee"] = relationship(back_populates="days_off")


class EmployeeService(Base):
    """Связь между сотрудниками и услугами (какие услуги может делать мастер)"""
    __tablename__ = "employee_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Связи
    employee: Mapped["Employee"] = relationship(back_populates="employee_services")
    service: Mapped["Service"] = relationship(back_populates="employees")


class Subscription(Base):
    """Шаблон абонемента (создается владельцем бизнеса)"""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer)  # Цена абонемента
    validity_days: Mapped[int] = mapped_column(Integer)  # Срок действия в днях
    rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Правила применения
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ai_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI-инструкция

    # Связи
    business: Mapped["Business"] = relationship(back_populates="subscriptions")
    services: Mapped[List["SubscriptionService"]] = relationship(back_populates="subscription", cascade="all, delete-orphan")
    client_subscriptions: Mapped[List["ClientSubscription"]] = relationship(back_populates="subscription", cascade="all, delete-orphan")


class SubscriptionService(Base):
    """Услуги, включенные в абонемент"""
    __tablename__ = "subscription_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # Количество этой услуги в абонементе

    # Связи
    subscription: Mapped["Subscription"] = relationship(back_populates="services")
    service: Mapped["Service"] = relationship(back_populates="subscription_services")


class ClientSubscription(Base):
    """Купленный абонемент клиента"""
    __tablename__ = "client_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))

    purchased_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # Дата истечения

    # Статусы: pending_payment, payment_sent, active, expired, depleted
    status: Mapped[str] = mapped_column(String(50), default="pending_payment")
    payment_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Когда владелец подтвердил оплату
    payment_receipt: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # file_id чека (фото/документ)
    expiry_reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # Напоминание о скором истечении

    # Связи
    subscription: Mapped["Subscription"] = relationship(back_populates="client_subscriptions")
    client: Mapped["User"] = relationship(back_populates="client_subscriptions")
    business: Mapped["Business"] = relationship(back_populates="client_subscriptions")
    usages: Mapped[List["SubscriptionUsage"]] = relationship(back_populates="client_subscription", cascade="all, delete-orphan")


class SubscriptionUsage(Base):
    """Использование услуги из абонемента"""
    __tablename__ = "subscription_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_subscription_id: Mapped[int] = mapped_column(ForeignKey("client_subscriptions.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    booking_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Связи
    client_subscription: Mapped["ClientSubscription"] = relationship(back_populates="usages")
    service: Mapped["Service"] = relationship(back_populates="subscription_usages")
    booking: Mapped[Optional["Booking"]] = relationship(
        foreign_keys="[SubscriptionUsage.booking_id]",
        uselist=False
    )


class AuthPhoneCode(Base):
    """Одноразовые SMS-коды для phone-login.
    Заменяет in-memory _pending_codes в web/api/routes/auth.py — переживает рестарты API."""
    __tablename__ = "auth_phone_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
