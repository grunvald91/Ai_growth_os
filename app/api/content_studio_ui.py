"""Presentation helpers for Growth Studio's task-first interface.

Only owner-authenticated route handlers may call these templates. Every
dynamic string is HTML escaped; no backend side effects occur here.
"""
from __future__ import annotations
import html
import json
from datetime import datetime
from urllib.parse import quote

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)

FORMAT_CHOICES = [
    ("post", "Пост", "Готовый текст для одной площадки"),
    ("short_video", "Короткое видео · сценарий", "Сценарий, кадры и озвучка — не MP4"),
    ("story", "Stories", "Последовательность экранов — не готовые изображения"),
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

def create_content_html(product_id, *, created=False, fields=None, error=None) -> str:
    fields = fields or {}
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
    return f"""
    <section class="sg-surface sg-animator-entry sg-animator-top">
      <div><span class="sg-overline">НОВЫЙ ФОРМАТ · ВИДЕО С ОЗВУЧКОЙ</span>
        <h2>Рисованное видеообъяснение</h2>
        <p>Закажите анимированный ролик. Сценарий, рисунки, голос и готовый MP4
        будут доступны в отдельном задании.</p>
        <small>Платные API-вызовы — только после вашего подтверждения.</small>
      </div>
      <a class="btn primary" href="{base}/whiteboard">Создать рисованное видео →</a>
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
            <label class="sg-field sg-wide"><span>Дополнительные требования <em>· необязательно</em></span>
              <textarea name="editorial_brief" maxlength="3500" rows="3"
                placeholder="Тон, особенности услуги, что нельзя обещать, нужный призыв">{esc(fields.get('editorial_brief', ''))}</textarea>
            </label>
          </div>
          <div class="sg-form-bottom">
            <div class="sg-form-hint"><span aria-hidden="true">✓</span> Создание не публикует материал.
            После подготовки можно проверить, изменить и утвердить.</div>
            <button class="btn primary sg-primary-action" type="submit">Создать материал <span aria-hidden="true">→</span></button>
          </div>
        </form>
      </section>
      <aside class="sg-aside-stack">
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

def review_html(product_id, records, *, filter_name="all") -> str:
    base=f"/studio/products/{product_id}"
    tabs=[]
    types=[("all","Всё"),("text","Тексты и статьи"),("creative","Сценарии и визуалы")]
    for value,label in types:
        tabs.append(f'<a class="sg-filter {"is-active" if value==filter_name else ""}" '
                    f'href="{base}/review?kind={value}">{label}</a>')
    cards=[]
    for row in records:
        try: data=json.loads(row.content)
        except (ValueError,TypeError): data={}
        is_creative=row.kind in {"creative_brief", "video_script", "video_render"}
        if filter_name=="text" and is_creative: continue
        if filter_name=="creative" and not is_creative: continue
        preview=(data.get("body") or data.get("preview_text") or
                 data.get("full_article") or data.get("reader_article") or
                 (data.get("production_brief") or {}).get("voiceover_or_dialogue_script") or "")
        if not isinstance(preview, str):
            preview=str(preview)
        preview=preview[:320]
        channel=data.get("platform") or data.get("channel") or "Материал"
        kind={"creative_brief":"Креативный бриф", "video_script":"Сценарий", "video_render":"Готовый MP4",
              "article":"Статья", "channel_text":"Пост"}.get(row.kind, "Материал")
        action=f'{base}/materials/{row.id}'
        approve=(f'{base}/review/{row.id}/approve')
        warnings=data.get("quality_warnings") or []
        warning=f'<span class="sg-warning">Проверить: {len(warnings)}</span>' if warnings else ""
        cards.append(f"""
          <article class="sg-review-item">
            <div class="sg-review-top"><span class="sg-tag">{esc(kind)}</span>
            <span class="sg-channel">{esc(channel)}</span>{warning}</div>
            <h3>{esc(row.title)}</h3><p>{esc(preview)}</p>
            <div class="sg-review-footer"><span>Ожидает решения</span>
              <div class="sg-inline-actions">
                <a class="btn secondary" href="{action}">Открыть и исправить</a>
                <form method="post" action="{approve}">
                  {'<a class="btn primary" href="' + action + '">Проверить факты</a>' if data.get('fact_check_required') else '<button class="btn primary" type="submit">Утвердить ✓</button>'}
                </form>
              </div>
            </div>
          </article>""")
    empty_title = "В этом разделе нет материалов" if filter_name != "all" else "Материалы проверены"
    empty_note = "Попробуйте фильтр «Всё»." if filter_name != "all" else "Сейчас ничего не требует согласования."
    empty=f'<div class="sg-empty"><div>✓</div><h3>{empty_title}</h3><p>{empty_note}</p><a class="btn primary" href="{base}/create">Создать материал →</a></div>'
    return (f'<section class="sg-surface"><div class="sg-section-head"><div>'
            f'<span class="sg-overline">ВАШЕ ВНИМАНИЕ</span><h2>Очередь согласования</h2>'
            f'<p>Сначала проверяйте материал, затем разрешайте публикацию.</p></div>'
            f'<span class="sg-count">{len(records)} на проверке</span></div>'
            f'<div class="sg-filters">{"".join(tabs)}</div>'
            f'<div class="sg-review-list">{"".join(cards) or empty}</div></section>')

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

