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
