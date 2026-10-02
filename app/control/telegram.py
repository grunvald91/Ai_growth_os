from datetime import datetime

from app.control.contracts import ControlInboxItem, ControlStatus, ProductControlStatus


_STATUS_LABELS = {
    "draft": "черновик",
    "running": "идёт",
    "active": "идёт",
    "completed": "завершён",
    "failed": "ошибка",
    "cancelled": "отменён",
    "pending": "ожидает",
    "approved": "одобрено",
    "rejected": "отклонено",
    "published": "опубликовано",
}

_ACTION_LABELS = {
    "start_outreach": "Начать работу с потенциальными клиентами",
    "send_outreach_batch": "Отправить подготовленные обращения",
    "publish": "Опубликовать подготовленный материал",
    "change_pricing": "Изменить цены",
    "delete_data": "Удалить данные",
}

_METRIC_LABELS = {
    "qualified_leads": "заинтересованные потенциальные клиенты",
    "valid_contacts": "проверенные контакты",
    "replies": "ответы",
    "positive_replies": "положительные ответы",
    "demo_requests": "запросы на демонстрацию",
    "attributed_form_submissions": "заявки с подтверждённым источником",
}

_CHANNEL_LABELS = {
    "direct_outreach": "прямые обращения к компаниям",
    "email": "Email",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "website": "сайт или форма обратной связи",
}

_RISK_LABELS = {
    "green": "низкий",
    "yellow": "средний — требуется твоё подтверждение",
    "red": "высокий — требуется дополнительная проверка",
}



def user_text(value: str | None, fallback: str = "не указано") -> str:
    if not value:
        return fallback
    text = str(value)
    replacements = {
        "Direct outreach": "Прямые обращения",
        "direct outreach": "прямые обращения",
        "outreach": "обращения",
        "qualified leads": "заинтересованные потенциальные клиенты",
        "qualified lead": "заинтересованный потенциальный клиент",
        "valid reached businesses": "проверенные компании, с которыми удалось связаться",
        "valid reached": "проверенные контакты",
        "follow-up": "повторное обращение",
        "baseline": "исходные данные",
        "KEEP": "ПРОДОЛЖАЕМ",
        "STOP": "ОСТАНАВЛИВАЕМ",
        "INCONCLUSIVE": "ДАННЫХ НЕДОСТАТОЧНО",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def success_condition_text(value: str | None, experiment_key: str | None = None) -> str:
    raw = value or ""
    if experiment_key == "EXP-SERVGO-001" or (
        ">=3" in raw and "qualified" in raw.lower()
    ):
        return (
            "Не менее 3 заинтересованных потенциальных клиентов "
            "из 30 проверенных компаний, при понятном источнике каждого обращения."
        )
    if raw and any("А" <= char <= "я" or char in "Ёё" for char in raw):
        return user_text(raw)
    return "Условие успеха задано в системе."


def stop_condition_text(value: str | None, experiment_key: str | None = None) -> str:
    raw = value or ""
    if experiment_key == "EXP-SERVGO-001" or (
        "0 qualified" in raw.lower() and "14" in raw
    ):
        return (
            "После работы со всей проверенной выборкой, одного повторного обращения "
            "и 14 дней — ни одного заинтересованного потенциального клиента."
        )
    if raw and any("А" <= char <= "я" or char in "Ёё" for char in raw):
        return user_text(raw)
    return "Условие остановки задано в системе."

def status_label(value: str | None) -> str:
    if not value:
        return "неизвестно"
    return _STATUS_LABELS.get(value.lower(), value)


def action_label(value: str | None) -> str:
    if not value:
        return "Выполнить действие"
    return _ACTION_LABELS.get(value, "Выполнить действие")


def metric_label(value: str | None) -> str:
    if not value:
        return "результат эксперимента"
    return _METRIC_LABELS.get(value, value.replace("_", " "))


def channel_label(value: str | None) -> str:
    if not value:
        return "не указан"
    return _CHANNEL_LABELS.get(value, value)


def risk_label(value: str | None) -> str:
    if not value:
        return "не указан"
    return _RISK_LABELS.get(value.lower(), value)


def _format_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


def render_status(
    status: ControlStatus,
    experiment_stats: dict[str, dict[str, int]] | None = None,
) -> str:
    experiment_stats = experiment_stats or {}
    lines = ["📊 AI-команда — главное"]

    if not status.products:
        lines.extend(["", "Пока нет подключённых проектов."])
        return "\n".join(lines)

    for product in status.products:
        lines.extend(["", f"📌 {product.product_name}"])
        active = next(
            (
                record
                for record in product.experiments
                if record.status in {"running", "active"}
            ),
            None,
        )
        if active is not None:
            key = active.experiment_key or str(active.id)
            lines.append(f"🟢 Сейчас идёт: {user_text(active.name)}")
            stats = experiment_stats.get(str(active.id))
            if stats:
                total = stats.get("total", 0)
                resolved = stats.get("enrichment_resolved", 0)
                ready = stats.get("ready_for_outreach", 0)
                contacted = stats.get("contacted", 0)
                lines.append(f"👥 Компаний в базе: {total}")
                lines.append(f"🔎 Контакты проверены: {resolved}/{total}")
                lines.append(f"✉️ Можно написать напрямую: {ready}")
                lines.append(f"📤 Уже связались: {contacted}")
            lines.append(f"🎯 Цель: {metric_label(active.target_metric)}")
            lines.append(f"Код теста: {key}")
        elif product.draft_experiments:
            lines.append(f"🟡 Подготовлено тестов к запуску: {product.draft_experiments}")
        else:
            lines.append("⚪ Активных тестов сейчас нет.")

        if product.decisions_waiting:
            lines.append(f"🙋 Нужно твоё решение: {product.decisions_waiting}")
        else:
            lines.append("✅ От тебя сейчас ничего не требуется.")

    return "\n".join(lines)


def _render_product(product: ProductControlStatus) -> str:
    lines = [
        product.product_name,
        (
            f"Тесты: {product.active_experiments} идут, "
            f"{product.draft_experiments} подготовлены"
        ),
        f"Нужно решений: {product.decisions_waiting}",
    ]
    if product.experiments:
        record = product.experiments[0]
        key = record.experiment_key or str(record.id)
        lines.append(f"Текущий: {key} — {status_label(record.status)}")
    return "\n".join(lines)


def render_decision_required(item: ControlInboxItem) -> str:
    if item.kind != "decision_required":
        raise ValueError("Expected decision_required inbox item")

    lines = [
        "✅ Нужно твоё решение",
        "",
        f"Что предлагается: {action_label(item.action_type)}",
    ]

    # Internal reasons can be technical or English. Show them only when they
    # already look human-facing; otherwise keep the card concise and clear.
    summary = (item.summary or "").strip()
    if summary and any("А" <= char <= "я" or char in "Ёё" for char in summary):
        lines.extend(["", "Почему:", summary])

    if item.risk_level:
        lines.extend(["", f"Уровень контроля: {risk_label(item.risk_level)}"])

    if item.action_type == "send_outreach_batch":
        lines.extend(
            [
                "",
                "После одобрения система сможет перейти к отправке подготовленных обращений.",
                "Перед отправкой открой «Подробнее» и проверь тексты и получателей.",
            ]
        )
    elif item.action_type == "publish":
        lines.extend(
            [
                "",
                "Это разрешение на публикацию подготовленного материала.",
                "Если не уверен — сначала открой «Подробнее».",
            ]
        )

    return "\n".join(lines)


def render_experiments(status: ControlStatus) -> str:
    lines = [
        "🧪 Маркетинговые тесты",
        "",
        "Здесь видно, что команда сейчас проверяет и на каком этапе находится работа.",
    ]
    found = False
    for product in status.products:
        if not product.experiments:
            continue
        found = True
        lines.extend(["", f"📌 {product.product_name}"])
        for record in product.experiments:
            key = record.experiment_key or str(record.id)
            icon = "🟢" if record.status in {"running", "active"} else "🟡"
            lines.append(f"{icon} {user_text(record.name)}")
            lines.append(f"Состояние: {status_label(record.status)}")
            lines.append(f"Цель: {metric_label(record.target_metric)}")
            lines.append(f"Код: {key}")
    if not found:
        lines.extend(["", "Сейчас нет активных или подготовленных тестов."])
    return "\n".join(lines)


def render_experiment_detail(experiment, prospect_stats: dict[str, int] | None = None) -> str:
    key = experiment.experiment_key or str(experiment.id)
    lines = [
        f"🧪 {user_text(experiment.name)}",
        f"Код: {key}",
        "",
        f"Состояние: {status_label(experiment.status)}",
        f"Что делаем: {channel_label(experiment.channel)}",
        f"Главная цель: {metric_label(experiment.target_metric)}",
    ]

    if experiment.observation_window_days:
        lines.append(f"Срок проверки: {experiment.observation_window_days} дней")

    if prospect_stats is not None:
        total = prospect_stats.get("total", 0)
        resolved = prospect_stats.get("enrichment_resolved", 0)
        pending = prospect_stats.get("enrichment_pending", 0)
        ready = prospect_stats.get("ready_for_outreach", 0)
        lines.extend(
            [
                "",
                "📍 Прогресс по компаниям",
                f"Всего в базе: {total}",
                f"Контакты проверены: {resolved}/{total}",
                f"Ещё нужно проверить: {pending}",
                f"Можно написать напрямую: {ready}",
                f"Есть сайт или чат: {prospect_stats.get('website_or_chat', 0)}",
                f"Только телефон: {prospect_stats.get('phone_only', 0)}",
                f"Данные устарели или не подтверждены: {prospect_stats.get('stale', 0)}",
                f"Уже связались: {prospect_stats.get('contacted', 0)}",
                "",
                "📬 Найденные каналы",
                f"Email: {prospect_stats.get('email', 0)}",
                f"WhatsApp: {prospect_stats.get('whatsapp', 0)}",
                f"Telegram: {prospect_stats.get('telegram', 0)}",
                f"Сайт: {prospect_stats.get('website', 0)}",
                f"Телефон: {prospect_stats.get('phone', 0)}",
            ]
        )

    if experiment.budget is not None:
        lines.append(f"Бюджет теста: {experiment.budget} {experiment.currency or ''}".rstrip())

    start = _format_date(experiment.starts_at)
    end = _format_date(experiment.ends_at)
    if start:
        lines.append(f"Начало: {start}")
    if end:
        lines.append(f"Проверяем результат до: {end}")

    lines.extend(
        [
            "",
            "💡 Что проверяем",
            user_text(experiment.hypothesis),
            "",
            "✅ Когда считаем подход рабочим",
            success_condition_text(experiment.success_condition, experiment.experiment_key),
            "",
            "🛑 Когда останавливаем",
            stop_condition_text(experiment.stop_condition, experiment.experiment_key),
        ]
    )
    return "\n".join(lines)


def render_experiment_launch_confirmation(experiment) -> str:
    key = experiment.experiment_key or str(experiment.id)
    return "\n".join(
        [
            "▶️ Запустить маркетинговый тест?",
            "",
            user_text(experiment.name),
            f"Код: {key}",
            f"Срок проверки: {experiment.observation_window_days} дней",
            f"Бюджет: {experiment.budget} {experiment.currency or ''}".rstrip(),
            "",
            "После подтверждения система:",
            "• начнёт считать срок теста;",
            "• подготовит и проверит базу компаний;",
            "• запустит внутреннюю работу ИИ-команды;",
            "• будет собирать результаты в одном месте.",
            "",
            "Важно: письма, сообщения и публикации без отдельного согласования не отправляются.",
            "",
            "Запустить?",
        ]
    )
