"""An owner-readable, company-scoped SEO factory plan and work calendar.

This module only renders persisted run rows. Article task.due_at is the
earliest *preparation* date, not a guaranteed publication date.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import UUID


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _when(value: str | None, zone: str) -> str:
    if not value:
        return "неизвестно"
    try:
        raw = datetime.fromisoformat(value)
        moment = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
        return moment.astimezone(ZoneInfo(zone)).strftime("%d.%m.%Y %H:%M")
    except (ValueError, KeyError, OverflowError):
        return "неизвестно"


def _state(item: dict) -> tuple[str, str]:
    if item.get("publication_path"):
        return "published", "Опубликована"
    if item.get("task_status") == "failed":
        reason = item.get("failure_code")
        if reason == "invalid_article_output":
            return "failed", "Ошибка формата ответа ИИ"
        if reason == "timeout":
            return "failed", "Истекло время ожидания"
        return "failed", "Ошибка генерации"
    if item.get("article_status") == "awaiting_approval":
        return "review", "Ждёт проверки"
    if item.get("task_status") == "completed":
        return "ready", "Текст подготовлен"
    if item.get("task_status") == "running":
        return "running", "Генерируется"
    if item.get("task_status") == "queued":
        return "queued", "В очереди"
    if item.get("task_status") == "cancelled":
        return "cancelled", "Отменена"
    return "unknown", "Состояние уточняется"


def seo_plan_board(run: dict | None, product_id: UUID, *, autorun: bool,
                   enabled: bool, max_visible: int = 12) -> str:
    base = f"/studio/products/{product_id}"
    if run is None:
        return (
            '<section class="panel seo-plan-board" aria-label="SEO-план и календарь">'
            '<div class="seo-board-heading"><div><span class="seo-board-eyebrow">ПЛАН РАБОТЫ</span>'
            '<h2>SEO-план и календарь</h2></div></div>'
            '<p>Запусков пока нет. После создания плана здесь появятся поисковые запросы, '
            'этапы подготовки, очередность статей, проверки и фактические публикации.</p>'
            + (f'<a class="btn primary" href="{base}/seo/queue">Создать план</a>' if enabled else '')
            + '</section>'
        )
    tz = run.get("timezone_name") or "Europe/Moscow"
    horizon = run.get("horizon_days")
    period = f"План на {horizon} дней" if horizon in (7, 30) else "Период не указан"
    items = sorted(run.get("articles") or [],
                   key=lambda a: (a.get("scheduled_iso") or "", str(a.get("title") or "")))
    statuses = [_state(item)[0] for item in items]
    published = statuses.count("published")
    failed = statuses.count("failed")
    review = statuses.count("review")
    ready = statuses.count("ready")
    running = statuses.count("running")
    waiting = statuses.count("queued")
    total = len(items)
    planned = run.get("articles_queued")
    planned_count = total if total else planned
    planned_display = str(planned_count) if planned_count is not None else "—"
    stages = (
        ("Поисковые запросы", bool(run.get("semantic_ready"))),
        ("SEO-план", bool(run.get("plan_ready"))),
        ("Статьи в очереди", bool(total or (isinstance(planned, int) and planned > 0))),
        ("Статьи опубликованы", published > 0),
    )
    stage_html = "".join(
        '<li class="' + ("is-done" if done else "is-pending") + '">'
        '<span aria-hidden="true">' + ("✓" if done else "·") + '</span>'
        + _esc(label) + '</li>' for label, done in stages
    )
    counters = (
        ("В плане", planned_display),
        ("Опубликовано", published),
        ("На проверке", review),
        ("Ошибки", failed),
    )
    counters_html = "".join(
        '<div><span>' + _esc(name) + '</span><strong>' + _esc(count)
        + '</strong></div>' for name, count in counters
    )

    def article_html(item: dict) -> str:
        state, label = _state(item)
        article_id = item.get("article_id")
        task_id = item.get("task_id")
        href = f"{base}/materials/{article_id}" if article_id else f"{base}/tasks/{task_id}"
        title = _esc(item.get("title") or "Без названия")
        query = item.get("primary_query") or ""
        query_html = '<small>Запрос: ' + _esc(query) + '</small>' if query else ""
        preparation = item.get("scheduled_at")
        if state == "published":
            released = item.get("published_at")
            publication = ("Вышла " + _esc(released)) if released else "Опубликована; время не записано"
        else:
            publication = "Дата выхода пока не определена"
        prepare_html = (
            '<span>Подготовка по плану: ' + _esc(preparation) + '</span>'
            if preparation else '<span>Дата подготовки не назначена</span>'
        )
        retry = (
            '<form class="seo-board-retry" method="post" action="'
            + _esc(f"{base}/seo/articles/{task_id}/retry") + '">'
            '<button class="btn secondary" type="submit"'
            ' title="Запускает платную повторную генерацию только этой статьи">'
            'Повторить генерацию</button></form>'
            if state == "failed" and enabled and task_id else ""
        )
        return (
            '<li class="seo-board-item" data-state="' + _esc(state) + '">'
            '<div class="seo-board-date">' + (_esc(preparation[:10]) if preparation else "Без даты") + '</div>'
            '<div class="seo-board-copy"><a href="' + _esc(href) + '">' + title + '</a>'
            + query_html + '<div class="seo-board-dates">' + prepare_html
            + '<span>' + publication + '</span></div></div>'
            + '<span class="seo-board-state state-' + _esc(state) + '">' + _esc(label) + '</span>'
            + retry + '</li>'
        )
    visible = "".join(article_html(item) for item in items[:max_visible])
    remainder = ("<details class=\"seo-board-more\"><summary>Показать остальные "
                 + str(total - max_visible) + " статей</summary><ol class=\"seo-board-list\">"
                 + "".join(article_html(item) for item in items[max_visible:])
                 + "</ol></details>") if total > max_visible else ""
    if total:
        next_actions = (
            ("Есть ошибки генерации. Откройте соответствующие задания и повторите только "
             "те статьи, причина сбоя которых устранена.") if failed else
            ("Есть материалы на проверке: они не будут выпущены до прохождения "
             "контроля качества.") if review else
            ("Подготовка продолжается по указанным заданиям.") if running or waiting else
            ("Все задания плана обработаны. Проверьте результаты публикаций.")
        )
    else:
        next_actions = (
            "Фабрика готовит поисковые данные и SEO-план. Даты появления статей "
            "станут известны после постановки заданий в очередь."
            if run.get("status") in ("queued", "running")
            else "Статьи пока не поставлены в очередь. Откройте последний запуск для подробностей."
        )
    run_status = "Создание плана: " + str(run.get("status_label") or run.get("status") or "неизвестно")
    updated_at = _when(run.get("updated_at"), tz)
    return (
        '<section class="panel seo-plan-board" aria-label="SEO-план и календарь">'
        '<div class="seo-board-heading"><div><span class="seo-board-eyebrow">ПЛАН РАБОТЫ</span>'
        '<h2>SEO-план и календарь</h2>'
        '<p>' + _esc(period) + ' · ' + _esc(tz) + '</p></div>'
        '<span class="seo-board-overall">' + _esc(run_status) + '</span></div>'
        '<div class="seo-board-stats">' + counters_html + '</div>'
        '<ol class="seo-board-stages">' + stage_html + '</ol>'
        '<div class="seo-board-summary"><p>Последнее изменение: ' + _esc(updated_at)
        + '. Автопланирование: ' + ("включено" if autorun and enabled else "выключено") + '.</p>'
        '<p>Время в календаре — <strong>плановый старт подготовки</strong>, '
        'а не обещанная дата публикации. Фактический выход зависит от генерации, '
        'проверки и дневного лимита.</p>'
        '<p class="seo-board-next">' + _esc(next_actions) + '</p></div>'
        + ('<h3>Статьи по плану</h3><ol class="seo-board-list">' + visible
           + '</ol>' + remainder if total else
           '<p class="seo-board-empty">Статьи пока не сформированы.</p>')
        + '</section>'
    )
