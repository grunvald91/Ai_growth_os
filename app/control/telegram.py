from app.control.contracts import ControlInboxItem, ControlStatus, ProductControlStatus


def render_status(status: ControlStatus) -> str:
    lines = [
        "AI Growth OS",
        f"Активных экспериментов: {status.active_experiments}",
        f"Решений ожидает: {status.decisions_waiting}",
    ]
    if not status.products:
        lines.append("")
        lines.append("Продукты пока не настроены.")
        return "\n".join(lines)

    for product in status.products:
        lines.extend(["", _render_product(product)])
    return "\n".join(lines)


def _render_product(product: ProductControlStatus) -> str:
    lines = [
        product.product_name,
        f"Эксперименты: {product.active_experiments} активных, {product.draft_experiments} черновиков",
        f"Нужно решений: {product.decisions_waiting}",
    ]
    if product.experiments:
        record = product.experiments[0]
        key = record.experiment_key or str(record.id)
        lines.append(f"Текущий: {key} — {record.status}")
    return "\n".join(lines)


def render_decision_required(item: ControlInboxItem) -> str:
    if item.kind != "decision_required":
        raise ValueError("Expected decision_required inbox item")
    lines = [
        "🔶 Нужно решение",
        item.title,
        "",
        item.summary,
    ]
    if item.risk_level:
        lines.extend(["", f"Риск: {item.risk_level}"])
    return "\n".join(lines)


def render_experiments(status: ControlStatus) -> str:
    lines = ["🧪 Эксперименты"]
    found = False
    for product in status.products:
        if not product.experiments:
            continue
        found = True
        lines.extend(["", product.product_name])
        for record in product.experiments:
            key = record.experiment_key or str(record.id)
            metric = f" · метрика: {record.target_metric}" if record.target_metric else ""
            lines.append(f"• {key} — {record.status}{metric}")
            lines.append(f"  {record.name}")
    if not found:
        lines.extend(["", "Активных или черновых экспериментов нет."])
    return "\n".join(lines)



def render_experiment_detail(experiment) -> str:
    key = experiment.experiment_key or str(experiment.id)
    lines = [
        f"🧪 {key}",
        experiment.name,
        "",
        f"Статус: {experiment.status}",
        f"Канал: {experiment.channel or 'не указан'}",
        f"Главная метрика: {experiment.target_metric or 'не указана'}",
    ]
    if experiment.observation_window_days:
        lines.append(f"Окно наблюдения: {experiment.observation_window_days} дней")
    if experiment.budget is not None:
        lines.append(f"Бюджет: {experiment.budget} {experiment.currency or ''}".rstrip())
    if experiment.starts_at:
        lines.append(f"Старт: {experiment.starts_at}")
    if experiment.ends_at:
        lines.append(f"Окончание окна: {experiment.ends_at}")
    lines.extend([
        "",
        "Гипотеза:",
        experiment.hypothesis,
        "",
        "Условие KEEP:",
        experiment.success_condition or "не задано",
        "",
        "Условие STOP:",
        experiment.stop_condition or "не задано",
    ])
    return "\n".join(lines)


def render_experiment_launch_confirmation(experiment) -> str:
    key = experiment.experiment_key or str(experiment.id)
    return "\n".join([
        f"▶️ Запуск {key}",
        "",
        f"{experiment.name}",
        f"Окно: {experiment.observation_window_days} дней",
        f"Бюджет: {experiment.budget} {experiment.currency or ''}".rstrip(),
        "",
        "После подтверждения:",
        "• эксперимент станет RUNNING;",
        "• зафиксируются дата старта и конец окна;",
        "• создастся запись TEST в Decision Journal;",
        "• worker получит внутреннюю задачу подготовки cohort;",
        "• никакие письма, сообщения или публикации автоматически не отправятся.",
        "",
        "Подтвердить запуск?",
    ])
