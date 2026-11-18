"""Presentation helpers for Growth Studio's task-first interface.

Only owner-authenticated route handlers may call these templates. Every
dynamic string is HTML escaped; no backend side effects occur here.
"""
from __future__ import annotations
import html
import json
import re
from datetime import datetime
from urllib.parse import quote

from app.content.durations import KINDS, duration_estimate

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)

FORMAT_CHOICES = [
    ("post", "Пост", "Готовый текст для одной площадки"),
    ("short_video", "Короткое видео · сценарий", "Сценарий, кадры и озвучка — не MP4"),
    ("story", "Stories", "Вертикальные PNG в фирменном стиле · при наличии Brand Kit"),
    ("static_image", "Изображение · бриф", "Визуальный бриф — не готовая картинка"),
    ("package", "Полный пакет", "Статья, адаптации и видеосценарии"),
]

CHANNELS = {
    "post": [("telegram", "Telegram"), ("vk", "ВКонтакте"), ("dzen", "Дзен"),
             ("vc_ru", "vc.ru"), ("tenchat", "TenChat")],
    "short_video": [("youtube_shorts", "YouTube Shorts"), ("instagram_reels", "Instagram Reels"),
                    ("vk_clips", "VK Клипы")],
    "story": [("telegram", "Telegram Stories"), ("vk", "Истории ВК"),
              ("instagram_reels", "Instagram Stories")],
    "static_image": [("telegram", "Telegram"), ("vk", "ВКонтакте"),
                     ("dzen", "Дзен"), ("instagram_reels", "Instagram")],
}

def create_content_html(product_id, *, created=False, fields=None, error=None,
                        durations=None, brand_canary=False) -> str:
    fields = fields or {}
    timing = "".join(f"<li>{esc(duration_estimate(durations or {}, kind))}</li>"
                     for kind in ("post", "package", "creative"))
    base = f"/studio/products/{product_id}"
    choices = "".join(
        f'<label class="sg-choice"><input type="radio" name="format" value="{esc(key)}" '
        f'{"checked" if key == fields.get("format", "post") else ""} required><span class="sg-choice-copy">'
        f'<b>{esc(name)}</b><small>{esc(description)}</small></span></label>'
        for i, (key,name,description) in enumerate(FORMAT_CHOICES)
    )
    # Without JavaScript, all compatible channels remain selectable in
    # explicitly labeled groups. JS narrows these to the chosen format.
    option_groups = "".join(
        '<optgroup label="' + esc(next(name for key,name,_ in FORMAT_CHOICES if key == fmt)) + '">'
        + "".join(f'<option value="{esc(key)}" {"selected" if key == fields.get("channel") else ""}>{esc(label)}</option>'
                  for key,label in platforms) + '</optgroup>'
        for fmt,platforms in CHANNELS.items()
    )
    feedback = ('<div class="sg-notice" role="status">✓ Задание добавлено в очередь. '
                f'<a href="{base}/review">Посмотреть результаты и согласование</a></div>'
                if created else "")
    if error:
        feedback = f'<div class="sg-warning" role="alert">{esc(error)}</div>'
    brand_controls = """
            <fieldset class="sg-field sg-wide" data-story-brand-control>
              <legend>Stories · фирменное оформление</legend>
              <label class="sg-field"><span>Стиль</span>
                <select name="brand_mode">
                  <option value="auto" selected>Использовать утверждённый Brand Kit, если настроен</option>
                  <option value="brand">Только с утверждённым Brand Kit</option>
                  <option value="classic">Старый способ без фирменной композиции</option>
                </select>
              </label>
              <label class="sg-field"><span>Генерация фона</span>
                <select name="image_model">
                  <option value="none">Без платного изображения · фирменный фон</option>
                  <option value="auto" selected>Модель из Brand Kit · платный запрос</option>
                  <option value="gemini">Gemini · платный запрос</option>
                  <option value="openai">ChatGPT Image · платный запрос</option>
                </select>
              </label>
              <label class="sg-field" data-story-paid-confirm><input type="checkbox" name="paid_confirm" value="yes"> Подтверждаю платную генерацию фонов для выбранного количества кадров</label>
              <small>Текст и логотип накладываются отдельно. Платная генерация только после явного подтверждения.</small>
            </fieldset>
    """ if brand_canary else ""
    return f"""
    <section class="sg-surface sg-animator-entry sg-animator-top">
      <div><span class="sg-overline">НОВЫЙ ФОРМАТ · ВИДЕО С ОЗВУЧКОЙ</span>
        <h2>Простое видео с озвучкой</h2>
        <p>Закажите анимированный ролик. Сценарий, рисунки, голос и готовый MP4
        будут доступны в отдельном задании.</p>
        <small>Платные API-вызовы — только после вашего подтверждения.</small>
      </div>
      <a class="btn primary" href="{base}/whiteboard">Создать простое видео →</a>
    </section>
    <div class="sg-layout-two">
      <section class="sg-surface sg-create-form">
        <div class="sg-section-head"><div><span class="sg-overline">ШАГ 1 · МАТЕРИАЛ</span>
        <h2>Что будем создавать?</h2><p>Выберите результат, а не целую цепочку ИИ-агентов.</p></div></div>
        {feedback}
        <form method="post" action="{base}/create" class="sg-form" id="sg-create-form" data-studio-create>
          <fieldset class="sg-format-grid"><legend class="sr-only">Формат материала</legend>{choices}</fieldset>
          <div class="sg-fields">
            <label class="sg-field" data-create-platform><span>Площадка</span>
              <select name="channel" aria-label="Площадка">{option_groups}</select>
              <small>Список меняется в зависимости от формата</small>
            </label>
            <label class="sg-field sg-wide"><span>Тема или задача</span>
              <textarea name="topic" minlength="3" maxlength="300" rows="3"
                placeholder="Например: три ошибки при выборе массажной процедуры" required>{esc(fields.get("topic", ""))}</textarea>
              <small>Одно предложение обычными словами. Подробности ИИ возьмёт из профиля компании.</small>
            </label>
            <label class="sg-field sg-wide"><span>Для кого пишем <em>· необязательно</em></span>
              <input name="audience" maxlength="300" value="{esc(fields.get('audience', ''))}" placeholder="Например: занятые родители с детьми">
            </label>
            {brand_controls}
            <label class="sg-field sg-wide"><span>Дополнительные требования <em>· необязательно</em></span>
              <textarea name="editorial_brief" maxlength="3500" rows="3"
                placeholder="Тон, особенности услуги, что нельзя обещать, нужный призыв">{esc(fields.get('editorial_brief', ''))}</textarea>
            </label>
          </div>
          <div class="sg-form-bottom">
            <div class="sg-form-hint"><span aria-hidden="true">✓</span> Создание не публикует материал.
            После подготовки можно проверить, изменить и утвердить.</div>
            <div class="sg-form-hint sg-duration-note" role="note"><span aria-hidden="true">⏱</span>
            <div>Сколько ждать результат:<ul>{timing}</ul>ИИ-модели отвечают небыстро. Страницу можно закрыть —
            задание продолжится, статус виден в разделе <a href="{base}/work">«Задания»</a>.
            <a href="{base}/speed">Скорость моделей →</a></div></div>
            <button class="btn primary sg-primary-action" type="submit">Создать материал <span aria-hidden="true">→</span></button>
          </div>
        </form>
      </section>
      <aside class="sg-aside-stack">
        <section class="sg-surface sg-guide"><h3>Простое видео с озвучкой</h3>
          <p>Готовый вертикальный MP4 на 30 секунд или минуту: русская озвучка, рисование, субтитры и призыв в конце. Платные модели запускаются после отдельного подтверждения.</p>
          <a class="btn secondary" href="{base}/whiteboard">Создать простое видео →</a>
        </section>
        <section class="sg-surface sg-guide"><div class="sg-guide-icon">✦</div>
          <h2>Как работает ИИ-команда</h2>
          <ol class="sg-steps"><li><b>Создаёт</b><span>Под конкретную компанию, формат и аудиторию.</span></li>
          <li><b>Даёт на проверку</b><span>Вы видите готовый текст или производственный бриф.</span></li>
          <li><b>Дорабатывает</b><span>Пост можно доработать с ИИ. Все форматы можно исправить вручную; предыдущая версия сохранится.</span></li>
          <li><b>Публикует отдельно</b><span>Telegram: только утверждённые посты и подключённые каналы.</span></li></ol>
        </section>
        <section class="sg-surface sg-guide"><h3>Нужен план на неделю или месяц?</h3>
          <p>Для работы по расписанию используйте действующий контент-план.</p>
          <a class="btn secondary" href="{base}/plan">Открыть контент-план →</a>
        </section>
      </aside>
    </div>"""

def speed_html(product_id, calls, durations, *, days=7) -> str:
    base = f"/studio/products/{product_id}"
    tabs = "".join(f'<a class="tab {"active" if value == days else ""}" href="{base}/speed?days={value}">{value} дней</a>'
                   for value in (7, 30))
    rows = "".join(
        f"<tr><td>{esc(row['provider'])}</td><td>{esc(row['model'])}</td><td>{row['calls']}</td>"
        f"<td>{row['failed']}</td><td>{row['p50']:.0f} с</td><td>{row['p90']:.0f} с</td><td>{row['max']:.0f} с</td></tr>"
        for row in calls) or '<tr><td colspan="7">За выбранный период вызовов нет.</td></tr>'
    kinds = "".join(
        f"<tr><td>{esc(KINDS[kind][0])}</td><td>{item['count']}</td>"
        f"<td>{item['median']:.0f} мин</td><td>{item['p90']:.0f} мин</td></tr>"
        for kind, item in sorted(durations.items()) if kind in KINDS) or '<tr><td colspan="4">Завершённых заданий за 30 дней нет.</td></tr>'
    return f"""
    <section class="sg-surface"><div class="sg-section-head"><div><span class="sg-overline">ВЫЗОВЫ МОДЕЛЕЙ</span>
      <h2>Время ответа по провайдерам</h2>
      <p>p50 — половина вызовов быстрее этого времени, p90 — 9 из 10 вызовов. Лимит ожидания одного вызова — 10 минут:
      мы не обрываем медленные ответы, а измеряем их.</p></div></div>
      <div class="tabs">{tabs}</div>
      <div class="sg-table-wrap"><table class="sg-table"><thead><tr><th>Провайдер</th><th>Модель</th><th>Вызовов</th>
      <th>Ошибок</th><th>p50</th><th>p90</th><th>Максимум</th></tr></thead><tbody>{rows}</tbody></table></div>
    </section>
    <section class="sg-surface"><div class="sg-section-head"><div><span class="sg-overline">ЗАДАНИЯ ЦЕЛИКОМ</span>
      <h2>Сколько ждать результат</h2><p>От первого до последнего вызова модели у завершённых заданий за 30 дней.
      Эти же цифры показываются в формах создания.</p></div></div>
      <div class="sg-table-wrap"><table class="sg-table"><thead><tr><th>Задание</th><th>Завершено</th>
      <th>Обычно</th><th>9 из 10 быстрее</th></tr></thead><tbody>{kinds}</tbody></table></div>
    </section>"""


# The same Artifact.status and category scope drives every review filter.
# The source of truth for approving a material remains app.content.review.
REVIEW_KINDS_BY_FILTER = {
    "all": ("channel_text", "article", "creative_brief", "video_script", "video_render"),
    "posts": ("channel_text",),
    "articles": ("article",),
    "creative": ("creative_brief", "video_script", "video_render"),
    "video": ("video_script", "video_render"),
    # Backwards compatible pre-redesign link; do not silently widen permissions.
    "text": ("channel_text", "article"),
}
REVIEW_TABS = (
    ("all", "Всё"), ("posts", "Посты"), ("articles", "Статьи / SEO"),
    ("creative", "Креативы"), ("video", "Видео"),
)


def _review_preview(row, data: dict) -> str:
    """A short, safe excerpt. Never pretend it is the complete review text."""
    sources = (
        data.get("body"), data.get("preview_text"), data.get("full_article"),
        data.get("reader_article"),
    )
    raw = next((entry for entry in sources if isinstance(entry, str) and entry.strip()), "")
    if not raw and isinstance(data.get("production_brief"), dict):
        raw = data["production_brief"].get("voiceover_or_dialogue_script") or ""
    if not isinstance(raw, str):
        raw = ""
    lines = raw.strip().splitlines()
    if lines and re.sub(r'^#+\\s*', '', lines[0]).strip().casefold() == (row.title or '').strip().casefold():
        lines = lines[1:]
    preview = re.sub(r'\\s+', ' ', '\\n'.join(lines)).strip()
    return (preview[:220].rsplit(" ", 1)[0] or preview[:220]) + "…" if len(preview) > 220 else preview


def review_html(product_id, records, *, filter_name="all", counts=None,
                page=1, page_count=1, total=None, filtered_total=None) -> str:
    """One scoped, paginated approval inbox; no separate mutation endpoint.

    Full content, version checks, warning resolution and media existence checks
    still happen on the material detail page using app.content.review.
    """
    from app.api.studio_resource_widgets import resource_widget

    base = f"/studio/products/{product_id}"
    counts = counts or {kind: 0 for kind in REVIEW_KINDS_BY_FILTER["all"]}
    def count_for(kind: str) -> int:
        return sum(counts.get(value, 0) for value in REVIEW_KINDS_BY_FILTER[kind])
    total = count_for("all") if total is None else total
    filtered_total = count_for(filter_name) if filtered_total is None else filtered_total
    types = list(REVIEW_TABS)
    if filter_name == "text":
        types.insert(1, ("text", "Тексты"))
    tabs = []
    for value, label in types:
        count = count_for(value)
        current = 'aria-current="page"' if value == filter_name else ""
        tabs.append(
            f'<a class="sg-filter {"is-active" if value == filter_name else ""}" '
            f'href="{base}/review?kind={value}" {current}>'
            f'{esc(label)} <span class="sg-review-filter-count">{count}</span></a>'
        )
    cards = []
    for row in records:
        try:
            data = json.loads(row.content)
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        preview = _review_preview(row, data)
        channel = str(data.get("platform") or data.get("channel") or "Материал")
        channel = {"own_site": "Сайт", "telegram": "Telegram", "vk": "ВКонтакте",
                   "zen": "Дзен"}.get(channel, channel)
        kind = {"creative_brief": "Креативный бриф", "video_script": "Сценарий",
                "video_render": "Готовый MP4", "article": "Статья",
                "channel_text": "Пост"}.get(row.kind, "Материал")
        if row.kind == "article" and data.get("seo_brief"):
            kind = "SEO-статья"
        action = f"{base}/materials/{row.id}"
        warnings = data.get("quality_warnings")
        count_warnings = len(warnings) if isinstance(warnings, list) else 0
        fact_check = bool(data.get("fact_check_required"))
        badges = (f'<span class="sg-warning">Замечаний: {count_warnings}</span>'
                  if count_warnings else "")
        if fact_check:
            badges += '<span class="sg-warning">Проверьте факты</span>'
        primary = (
            '<div class="sg-review-item">'
            f'<div class="sg-review-top"><span class="sg-tag">{esc(kind)}</span>'
            f'<span class="sg-channel">{esc(channel)}</span>{badges}</div>'
            f'<h3>{esc(row.title)}</h3>'
            f'<p>{esc(preview) if preview else "Предпросмотр недоступен. Откройте материал целиком."}</p>'
            '<div class="sg-review-footer"><span>Ожидает решения</span>'
            '<div class="sg-inline-actions">'
            f'<a class="btn secondary" href="{action}" '
            f'aria-label="Открыть и проверить: {esc(row.title)}">'
            'Открыть и проверить</a></div></div></div>'
        )
        warning_note = ("Числа, цены и результаты нужно проверить по источникам до утверждения."
                        if fact_check else
                        "Утвердить можно только после проверки полной версии материала.")
        cards.append(resource_widget(
            kind="material", primary_html=primary,
            description=f"{kind} · {channel}. Предпросмотр сокращён; полную версию проверьте перед решением.",
            note=warning_note, detail_label="Что проверить",
        ))
    empty_title = "В этой категории нет материалов" if filter_name != "all" else "Материалы проверены"
    empty_note = ("Выберите другую категорию." if filter_name != "all"
                  else "Сейчас ничего не требует согласования.")
    empty = (
        f'<div class="sg-empty"><div>✓</div><h3>{empty_title}</h3><p>{empty_note}</p>'
        f'<a class="btn primary" href="{base}/create">Создать материал →</a></div>'
    )
    paging = ""
    if page_count > 1:
        links = []
        if page > 1:
            links.append(f'<a class="btn secondary" href="{base}/review?kind={filter_name}&page={page - 1}">← Назад</a>')
        links.append(f'<span aria-current="page">Страница {page} из {page_count}</span>')
        if page < page_count:
            links.append(f'<a class="btn secondary" href="{base}/review?kind={filter_name}&page={page + 1}">Дальше →</a>')
        paging = '<nav class="sg-review-pagination" aria-label="Страницы согласования">' + "".join(links) + '</nav>'
    return (
        '<section class="sg-surface sg-review-page">'
        + review_section_nav(product_id, "pending")
        + '<div class="sg-section-head"><div><span class="sg-overline">ЕДИНЫЙ ЦЕНТР СОГЛАСОВАНИЯ</span>'
        '<h2>Очередь согласования</h2>'
        '<p>Посты, статьи, креативы и видео. Проверка материала — до утверждения.</p>'
        '</div>'
        f'<span class="sg-count">{total} на проверке</span></div>'
        f'<nav class="sg-filters" aria-label="Категории материалов">{"".join(tabs)}</nav>'
        f'<p class="sg-review-showing">В категории: {filtered_total}. '
        f'Показано: {len(records)}.</p>'
        f'<div class="sg-review-list">{"".join(cards) or empty}</div>'
        + paging + '</section>'
    )


def channels_html(product_id, connections, publications, approved_posts, *, added=False, scheduled=False,
                  selected_post=None, autopost_enabled=False, publication_details=None,
                  publication_page=1, publication_total=0) -> str:
    publication_details = publication_details or {}
    base=f"/studio/products/{product_id}"
    pages = max(1, (publication_total + 29) // 30)
    pagination = '<nav class="preview-actions" aria-label="История отправок">'
    if publication_page > 1:
        pagination += f'<a class="btn ghost" href="{base}/channels?page={publication_page - 1}">← Новее</a>'
    pagination += f'<span>Страница {publication_page} из {pages} · Всего {publication_total}</span>'
    if publication_page < pages:
        pagination += f'<a class="btn ghost" href="{base}/channels?page={publication_page + 1}">Ранее →</a>'
    pagination += '</nav>'
    feedback = ("<p class='sg-notice' role='status'>✓ Настройки обновлены.</p>"
                if added or scheduled else "")
    linked=[]
    for row in connections:
        try: data=json.loads(row.content)
        except (ValueError,TypeError): data={}
        linked.append(f'<div class="sg-channel-line"><span class="sg-link-dot">✓</span>'
                      f'<div><b>{esc(data.get("title") or row.title)}</b>'
                      f'<small>{esc(data.get("username") or data.get("chat_id") or "Telegram-канал")}</small></div>'
                      '<span class="sg-channel-status">Бот проверен</span></div>')
    channels_opts="".join(f'<option value="{esc(c.id)}">{esc(c.title)}</option>' for c in connections)
    post_opts="".join(f'<option value="{esc(p.id)}" {"selected" if str(p.id)==str(selected_post) else ""}>{esc(p.title[:105])}</option>' for p in approved_posts)
    pub_items=[]
    for p in publications:
        detail = publication_details.get(str(p.id), {})
        status={"scheduled":"Запланировано","sending":"Отправляется",
                "published":"Опубликовано","delivery_unknown":"Проверить вручную",
                "missed":"Время пропущено","cancelled":"Отменено",
                "failed_before_send":"Не отправлено"}.get(p.status, p.status)
        cancel=(f'<form method="post" action="{base}/publishing/{p.id}/cancel">'
                f'<button class="btn secondary small" type="submit">Отменить</button></form>'
                if p.status in {"scheduled","missed","failed_before_send"} else "")
        pub_items.append(f'<li class="sg-publication-row"><div><b>{esc(status)}</b>'
                         f'<a href="{base}/materials/{p.artifact_id}">{esc(detail.get("title") or "Открыть материал")}</a>'
                         f'<small>{esc(detail.get("channel") or "Telegram")} · {esc(detail.get("when") or "Дата не указана")}</small></div>{cancel}'
                         + (f'<form method="post" action="{base}/publishing/{p.id}/reconcile">'
                            '<label class="sg-field"><span>После ручной проверки канала</span><select name="outcome">'
                            '<option value="published">Сообщение есть в канале</option>'
                            '<option value="not_sent">Сообщения нет в канале</option></select></label>'
                            '<label class="sg-field"><span>ID сообщения (если опубликовано)</span><input name="message_id" type="number" min="1"></label>'
                            '<label><input type="checkbox" name="confirmed" value="yes" required> Я проверил канал вручную</label>'
                            '<button class="btn secondary" type="submit">Сохранить результат проверки</button></form>'
                            if p.status == "delivery_unknown" else "") + '</li>')
    dispatch_status = ("Автоотправка включена" if autopost_enabled else
                       "Автоотправка выключена — запланированные посты пока не отправляются")
    return f"""
    <section class="sg-surface" style="margin-bottom:12px">
      <div class="sg-section-head"><div><span class="sg-overline">КАНАЛЫ ПУБЛИКАЦИИ</span>
        <h2>ВКонтакте</h2><p>Подключение сообщества через VK ID, публикация утверждённых постов и расписание.</p>
      </div><a class="btn secondary" href="{base}/vk">Управлять VK →</a></div>
    </section>
    <div class="sg-layout-two">
      <div class="sg-aside-stack">
        <section class="sg-surface"><div class="sg-section-head"><div><span class="sg-overline">ПОДКЛЮЧЕНИЯ</span>
          <h2>Telegram-каналы</h2><p>Без ручной вставки API-токенов: бот проверит право публикации.</p>
          <p class="sg-publish-mode" role="status">{esc(dispatch_status)}</p></div>
          <span class="sg-count">{len(connections)}</span></div>
          {feedback}<div class="sg-channel-list">{"".join(linked) or '<p class="sg-muted">Пока нет подключённых каналов.</p>'}</div>
          <form method="post" action="{base}/channels/connect" class="sg-fields sg-compact-form">
            <label class="sg-field"><span>Имя канала или ID</span>
              <input name="channel" required maxlength="40" placeholder="@my_channel" autocomplete="off"></label>
            <button class="btn secondary" type="submit">Проверить и подключить →</button>
          </form>
          <p class="sg-footnote">Перед подключением добавьте управляющего бота администратором с правом публикации.</p>
        </section>
        <section class="sg-surface"><div class="sg-section-head"><div><span class="sg-overline">ИСТОРИЯ И ОЧЕРЕДЬ</span>
          <h2>Отправки</h2><p>Реальный статус Telegram отдельно от готовности текста.</p></div></div>
          <ul class="sg-publication-list">{"".join(pub_items) or '<li class="sg-muted">Отправок ещё нет.</li>'}</ul>
          {pagination}
        </section>
      </div>
      <aside class="sg-aside-stack">
        <section class="sg-surface sg-guide"><span class="sg-overline">ЗАПЛАНИРОВАТЬ</span>
          <h2>Отправить утверждённый пост</h2>
          <p>Выберите материал, канал и дату. Публикация требует отдельного подтверждения.</p>
          <form method="post" action="{base}/publishing/schedule" class="sg-fields sg-compact-form">
            <label class="sg-field"><span>Готовый Telegram-пост</span>
              <select name="post_id" required>{post_opts}</select></label>
            <label class="sg-field"><span>Канал</span>
              <select name="connection_id" required>{channels_opts}</select></label>
            <label class="sg-field"><span>Дата и время · Москва</span>
              <input type="datetime-local" name="when" required></label>
            <button class="btn primary" type="submit" {"disabled" if not connections or not approved_posts else ""}>Запланировать</button>
          </form>
          <p class="sg-footnote">Если автоотправка выключена на сервере, запись будет сохранена, но сообщение не отправится.</p>
        </section>
      </aside>
    </div>"""


# Pages stay on the same authenticated /review route. Section navigation never
# performs approval, requeue, publication or another paid operation.
def review_section_nav(product_id, selected="pending") -> str:
    base = f"/studio/products/{product_id}/review"
    sections = [
        ("pending", "Ожидают решения", base),
        ("history", "История решений", base + "?section=history"),
        ("seo", "Статусы SEO", base + "?section=seo"),
    ]
    return (
        '<nav class="sg-review-sections" aria-label="Разделы центра согласования">'
        + "".join(
            f'<a class="sg-filter {"is-active" if name == selected else ""}" '
            f'{"aria-current=page" if name == selected else ""} '
            f'href="{url}">{esc(label)}</a>'
            for name, label, url in sections
        ) + '</nav>'
    )


def _review_paging(product_id, section: str, page: int, page_count: int) -> str:
    if page_count <= 1:
        return ""
    base = f"/studio/products/{product_id}/review?section={section}&page="
    previous = (f'<a class="btn secondary" href="{base}{page-1}">← Назад</a>'
                if page > 1 else "")
    following = (f'<a class="btn secondary" href="{base}{page+1}">Дальше →</a>'
                 if page < page_count else "")
    return (
        '<nav class="sg-review-pagination" aria-label="Страницы раздела">'
        + previous + f'<span aria-current="page">Страница {page} из {page_count}</span>'
        + following + '</nav>'
    )


def review_history_html(product_id, result) -> str:
    """Render persisted audit events; never infer a decision from a status."""
    from app.api.studio_resource_widgets import resource_widget

    base = f"/studio/products/{product_id}"
    cards = []
    for approval, artifact in result.records:
        if approval.action_type == "publish":
            label = ("SEO: публикация разрешена" if approval.status == "approved"
                     else "SEO: публикация отклонена")
        else:
            label = "Материал утверждён" if approval.status == "approved" else "Материал отклонён"
        origin = ("ИИ / политика SEO" if approval.requested_by == "seo_factory"
                  else "Диспетчер" if approval.action_type.startswith("dispatcher:")
                  else "Studio")
        time = (approval.decided_at.strftime("%d.%m.%Y %H:%M")
                if approval.decided_at else "Время решения не записано")
        href = f"{base}/materials/{artifact.id}"
        primary = (
            '<div class="sg-review-item">'
            f'<div class="sg-review-top"><span class="sg-tag">{esc(label)}</span>'
            f'<span class="sg-channel">{esc(origin)}</span></div>'
            f'<h3>{esc(artifact.title)}</h3>'
            f'<p>{esc(time)} · {esc(approval.decided_by or "Решение без указанного автора")}</p>'
            '<div class="sg-review-footer"><span>Запись журнала</span>'
            f'<a class="btn secondary" href="{href}">Открыть материал</a>'
            '</div></div>'
        )
        cards.append(resource_widget(
            kind="material", primary_html=primary,
            description="Запись о решении из журнала согласований.",
            note="История показывает зафиксированное решение, а не текущую готовность к публикации.",
            detail_label="Что означает запись",
        ))
    empty = ('<p class="sg-review-empty">Решений в журнале пока нет. '
             'Ранее утверждённые материалы без записи не выдаются за проверенные события.</p>')
    return (
        '<section class="sg-surface sg-review-page">'
        + review_section_nav(product_id, "history")
        + '<div class="sg-section-head"><div>'
        '<span class="sg-overline">ПОДТВЕРЖДЁННЫЕ СОБЫТИЯ</span>'
        '<h2>История решений</h2>'
        '<p>Зафиксированные утверждения и решения о SEO-публикации. '
        'Дата и автор берутся из журнала, публикация отслеживается отдельно.</p>'
        '</div>'
        f'<span class="sg-count">Всего записей: {result.total}</span></div>'
        f'<div class="sg-review-list">{"".join(cards) or empty}</div>'
        + _review_paging(product_id, "history", result.page, result.page_count)
        + '</section>'
    )


def seo_progress_html(product_id, result) -> str:
    """Per-step observations: no invented score or automatic publish claim."""
    from app.api.studio_resource_widgets import resource_widget

    base = f"/studio/products/{product_id}"
    stage_labels = {
        "queued": "В очереди",
        "running": "Создание",
        "waiting_approval": "Ожидает разрешения",
        "completed": "Задание завершено",
        "failed": "Ошибка",
        "cancelled": "Отменено",
    }
    cards = []
    for entry in result.records:
        task, article = entry["task"], entry["article"]
        title = article.title if article else "SEO-статья — материал пока не готов"
        task_state = stage_labels.get(getattr(task.status, "value", task.status), "Неизвестно")
        artifact_state = (
            "На согласовании" if article and article.status.value == "awaiting_approval"
            else "Согласовано" if article and article.status.value == "approved"
            else "Опубликовано" if entry["published"] else
            "Черновик" if article and article.status.value == "draft" else
            "Текст найден, статус требует проверки" if article else "Ещё нет результата"
        )
        critic = ("Есть запись проверки (не означает допуск)"
                  if entry["critic_recorded"] else "Результат проверки не найден")
        release = ("Публикация подтверждена" if entry["published"] else
                   "Публикация не подтверждена")
        href = (f"{base}/materials/{article.id}" if article
                else f"{base}/tasks/{task.id}")
        primary = (
            '<div class="sg-review-item">'
            '<div class="sg-review-top"><span class="sg-tag">SEO-статья</span>'
            f'<span class="sg-channel">{esc(entry["status"])}</span></div>'
            f'<h3>{esc(title)}</h3>'
            f'<div class="sg-review-footer"><span>{esc(task_state)}</span>'
            f'<a class="btn secondary" href="{href}">Открыть {("статью" if article else "задачу")}</a>'
            '</div></div>'
        )
        stages = (
            "Задание: " + task_state + ". "
            "Материал: " + artifact_state + ". "
            "ИИ-проверка: " + critic + ". "
            "Публикация: " + release + "."
        )
        cards.append(resource_widget(
            kind="material", primary_html=primary,
            description=stages, note="Согласование статьи не равно её публикации.",
            detail_label="Этапы подготовки",
        ))
    empty = ('<p class="sg-review-empty">Задач SEO-завода по производству статей '
             'для этой компании пока не найдено.</p>')
    return (
        '<section class="sg-surface sg-review-page">'
        + review_section_nav(product_id, "seo")
        + '<div class="sg-section-head"><div>'
        '<span class="sg-overline">SEO-ЗАВОД</span>'
        '<h2>Статусы SEO-статей</h2>'
        '<p>Задание, материал, проверка и публикация — отдельные подтверждаемые этапы.</p>'
        '</div>'
        f'<span class="sg-count">Заданий: {result.total}</span></div>'
        f'<div class="sg-review-list">{"".join(cards) or empty}</div>'
        + _review_paging(product_id, "seo", result.page, result.page_count)
        + '</section>'
    )
