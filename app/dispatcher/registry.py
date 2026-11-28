"""Everything the dispatcher can do, described once.

The model picks from this list and never invents an action. A new ability is a new entry here
plus an executor in `executors.py`. Risk and cost live here, and the policy reads them in code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dispatcher.policy import Cost
from app.tools import ToolOperation

POST_CHANNELS = ("telegram", "vk", "dzen", "vc_ru", "tenchat")
CHANNEL_LABELS = {"telegram": "Telegram", "vk": "ВКонтакте", "dzen": "Дзен", "vc_ru": "vc.ru", "tenchat": "TenChat"}

# Where things live, so "where is X" answers with a real button. {p} is the company id.
PAGES = {
    "create": ("/studio/products/{p}/create", "Создать материал"),
    "work": ("/studio/products/{p}/work", "В работе"),
    "review": ("/studio/products/{p}/review", "На согласовании"),
    "plan": ("/studio/products/{p}/plan", "Контент-план"),
    "calendar": ("/studio/products/{p}/calendar", "Календарь"),
    "materials": ("/studio/products/{p}/materials", "Мои материалы"),
    "channels": ("/studio/products/{p}/channels", "Публикации"),
    "whiteboard": ("/studio/products/{p}/whiteboard", "Рисованное видео"),
    "analytics": ("/studio/products/{p}/analytics", "Результаты"),
    "comments": ("/studio/products/{p}/comments", "Комментарии"),
    "profile": ("/studio/products/{p}/profile", "Профиль компании"),
    "team": ("/studio/team", "ИИ-команда"),
    "schedule": ("https://servgo.ru/admin/schedule", "Расписание записи в ServGo"),
    "admin": ("https://servgo.ru/admin", "Админка ServGo"),
    "seo": ("/studio/products/{p}/seo", "SEO-завод"),
    "seo_queue": ("/studio/products/{p}/seo/queue", "Очередь SEO-статей"),
    "seo_metrics": ("/studio/products/{p}/seo/search-metrics", "Поисковые показатели"),
    "campaigns": ("/studio/products/{p}/campaigns", "Кампании"),
    "market_research": ("/studio/products/{p}/market-research", "Поиск организаций и конкурентов"),
    "brand_kit": ("/studio/products/{p}/brand-kit", "Бренд-кит"),
    "brand_library": ("/studio/products/{p}/brand-kit/library", "Библиотека дизайна"),
    "vk": ("/studio/products/{p}/vk", "Публикации ВКонтакте"),
    "vk_settings": ("/studio/products/{p}/vk/settings", "Настройки ВКонтакте"),
    "ai_chat": ("/studio/chat", "ИИ-чат"),
    "accounts": ("/studio/products/{p}/accounts", "Доступы компании"),
}
PageKey = Literal["create", "work", "review", "plan", "calendar", "materials", "channels", "whiteboard",
                  "analytics", "comments", "profile", "team", "schedule", "admin", "seo", "seo_queue",
                  "seo_metrics", "campaigns", "market_research", "brand_kit", "brand_library", "vk",
                  "vk_settings", "ai_chat", "accounts"]


def page_href(key: str, product_id: UUID | str) -> str:
    return PAGES[key][0].format(p=product_id)


def page_label(key: str) -> str:
    return PAGES[key][1]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoParams(_Params):
    pass


class PostParams(_Params):
    topic: str = Field(min_length=3, max_length=300)
    channel: Literal["telegram", "vk", "dzen", "vc_ru", "tenchat"] = "telegram"
    audience: str | None = Field(default=None, max_length=300)
    editorial_brief: str | None = Field(default=None, max_length=3500)
    image_model: Literal["none", "auto", "gemini", "openai"] = "none"
    image_ratio: Literal["4:5", "1:1"] = "4:5"

    @model_validator(mode="after")
    def vk_image_only(self):
        if self.image_model != "none" and self.channel != "vk":
            raise ValueError("Изображение в быстром посте пока доступно только для ВКонтакте")
        return self


class CreativeParams(_Params):
    topic: str = Field(min_length=3, max_length=300, description="Тема материала. Если тема не названа, сначала спроси её или предложи темы.")
    format: Literal["story", "short_video", "static_image"] = Field(description="Stories, видеосценарий или одиночный визуальный бриф.")
    platform: Literal["telegram", "vk", "instagram_reels", "youtube_shorts", "vk_clips", "dzen", "vc_ru", "tenchat"] | None = Field(default=None, description="Если не указана, автоматически выбирается подходящая площадка по формату.")
    frame_count: int | None = Field(default=None, ge=3, le=10, description="Строгое число экранов Stories, если владелец его назвал. Например, четыре — 4. Для других форматов не указывать.")
    audience: str | None = Field(default=None, max_length=300)
    editorial_brief: str | None = Field(default=None, max_length=3500)
    image_model: Literal["none", "auto", "gemini", "openai"] | None = None
    aspect_ratio: Literal["9:16", "4:5", "1:1", "16:9"] | None = None

    @model_validator(mode="after")
    def image_format(self):
        if self.image_model not in (None, "none") and self.format == "short_video":
            raise ValueError("Images only apply to Stories and image formats")
        if self.aspect_ratio is not None and self.format not in {"story", "static_image"}:
            raise ValueError("Aspect ratio only applies to images")
        if self.format == "story" and self.aspect_ratio not in (None, "9:16"):
            raise ValueError("Stories must be vertical 9:16")
        return self


class PackageParams(_Params):
    topic: str = Field(min_length=3, max_length=300, description="Тема полного контент-пакета. Если не названа, сначала уточни или предложи тему.")
    audience: str | None = Field(default=None, max_length=300)
    editorial_brief: str | None = Field(default=None, max_length=3500)


class VideoSettings(_Params):
    length: Literal["30", "60"] = Field(
        default="30", description="Длина видео: \"30\" — около 30 секунд (по умолчанию); \"60\" — около минуты, "
                                  "если владелец просит минутное, длинное или более подробное видео.")
    call_to_action: str | None = Field(
        default=None, min_length=6, max_length=200,
        description="Призыв в конце видео, только если владелец сам его назвал, его словами. "
                    "Не назвал — не указывай: видео позовёт подписаться на канал и поставить лайк.")

    profile: Literal["configured", "gemini_ru", "fish", "grok"] = "gemini_ru"
    image_model: Literal["gemini-3.1-flash-image", "gpt-image-2.5-sunburst"] = "gemini-3.1-flash-image"
    visual_review: bool = False
    science_mode: Literal["general", "auto", "scientific"] = "auto"
    max_api_calls: Literal[19, 25, 34, 45] | None = None
    @field_validator("max_api_calls", mode="before")
    @classmethod
    def parse_call_limit(cls, value):
        if isinstance(value, str) and value in {"19", "25", "34", "45"}:
            return int(value)
        return value

    recovery_budget: bool = False
    source_script: str = Field(default="", max_length=12000)
    background_notes: str = Field(default="", max_length=6000)
    reference_materials: str = Field(default="", max_length=6000)


class VideoParams(VideoSettings):
    topic: str = Field(min_length=8, max_length=400)


class GoToParams(_Params):
    target: PageKey


class TaskListParams(_Params):
    tab: Literal["all", "active", "approval", "ready", "problems"] = "all"
    kind: Literal["all", "post", "article", "creative", "video", "plan", "edit", "seo", "business"] = "all"
    query: str = Field(default="", max_length=120)


class TaskDetailParams(_Params):
    task_id: UUID


class ResumeVideoParams(_Params):
    task_id: UUID
    extra_calls: int = Field(default=0, ge=0, le=10)


class RedrawPostImageParams(_Params):
    material_id: UUID
    feedback: str = Field(default="", max_length=700)


class RepairVideoParams(_Params):
    task_id: UUID
    image_model: Literal["gemini-3.1-flash-image", "gpt-image-2.5-sunburst"] | None = None


class CancelVideoParams(_Params):
    task_id: str | None = Field(default=None, max_length=64)


class ScheduleParams(_Params):
    # bookings — список записей; free — свободные окна; load — загрузка мастеров; next — кто следующий.
    view: Literal["bookings", "free", "load", "next"] = "bookings"
    period: Literal["today", "tomorrow", "week"] = "today"


INSIGHT_TOPICS = ("all", "cancellations", "money", "clients", "weekdays", "daytime", "services", "upcoming")


class InsightsParams(_Params):
    topic: Literal["all", "cancellations", "money", "clients", "weekdays", "daytime", "services", "upcoming"] = Field(
        default="all",
        description="О чём спросил владелец: cancellations — отмены; money — расчётная стоимость записей, не выручка, фактические оплаты неизвестны; "
                    "clients — клиенты, возвраты, постоянные; weekdays — по каким дням недели больше или меньше записей, "
                    "пустые дни; daytime — утро, день, вечер и за сколько дней записываются; services — какие услуги и "
                    "какие яхты, мастера, залы берут; upcoming — сколько уже записано вперёд; all — общий вопрос про "
                    "дела бизнеса или почему мало клиентов.")
    days: int | None = Field(
        default=None, ge=7, le=365,
        description="За сколько последних дней смотреть. Не указывай, если владелец не просил другой срок: "
                    "возьмётся сохранённая глубина (по умолчанию 30 дней).")


class DepthParams(_Params):
    days: int = Field(ge=7, le=365, description="Сколько последних дней анализировать: 30 — месяц, 90 — квартал, 7 — неделя.")


class ReturnCandidatesParams(_Params):
    days: int = Field(default=180, ge=30, le=365,
                      description="За сколько последних дней изучить выполненные визиты. По умолчанию 180 дней.")


class FillOpenSlotsParams(_Params):
    service_id: int | None = Field(default=None, ge=1,
                                   description="ID услуги только из подтверждённого каталога BookingBot. "
                                               "Не знаете ID — не указывайте, система покажет выбор услуг.")
    period: Literal["today", "tomorrow", "week"] = "tomorrow"
    page: int = Field(default=1, ge=1, description="Страница каталога услуг.")


class SlotOfferParams(_Params):
    service_id: int = Field(ge=1)
    resource_id: int = Field(ge=1)
    day: date
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    channel: Literal["telegram", "vk", "dzen", "vc_ru", "tenchat"] = "telegram"




class MaterialParams(_Params):
    # Без material_id берётся самый свежий материал, который ждёт решения.
    material_id: str | None = Field(default=None, max_length=64)


class ReviseParams(_Params):
    material_id: str | None = Field(default=None, max_length=64)
    feedback: str = Field(min_length=8, max_length=1800)
    source_version: int | None = Field(default=None, ge=1)
    fragment: str | None = Field(default=None, min_length=2, max_length=380)


class SchedulePostParams(_Params):
    post_id: str | None = Field(default=None, max_length=64)
    # Московское время: «ГГГГ-ММ-ДД ЧЧ:ММ».
    when: str = Field(min_length=10, max_length=25)
    channel_id: str | None = Field(default=None, max_length=64)


class VkScheduleParams(_Params):
    post_id: UUID
    when: str = Field(min_length=10, max_length=25)


class PublicationListParams(_Params):
    channel: Literal["all", "telegram", "vk"] = "all"
    status: Literal["all", "scheduled", "sending", "published", "problems", "cancelled"] = "all"


class PublicationParams(_Params):
    publication_id: UUID


class MovePublicationParams(PublicationParams):
    when: str = Field(min_length=10, max_length=25)


class SeoFactoryParams(_Params):
    horizon_days: Literal[7, 30] = Field(
        default=7, description="Период SEO-контент-плана: 7 дней, или 30 дней, если владелец сказал «на месяц».")
    focus: str | None = Field(
        default=None, min_length=3, max_length=300,
        description="О чём или для кого строить поисковые статьи, только если владелец сам это назвал, его словами. "
                    "Не назвал — не указывай: запросы строятся по профилю компании.")


class PlanPackageEditParams(_Params):
    package_key: str = Field(min_length=1, max_length=80,
                             description="Точный package_key из editable_plan.draft_packages в контексте. Не выдумывай.")
    topic: str | None = Field(default=None, min_length=3, max_length=300,
                              description="Новая тема пакета; не указывай, если менять её не просили.")
    objective: str | None = Field(default=None, min_length=3, max_length=600,
                                  description="Новая цель материала, только по просьбе владельца.")
    audience: str | None = Field(default=None, min_length=3, max_length=300,
                                 description="Новая аудитория, только по просьбе владельца.")

    @model_validator(mode="after")
    def has_changes(self):
        if all(value is None for value in (self.topic, self.objective, self.audience)):
            raise ValueError("Укажите хотя бы одно изменение пакета")
        return self


class PlanSlotMoveParams(_Params):
    slot_id: str = Field(min_length=1, max_length=100,
                         description="Точный slot_id из editable_plan.calendar_slots. Не выдумывай.")
    when: str = Field(pattern=r"^\d{4}-\d{2}-\d{2} [0-2]\d:[0-5]\d$",
                      description="Новая местная дата и время в часовом поясе плана: YYYY-MM-DD HH:MM.")


class RestrictedParams(_Params):
    kind: Literal["budget", "pricing", "access", "delete", "credentials", "billing"]


# What each blocked request is, for the operation the policy evaluates.
RESTRICTED_OPERATIONS = {
    "budget": ToolOperation.INCREASE_BUDGET,
    "pricing": ToolOperation.CHANGE_PRICING,
    "access": ToolOperation.CHANGE_ACCESS,
    "delete": ToolOperation.DESTRUCTIVE_OPERATION,
    "credentials": ToolOperation.CHANGE_CREDENTIALS,
    "billing": ToolOperation.CHANGE_BILLING,
}


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    # For the model: when to pick it. Plain facts, no instructions about other rules.
    description: str
    operation: ToolOperation
    cost: Cost
    params: type[_Params]
    # Shown on the card and spoken before a confirmation.
    consequence: str = ""
    # Ask first even when risk and cost alone would allow running at once (owner's table, plan section 3.1).
    always_confirm: bool = False


CAPABILITIES: dict[str, Capability] = {c.id: c for c in (
    Capability("recommend_today", "Что сделать сегодня",
               "Дать владельцу конкретные следующие шаги по текущим фактам платформы: "
               "готовые материалы, проблемы, задания, контент-план и проверка бизнес-показателей. "
               "Для «что порекомендуешь сегодня сделать для бизнеса», «с чего начать маркетинг» и подобных просьб. "
               "Ничего не запускает без нажатия владельца.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("show_recent_videos", "Мои последние видео",
               "Показать список созданных и запущенных видео со статусами и ссылками: "
               "«покажи последние видео», «какие видео готовы», «где недавно созданные ролики». "
               "Это просмотр результата, не запуск нового видео.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("list_tasks", "Найти задания",
               "Показать задания выбранной компании по статусу и типу: ошибки/отмены problems, выполняемые active, "
               "на проверке approval, завершённые ready, все all; kind=video для видео, seo для SEO, article для статей. "
               "query — явно указанная тема или часть названия. Для «почему видео не готово», «что сломалось», "
               "«найди задание»; показывает реальные причины, если известны, и кнопки открытия.",
               ToolOperation.ANALYTICS, Cost.NONE, TaskListParams),
    Capability("resume_video", "Продолжить остановленное видео",
               "Возобновить именно существующее неудачное рисованное видео с сохранённых этапов. "
               "task_id из recent_tasks; extra_calls по умолчанию 0, больше только если владелец явно назвал число "
               "дополнительных обращений. Платное подтверждение обязательно; не создавать новое видео.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID, ResumeVideoParams),
    Capability("retry_content_task", "Повторить остановленное задание",
               "Повторить неудачную или отменённую генерацию поста, статьи, креатива или редактуру. "
               "task_id из recent_tasks. Для рисованного видео использовать resume_video, не этот инструмент. "
               "Повтор может потребовать платных вызовов; сначала подтверждение.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID, TaskDetailParams),
    Capability("redraw_post_image", "Перегенерировать картинку поста ВКонтакте",
               "Переделать только картинку существующего поста VK до утверждения, не менять текст. "
               "material_id из recent_materials или выбранной карточки; feedback — пожелание к картинке. "
               "Использует модель исходного поста, платный запуск после подтверждения.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID, RedrawPostImageParams),
    Capability("show_task", "Проверить конкретное задание",
               "Показать статус, этап и известную причину остановки конкретного задания. task_id только из recent_tasks "
               "или явно выбранного задания; если не знаешь ID, сначала list_tasks.",
               ToolOperation.ANALYTICS, Cost.NONE, TaskDetailParams),
    Capability("repair_video_images", "Перерисовать видео без новой озвучки",
               "Подготовить визуальную перегенерацию завершённого исходного рисованного видео с сохранением аудио и сценария. "
               "task_id только из recent_tasks или явно выбранного задания. Если исходник неясен, найди видео через list_tasks; "
               "image_model необязательна, по умолчанию модель исходника. Платный запуск отдельно подтверждается.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID_LATER, RepairVideoParams),
    Capability("show_work", "Что в работе",
               "Показать, какие задания сейчас выполняются, ждут решения или завершились.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("show_review", "Что ждёт проверки",
               "Показать материалы и видео, которые готовы и ждут решения владельца.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("show_plan", "Показать контент-план",
               "Показать текущий контент-план: статус и ближайшие публикации.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("go_to", "Где это находится",
               "Подсказать, где на платформе нужная страница, и дать кнопку-ссылку. "
               "Используй карту разделов platform_guide: для неподключённого действия объясни шаги, "
               "не утверждай, что оно выполнено. target — один из: " + ", ".join(PAGES) + ".",
               ToolOperation.ANALYTICS, Cost.NONE, GoToParams),
    Capability("show_schedule", "Расписание записей",
               "Записи клиентов онлайн-записи, свободные окна, загрузка мастеров, кто следующий клиент. "
               "view — bookings (список записей), free (свободные окна), load (загрузка), next (кто следующий); "
               "period — today, tomorrow или week (ближайшие 7 дней).",
               ToolOperation.ANALYTICS, Cost.NONE, ScheduleParams),
    Capability("show_insights", "Итоги бизнеса по записям",
               "Разбор записей за период: количество, отмены, расчётная стоимость услуг (не фактическая выручка), "
               "вернувшиеся клиенты, самые слабые дни и время, за сколько дней записываются, чем чаще берут, "
               "сколько уже записано вперёд. Подходит для вопросов «как дела в бизнесе», «почему мало клиентов», "
               "«как увеличить число клиентов», «что улучшить», «сколько заработали», «какие дни пустые», "
               "«как часто возвращаются клиенты». topic — о чём именно вопрос (на узкий вопрос — узкий ответ); days — за сколько дней, если владелец назвал срок.",
               ToolOperation.ANALYTICS, Cost.NONE, InsightsParams),
    Capability("fill_open_slots", "Подобрать клиентов под свободные часы",
               "Для «чем заполнить завтра», «найди клиентов под свободные окна», «что делать с пустыми слотами»: "
               "показывает услуги, реальные возможные времена начала записи и клиентов с выполненными "
               "посещениями этой услуги без ближайшей записи. Никакие сообщения не отправляет. "
               "Без service_id предлагает выбрать услугу.",
               ToolOperation.ANALYTICS, Cost.NONE, FillOpenSlotsParams),
    Capability("prepare_slot_offer", "Подготовить маркетинговый пост для свободного времени",
               "Передать маркетинговой команде подготовку черновика объявления о конкретной свободной записи. "
               "Время перед постановкой задачи перепроверяется. Клиентские данные не передаются команде, "
               "рассылка не запускается, публикации нет. Использовать только после выбора конкретного времени.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, SlotOfferParams),
    Capability("show_return_candidates", "Кого пригласить на повторный визит",
               "Список клиентов из CRM записей с минимум тремя выполненными визитами, "
               "которые выходят за свой прежний интервал повторных посещений и не записаны "
               "на ближайшие 14 дней. Отчёт только для владельца, сообщений клиентам не отправляет. "
               "Подходит для «кто давно не приходил», «кому предложить вернуться», "
               "«покажи клиентов для повторного приглашения».",
               ToolOperation.ANALYTICS, Cost.NONE, ReturnCandidatesParams),
    Capability("set_insights_depth", "Глубина анализа",
               "Изменить, за сколько последних дней смотреть записи при разборе бизнеса (от 7 до 365 дней). "
               "Например: «смотри за квартал» — 90, «за полгода» — 180.",
               ToolOperation.ANALYTICS, Cost.NONE, DepthParams),
    Capability("send_to_team", "Передать данные команде",
               "Передать цифры бизнеса по записям маркетинг-команде на разбор: она вернёт решения, что оставить, "
               "остановить, усилить и проверить, со ссылками на цифры. Ничего не публикует и не тратит на рекламу.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, NoParams),
    Capability("create_post", "Создать пост",
               "Поставить в работу один пост на тему для площадки (по умолчанию Telegram). "
               "Нужна тема; площадка — telegram, vk, dzen, vc_ru или tenchat; аудитория необязательна.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, PostParams, always_confirm=True),
    Capability("create_creative", "Создать Stories или видеосценарий",
               "По просьбе «сделай 4 сторис», «создай пять историй», «подготовь сценарий Reels», "
               "«сделай картинку», «пост с картинкой», «обложка к посту» создать креатив. "
               "Если нужен пост вместе с изображением — create_creative/static_image, caption — текст поста. "
               "format=story — Stories; short_video — только "
               "сценарий ролика, НЕ MP4; static_image — готовая картинка PNG и подпись. "
               "Stories 9:16 вертикальные; картинка для Instagram 4:5, для Telegram/VK/Dzen 1:1 "
               "по умолчанию. Явные предпочтения сторон передавай как aspect_ratio. "
               "Сохраняй точное число экранов от 3 до 10 в frame_count, если оно названо. "
               "Для Stories платформы telegram/vk/instagram_reels; для сценария youtube_shorts/instagram_reels/vk_clips; "
               "для изображения telegram/vk/dzen/vc_ru/tenchat/instagram_reels. Если тема не названа — уточни, не выдумывай. "
               "Перед запуском показывай пользователю формат, число экранов и тему.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, CreativeParams, always_confirm=True),
    Capability("create_package", "Создать полный пакет",
               "Подготовить по одной теме статью и адаптации для разных площадок, креативные брифы "
               "и видеосценарии. Готовый MP4 пока не входит. Если тема не названа, уточни её. "
               "Обязательно показать подтверждение перед запуском.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, PackageParams, always_confirm=True),
    Capability("make_plan", "Составить контент-план",
               "Поставить в работу составление нового контент-плана. Он ничего не публикует: план утверждается отдельно.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, NoParams),
    Capability("seo_factory", "SEO-завод",
               "Запустить SEO-завод: собрать реальные поисковые запросы клиентов (подсказки Яндекса и Google), "
               "составить контент-план статей под воронку и поставить статьи в работу. Готовые статьи после проверки "
               "критиком сами выходят в блог на servgo.ru. Подходит для «сделай SEO-план», «запусти SEO-завод», "
               "«хочу, чтобы нас находили в поиске и через ИИ», «статьи для блога под запросы клиентов», "
               "«подготовь SEO на месяц». Период — 7 или 30 дней; если месяц не указан, 7. "
               "Тратит много запросов к ИИ, поэтому всегда сначала просит подтверждение.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID, SeoFactoryParams, always_confirm=True),
    Capability("edit_plan_package", "Изменить тему чернового контент-плана",
               "Редактировать существующий пакет в НЕУТВЕРЖДЁННОМ контент-плане: тему, цель или аудиторию. "
               "Используй package_key только из editable_plan.draft_packages контекста. "
               "При неизвестном пакете сначала попроси уточнить и покажи план. "
               "Перед сохранением покажи сравнение и запроси подтверждение.",
               ToolOperation.GENERATE_DRAFT, Cost.NONE, PlanPackageEditParams, always_confirm=True),
    Capability("move_plan_slot", "Перенести публикацию в календаре",
               "Изменить время ОДНОГО слота плана без регенерации и публикации. "
               "Используй slot_id только из editable_plan.calendar_slots контекста, "
               "а when — точную локальную дату YYYY-MM-DD HH:MM в часовом поясе плана. "
               "Не угадывай номер слота и дату. Покажи старую и новую дату до подтверждения.",
               ToolOperation.PUBLISH, Cost.NONE, PlanSlotMoveParams, always_confirm=True),
    Capability("approve_plan", "Утвердить контент-план",
               "Утвердить готовый черновик плана, чтобы команда начала готовить материалы по нему.",
               ToolOperation.PUBLISH, Cost.NONE, NoParams,
               consequence="После утверждения команда начнёт готовить материалы по плану."),
    Capability("show_material", "Показать готовый материал",
               "Показать готовый пост, статью или видео, которые ждут решения владельца, с кнопкой «Утвердить». "
               "Без material_id — самый свежий.",
               ToolOperation.ANALYTICS, Cost.NONE, MaterialParams),
    Capability("approve_material", "Утвердить материал",
               "Утвердить готовый материал, который ждёт решения. Сам он нигде не публикуется. "
               "Без material_id — самый свежий.",
               ToolOperation.PUBLISH, Cost.NONE, MaterialParams,
               consequence="После утверждения материал можно планировать к публикации. Сам он нигде не публикуется."),
    Capability("revise_material", "Доработать пост",
               "Отправить готовый пост на доработку. feedback — что именно изменить (от 8 символов). "
               "Без material_id — самый свежий пост на проверке.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, ReviseParams,
               consequence="Команда перепишет пост по вашей правке. Новая версия снова придёт вам на проверку.",
               always_confirm=True),
    Capability("list_publications", "Проверить публикации",
               "Реальные статусы публикаций Telegram и VK: запланированы, отправляются, опубликованы, отменены, проблемы. "
               "При вопросе «что опубликовано», «почему не вышел пост», «что запланировано» выбери фильтры.",
               ToolOperation.ANALYTICS, Cost.NONE, PublicationListParams),
    Capability("schedule_vk_post", "Запланировать пост ВКонтакте",
               "Назначить утверждённый пост VK в подключённое сообщество. post_id из recent_materials; when — московское время "
               "ГГГГ-ММ-ДД ЧЧ:ММ. Только после подтверждения, с действующими проверками изображения и критика.",
               ToolOperation.PUBLISH, Cost.NONE, VkScheduleParams),
    Capability("cancel_publication", "Отменить назначенную публикацию",
               "Отменить ещё не отправленную публикацию Telegram или VK. publication_id из recent_publications или карточки. "
               "Не удаляет уже опубликованный пост. Требуется подтверждение.",
               ToolOperation.PUBLISH, Cost.NONE, PublicationParams),
    Capability("move_publication", "Перенести назначенную публикацию",
               "Перенести ещё не отправленную самостоятельную публикацию Telegram или VK. "
               "publication_id из recent_publications, when — московское время ГГГГ-ММ-ДД ЧЧ:ММ. "
               "Для не назначенного слота контент-плана использовать move_plan_slot. Требуется подтверждение.",
               ToolOperation.PUBLISH, Cost.NONE, MovePublicationParams),
    Capability("schedule_post", "Запланировать публикацию в Telegram",
               "Запланировать утверждённый пост в подключённый Telegram-канал. when — московское время "
               "«ГГГГ-ММ-ДД ЧЧ:ММ» (сегодняшняя дата есть в контексте). Без post_id — последний утверждённый пост.",
               ToolOperation.PUBLISH, Cost.NONE, SchedulePostParams,
               consequence="Пост уйдёт в канал в назначенное время."),
    Capability("whiteboard_video", "Рисованное видео",
               "Создать итоговый рисованный/объясняющий видеоролик по теме: около 30 секунд или около минуты. "
               "Подходит для обычных просьб создать видео, включая «минутное видео на тему…», даже без слова рисованное. "
               "Если нужен иной несовместимый вид видео, предложить рисованный вариант и уточнить согласие. "
               "Не использовать для сценария, просмотра, отмены или изменения существующего видео. "
               "Платная генерация начнётся только после подтверждения.",
               ToolOperation.GENERATE_DRAFT, Cost.PAID_LATER, VideoParams),
    Capability("cancel_video", "Отменить рисованное видео",
               "Отменить задание на рисованное видео, которое ещё не завершено. Без task_id — самое последнее.",
               ToolOperation.GENERATE_DRAFT, Cost.NONE, CancelVideoParams),
    Capability("restricted_action", "Действие под запретом",
               "Просьба менять бюджет, цены, доступы, платёжные данные, ключи или удалять данные. "
               "kind — budget, pricing, access, delete, credentials или billing.",
               ToolOperation.DESTRUCTIVE_OPERATION, Cost.NONE, RestrictedParams),
)}


def catalog_for_model() -> list[dict]:
    """The list sent to the model: ids, meaning and parameter shapes. No policy details."""
    return [{"id": c.id, "title": c.title, "description": c.description,
             "params": c.params.model_json_schema().get("properties", {}),
             "required": c.params.model_json_schema().get("required", [])}
            for c in CAPABILITIES.values()]


# Navigation-only guidance; these are NOT callable actions. All targets are
# fixed company-scoped pages (except the authenticated AI chat / team pages).
PLATFORM_GUIDE = {
    "create": "Создание статьи, поста, Stories, картинки, видеосценария и пакета; для одиночной статьи выберите формат и тему.",
    "work": "Найти задание, открыть его статус, повторить остановленную генерацию при наличии кнопки.",
    "review": "Проверка материалов, утверждение, доработка и перегенерация изображения поста.",
    "whiteboard": "Видео: открыть конкретное задание, продолжить с сохранённых этапов, подтвердить доступный лимит.",
    "plan": "Контент-план: редактирование пакетов, утверждение, приостановка и постановка пакета в работу.",
    "calendar": "Календарь публикаций: посмотреть слоты и изменить время.",
    "materials": "Архив материалов, открыть и отредактировать конкретный материал.",
    "channels": "Подключённые каналы, запланированные публикации, отмена или сверка публикации.",
    "vk": "Публикации ВКонтакте, планирование и отмена.",
    "vk_settings": "Настроить интеграцию ВКонтакте; секреты вводятся только в защищённой форме.",
    "seo": "SEO-завод: состояние, активация, настройки и управление статьями.",
    "seo_queue": "Очередь SEO: посмотреть задания, повторить или отменить доступные операции.",
    "seo_metrics": "Поисковые показатели и проверка подключённых источников.",
    "campaigns": "Маркетинговые эксперименты: создать, настроить, запустить, остановить, измерить результат.",
    "market_research": "Поиск организаций через Яндекс Карты: указать нишу и город, выбрать и сохранить источники исследования. Не обещать найденные email или рассылку.",
    "brand_kit": "Фирменный стиль: версии, утверждение и оформление.",
    "brand_library": "Библиотека фонов и готовых дизайнов.",
    "comments": "Комментарии: подключить канал, настроить режим, открыть и проверить ответ.",
    "analytics": "Результаты маркетинга и аналитика компании.",
    "profile": "Профиль компании и сведения о бизнесе.",
    "accounts": "Управление аккаунтами и доступами через защищённую форму; не запрашивать секреты в чате.",
    "team": "ИИ-команда; настройка ролей и моделей доступна при соответствующих полномочиях.",
    "ai_chat": "Отдельный ИИ-чат и специалисты; выбор модели, диалоги и файлы.",
    "schedule": "Онлайн-запись: расписание клиентов и мастеров.",
    "admin": "Админка онлайн-записи; возможности зависят от роли пользователя.",
}
