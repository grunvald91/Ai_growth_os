"""Everything the dispatcher can do, described once.

The model picks from this list and never invents an action. A new ability is a new entry here
plus an executor in `executors.py`. Risk and cost live here, and the policy reads them in code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
}
PageKey = Literal["create", "work", "review", "plan", "calendar", "materials", "channels", "whiteboard",
                  "analytics", "comments", "profile", "team", "schedule", "admin"]


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


class VideoParams(_Params):
    topic: str = Field(min_length=8, max_length=400)


class GoToParams(_Params):
    target: PageKey


class CancelVideoParams(_Params):
    task_id: str | None = Field(default=None, max_length=64)


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


CAPABILITIES: dict[str, Capability] = {c.id: c for c in (
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
               "target — один из: " + ", ".join(PAGES) + ".",
               ToolOperation.ANALYTICS, Cost.NONE, GoToParams),
    Capability("show_schedule", "Расписание записей",
               "Записи клиентов, свободные окна, загрузка мастеров, кто на смене.",
               ToolOperation.ANALYTICS, Cost.NONE, NoParams),
    Capability("create_post", "Создать пост",
               "Поставить в работу один пост на тему для площадки (по умолчанию Telegram). "
               "Нужна тема; площадка — telegram, vk, dzen, vc_ru или tenchat; аудитория необязательна.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, PostParams),
    Capability("make_plan", "Составить контент-план",
               "Поставить в работу составление нового контент-плана. Он ничего не публикует: план утверждается отдельно.",
               ToolOperation.GENERATE_DRAFT, Cost.CHEAP, NoParams),
    Capability("approve_plan", "Утвердить контент-план",
               "Утвердить готовый черновик плана, чтобы команда начала готовить материалы по нему.",
               ToolOperation.PUBLISH, Cost.NONE, NoParams,
               consequence="После утверждения команда начнёт готовить материалы по плану."),
    Capability("whiteboard_video", "Рисованное видео",
               "Подготовить задание на рисованное видео по теме. Платная генерация начнётся только после подтверждения.",
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
             "params": c.params.model_json_schema().get("properties", {})}
            for c in CAPABILITIES.values()]
