"""The reviewed v4 document, with real Studio content and dispatcher controls.

Only presentation is assembled here. The original handlers execute first, so
validation, access scope, forms and all write policies retain their owners.
The prototype JavaScript and its fabricated records are never shipped here.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

ASSETS = Path(__file__).parent / 'assets'


class Document(HTMLParser):
    """Locate complete elements without regex matching nested HTML."""
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        for m in re.finditer('\n', source):
            self.lines.append(m.end())
        self.stack = []
        self.elements = []
        self.feed(source)

    def position(self):
        line, col = self.getpos()
        return self.lines[line-1] + col

    def handle_starttag(self, tag, attrs):
        if tag in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            return
        start = self.position()
        self.stack.append((tag, dict(attrs), start, start+len(self.get_starttag_text())))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                element = self.stack[i]
                self.elements.append((*element, self.position(), self.source.find('>', self.position())+1))
                del self.stack[i:]
                break

    def find(self, *, tag=None, cls=None, ident=None):
        return next((e for e in self.elements if
                     (not tag or e[0] == tag) and
                     (not cls or cls in (e[1].get('class') or '').split()) and
                     (not ident or e[1].get('id') == ident)), None)

    def inner(self, e):
        return self.source[e[3]:e[4]] if e else ''

    def outer(self, e):
        return self.source[e[2]:e[5]] if e else ''


def icon(name):
    return f'<svg class="icon" aria-hidden="true"><use href="#i-{name}"/></svg>'



def create_workspace_v4(pid, warning, voice_project="", voice_max=300):
    """Compact creation UI with the same dispatcher voice gesture as the owner home."""
    base = f"/studio/products/{pid}"
    formats = [
        ("post", "Пост", "Стратег и редактор создают текст для одной площадки."),
        ("article", "Статья", "Для статей пока используйте SEO-завод. Отдельный однократный режим статьи ещё не подключён."),
        ("story", "Stories", "Сценарий и готовые изображения каждого экрана. Доступны PNG и ZIP."),
        ("static_image", "Изображение", "Готовая картинка PNG с возможностью скачать файл."),
        ("short_video", "Видеосценарий", "Сценарий, кадры и озвучка, без готового MP4."),
        ("whiteboard", "Простое видео", "Анимированный MP4 с озвучкой и субтитрами; платный запуск подтверждается отдельно."),
        ("package", "Полный пакет", "Статья, адаптации и сценарии. Готовый MP4 пока в этот пакет не включён."),
    ]
    cards = ''.join(
        '<div class="sg-create-pick">'
        + (f'<a class="sg-create-format-link" href="{base}/seo">{html.escape(label)}</a>'
           if key == "article" else
           f'<button type="button" class="sg-create-format" data-sg-format="{key}" '
           f'aria-pressed="false">{html.escape(label)}</button>')
        + f'<button type="button" class="sg-create-info" data-sg-info="{html.escape(label)}" '
          f'data-sg-description="{html.escape(description, quote=True)}" '
          f'aria-label="О формате {html.escape(label)}">i</button></div>'
        for key, label, description in formats
    )
    tools = [
        ("Контент-план", base + "/plan", "Темы и сроки публикаций."),
        ("SEO-завод", base + "/seo", "Поисковые запросы, статьи и блог."),
        ("Исследование рынка", base + "/market-research", "Организации и конкуренты по данным Яндекс Карт."),
        ("Кампании", base + "/campaigns", "Цели, аудитории, каналы и результаты."),
        ("Разведка", f"/intelligence?product_id={pid}", "Конкуренты и маркетинговые гипотезы."),
    ]
    tool_rows = ''.join(
        f'<div class="sg-create-tool"><a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
        f'<button type="button" class="sg-create-info" data-sg-info="{html.escape(label)}" '
        f'data-sg-description="{html.escape(description,quote=True)}" '
        f'aria-label="Об инструменте {html.escape(label)}">i</button></div>'
        for label, href, description in tools
    )
    # V4 has one command path; classic Studio keeps the legacy /create POST.
    advanced = '''
      <div id="sg-create-settings" class="sg-create-settings">
        <label class="sg-create-setting" id="sg-create-platform-row" hidden>
          <span>Площадка</span>
          <select id="sg-create-platform" aria-label="Площадка"></select>
        </label>
        <label class="sg-create-setting" id="sg-create-count-row" hidden>
          <span>Количество экранов Stories</span>
          <select id="sg-create-count" aria-label="Количество экранов">
            <option value="">Автоматически (3–10)</option>
            ''' + ''.join(f'<option value="{n}">{n}</option>' for n in range(3, 11)) + '''
          </select>
        </label>
        <label class="sg-create-setting" id="sg-create-aspect-row" hidden>
          <span>Пропорции изображения</span>
          <select id="sg-create-aspect" aria-label="Пропорции изображения">
            <option value="">Автоматически по площадке</option>
            <option value="1:1">1:1 · квадрат</option>
            <option value="4:5">4:5 · вертикальный пост</option>
            <option value="9:16">9:16 · вертикальный экран</option>
            <option value="16:9">16:9 · горизонтальный</option>
          </select>
        </label>
        <p id="sg-create-story-ratio" class="sg-create-tip" hidden>Stories · вертикальные 9:16</p>
        <label class="sg-create-setting" id="sg-create-model-row" hidden>
          <span>Генерация изображений</span>
          <select id="sg-create-image-model" aria-label="Модель изображений">
            <option value="auto">Автоматически · одна модель</option>
            <option value="gemini">Gemini</option>
            <option value="openai">ChatGPT Image</option>
          </select>
        </label>
        <label class="sg-create-setting">
          <span>Аудитория <small>· необязательно</small></span>
          <input id="sg-create-audience" maxlength="300" placeholder="Для кого готовим материал?">
        </label>
        <label class="sg-create-setting">
          <span>Дополнительные требования <small>· необязательно</small></span>
          <textarea id="sg-create-brief" maxlength="3500" rows="2"
            placeholder="Тон, важные условия, что нельзя обещать…"></textarea>
        </label>
        <p class="sg-create-tip">Параметры попадут в карточку диспетчера.
          Работа начнётся только после вашего подтверждения.</p>
      </div>
    '''
    return f'''
    <section class="sg-create-screen">
      <div class="sg-create-scroll">
        <header class="sg-create-intro">
          <h1>Создать</h1>
          <p><b>Ваша ИИ-команда готовит материалы для бизнеса.</b> Используем профиль
          компании, доступные исследования аудитории и проверяем результат перед публикацией.</p>
          <div class="sg-create-intro-links">
            <button type="button" data-sg-about>Как это работает <span aria-hidden="true">ⓘ</span></button>
            <button type="button" data-sg-suggest>✦ Предложить тему</button>
          </div>
        </header>
        <div class="sg-create-info-panel" id="sg-create-info-panel" role="note" hidden></div>
        <div class="sg-create-suggestions" id="sg-create-suggestions" hidden></div>
        <div class="sg-create-label">Что создаём?</div>
        <div class="sg-create-picks">{cards}</div>
        <details class="sg-create-advanced" id="sg-create-advanced">
          <summary>Сценарий и подробности</summary>
          <p class="sg-create-tip">Нужны особые требования, аудитория или площадка?
          Укажите их здесь либо просто опишите задачу в строке ниже.</p>
          {warning}{advanced}
        </details>
        <details class="sg-create-timing" id="sg-create-timing">
          <summary>Сколько занимает создание?</summary>
          <p>Зависит от формата, выбранной модели и количества материалов.
          После подтверждения следите за фактическим статусом на странице задания;
          точное время заранее не гарантируется.</p>
        </details>
        <details class="sg-create-extra">
          <summary>Другие инструменты</summary>
          <div class="sg-create-tools">{tool_rows}</div>
        </details>
        <div class="sg-create-context" id="sg-create-context" aria-live="polite"></div>
        <section class="dp-home sg-create-dispatch" data-create-format=""
          data-api="{base}/dispatch" data-voice-url="/studio/voice/dictate"
          data-voice-preview-url="/studio/voice/preview" data-voice-max="{int(voice_max)}"
          data-voice-project="{html.escape(voice_project,quote=True)}"
          aria-label="Создание через диспетчера">
          <div class="sg-create-history" id="sg-create-history" aria-label="История сообщений">
            <button type="button" class="sg-create-history-close" id="sg-create-history-close"
              aria-label="Свернуть историю сообщений" aria-controls="dp-feed" aria-expanded="true">
              <span class="sg-create-history-grip" aria-hidden="true"></span>
            </button>
            <div class="dp-feed" id="dp-feed" role="log" aria-live="polite"
              aria-label="Ответы диспетчера"></div>
          </div>
          <div class="command-dock">
            <div class="voice-hold-overlay vg" id="dp-voice-overlay" aria-hidden="true">
              <div class="vg__glow"></div>
              <div class="vg__label" id="dp-voice-overlay-text">Отпустите, чтобы отправить</div>
              <canvas class="vg__bars" id="vgBars" aria-hidden="true"></canvas>
              <div class="vg__knob" id="vgKnob" aria-hidden="true"></div>
            </div>
            <div class="dp-live" id="dp-live" hidden>
              <span id="dp-live-label">Слушаю…</span><p id="dp-live-text"></p>
            </div>
            <p class="dp-voice-note" id="dp-voice-note" role="status" aria-live="polite" hidden></p>
            <div class="command-topicbar">
              <button class="dp-new" id="dp-new" type="button">{icon("plus")}<span>Новая тема</span></button>
            </div>
            <button type="button" class="sg-create-history-open" id="sg-create-history-open"
              aria-label="Развернуть историю сообщений" aria-controls="dp-feed" aria-expanded="false">
              <span class="sg-create-history-grip" aria-hidden="true"></span>
            </button>
            <form class="dp-form command-bar" id="dp-form" autocomplete="off">
              <label class="dp-sr" for="dp-input">Напишите или скажите, что создать</label>
              <textarea id="dp-input" class="dp-input" rows="1"
                placeholder="Нажмите — текст · удерживайте — голос" enterkeyhint="send"></textarea>
              <span class="voice-hold-hitbox" id="dp-voice-hitbox" aria-hidden="true"></span>
              <span class="voice-composer-status" id="dp-voice-status" role="status" hidden></span>
              <button class="command-mode-mic" id="dp-mode-mic" type="button" hidden
                 aria-label="Скрыть клавиатуру и перейти к голосовому вводу"
                 title="Вернуться к голосовому вводу"><svg aria-hidden="true" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <rect x="9" y="2.5" width="6" height="12" rx="3"/>
                <path d="M5.5 10.5v1.5a6.5 6.5 0 0 0 13 0v-1.5M12 18.5V22M8.5 22h7"/>
              </svg></button>
              <button class="dp-send command-go" id="dp-send" type="submit"
                aria-label="Отправить запрос"><span aria-hidden="true">↑</span></button>
            </form>
            <div class="command-tools"><div class="command-hints" id="dp-chips"
              aria-label="Подсказки"></div></div>
          </div>
        </section>
      </div>
    </section>
    <script type="application/json" id="dp-state">{{"messages":[],"hints":[]}}</script>
    '''

def render_v4(page, request, pid, switch):
    original = Document(page)
    main = original.find(tag='main')
    if not main:
        # Some administrative handlers return a body without a main landmark.
        main = original.find(tag='body')
    content = original.inner(main).replace('class="topbar"','class="v4-module-head"')
    # Keep special page-level wrappers when transplanting old <main> content:
    # otherwise v4 drops their class, so dark-mode selector styles never apply.
    main_classes = set((main[1].get('class') or '').split())
    wrapper = next((item for item in ('sg-company-picker', 'sg-link-page')
                    if item in main_classes), None)
    if wrapper:
        content = f'<div class="{wrapper}">{content}</div>'
    if request.url.path.startswith('/studio/tune'):
        content = original.outer(original.find(tag='header'))+content
    template = (ASSETS/'growth_os_v4_preview.html').read_text()
    # Reuse the reviewed CSS, SVG symbols and structural markup verbatim.
    template = template[:template.index('<script>')] + '</body></html>'
    doc = Document(template)
    base = f'/studio/products/{pid}' if pid else '/studio'
    current = request.url.path
    home = bool(pid and current == base and original.find(cls='dp-home'))
    links = [('home','Главная',base,'home'),('create','Создать',base+'/create','plus'),
             ('work','Задания',base+'/work','clock'),('calendar','Календарь',base+'/calendar','calendar'),
             ('seo','SEO-завод',base+'/seo','globe'),
             ('plan','Контент-план',base+'/plan','layers'),
             ('materials','Библиотека',base+'/materials','folder'),
             ('channels','Каналы и публикации',base+'/channels','layers'),
             ('campaigns','Кампании',base+'/campaigns','layers'),
             ('review','Согласование',base+'/review','check')]
    from app.api.content_studio import more_links
    if pid:
        seen = {link[2] for link in links}
        glyphs = {'whiteboard':'video','mail':'mail','team':'user','intelligence':'search','analytics':'chart','profile':'settings'}
        links += [(key, label, href, glyphs.get(key,'layers'))
                  for key,href,_,label in more_links(pid)
                  if href not in seen and key not in {'whiteboard', 'switch'}]
    else:
        links = [('companies','Компании','/studio?choose=1','layers'),('team','ИИ-команда','/studio/team','user')]

    def link(row, mobile=False):
        key,label,href,glyph = row
        active = current == href.split('?')[0] and ('?' not in href or str(request.url.query) == href.split('?')[1])
        return (f'<a class="{"active" if active else ""} {"" if mobile else "nav-btn"}" '
                f'href="{html.escape(href,quote=True)}"'+(' aria-current="page"' if active else '')+
                f'>{icon(glyph)}<span>{html.escape(label)}</span></a>')

    title = original.find(tag='h1')
    label = 'Главная' if home else html.unescape(re.sub('<[^>]+>', '', original.inner(title))) or 'Growth OS'
    if home:
        root = original.find(cls='dp-home')
        # Preserve every dispatcher node/id and its voice configuration, changing
        # the composition to v4 instead of restyling the old chat layout.
        inner = Document(original.inner(root))
        form = inner.outer(inner.find(ident='dp-form')).replace('class="dp-form"','class="dp-form command-bar"')
        form = form.replace('class="dp-send"','class="dp-send command-go"')
        # The whole text composer handles hold-to-talk in v4. Classic Studio keeps its mic.
        formdoc = Document(form)
        mic = formdoc.outer(formdoc.find(ident='dp-mic'))
        form = form.replace(mic, '').replace('placeholder="Что нужно сделать?"', 'placeholder="Нажмите — текст · удерживайте — голос"', 1)
        # An empty, non-selectable touch surface intercepts Android's native
        # long-press text selection, while a short tap still focuses the editor.
        form = form.replace('</textarea>',
                            '</textarea><span class="voice-hold-hitbox" id="dp-voice-hitbox" aria-hidden="true"></span>', 1)
        form = form.replace('<button class="dp-send command-go"',
                            '<span class="voice-composer-status" id="dp-voice-status" role="status" hidden></span>'
                            '<button class="command-mode-mic" id="dp-mode-mic" type="button" hidden '
                            'aria-label="Скрыть клавиатуру и перейти к голосовому вводу" '
                            'title="Вернуться к голосовому вводу">'
                            '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                            '<rect x="9" y="2.5" width="6" height="12" rx="3"/>'
                            '<path d="M5.5 10.5v1.5a6.5 6.5 0 0 0 13 0v-1.5M12 18.5V22M8.5 22h7"/>'
                            '</svg></button>'
                            '<button class="dp-send command-go"', 1)
        attrs = ' '.join(f'{k}="{html.escape(v or "",quote=True)}"' for k,v in root[1].items() if k!='class')
        content = (f'<section class="dp-home empty-hero animate-in" {attrs}>'
                   '<div class="hero-intro"><div class="hero-eyebrow">ServGo · ваш AI-помощник</div>'
                   '<h1 class="hero-h">Добрый день</h1><p class="hero-desc">Скажите, что нужно сделать. '
                   'Помогу создать контент, запустить работу команды или разобраться в показателях бизнеса.</p></div>'
                   +inner.outer(inner.find(ident='dp-feed'))+
                   '<div class="command-dock">'
                   '<div class="voice-hold-overlay vg" id="dp-voice-overlay" aria-hidden="true">'
                   '<div class="vg__glow"></div>'
                   '<div class="vg__label" id="dp-voice-overlay-text">Отпустите, чтобы отправить</div>'
                   '<canvas class="vg__bars" id="vgBars" aria-hidden="true"></canvas>'
                   '<div class="vg__knob" id="vgKnob" aria-hidden="true"></div>'
                   '</div>'+inner.outer(inner.find(ident='dp-live'))+
                   inner.outer(inner.find(ident='dp-voice-note'))+
                   '<div class="command-topicbar">'
                   '<button class="dp-new" id="dp-new" type="button" aria-label="Новая тема" title="Начать новый диалог">'
                   f'{icon("plus")}<span class="dp-new-label">Новая тема</span></button>'
                   '</div>'+form+
                   '<div class="command-tools">'
                   '<div class="command-hints" id="dp-chips" aria-label="Подсказки"></div>'
                   '</div>'
                   '<div class="home-hero-subnote">Результаты и следующие шаги появятся после запроса</div></div></section>')
    if pid and current == base+'/create':
        if original.find(ident='sg-create-form'):
            warning = original.outer(original.find(cls='sg-warning'))
            config = original.find(cls='sg-create-voice-config')
            config_attrs = config[1] if config else {}
            content = create_workspace_v4(
                pid, warning,
                voice_project=config_attrs.get('data-voice-project') or '',
                voice_max=int(config_attrs.get('data-voice-max') or 300),
            )
    if pid and current == base+'/seo':
        steps = [
            ('Поисковые вопросы', 'Подсказки Яндекса и Google'),
            ('Семантическое ядро', 'Группировка запросов'),
            ('Контент-план', 'Темы и даты публикации'),
            ('Написание статьи', 'Текст для блога'),
            ('ИИ-критик', 'Проверка содержания и фактов'),
            ('Публикация', 'После допуска — в блог'),
        ]
        pipeline = (
            '<details class="seo-process-overview">'
            '<summary>Как создаётся статья <span>6 этапов</span></summary>'
            '<ol class="seo-steps">'
            + ''.join(f'<li><strong>{html.escape(name)}</strong>'
                      f'<small>{html.escape(description)}</small></li>'
                      for name, description in steps)
            + '</ol></details>'
        )
        # Status and the enable button come first. The production steps are
        # optional reference information, not a giant card above the controls.
        at = content.find('<section class="panel seo-activation"')
        after = content.find('</section>', at) if at >= 0 else -1
        if after >= 0:
            after += len('</section>')
            content = content[:after] + pipeline + content[after:]
    # The same reviewed card styles frame the real forms, reports and permissions.
    if pid and current == base+'/seo':
        content = content.replace('class="panel"', 'class="surface panel"')
    else:
        content = content.replace('class="panel"', 'class="surface module-card panel"')
    if pid and current == base+'/materials':
        content = content.replace('class="material-grid route-material-grid"', 'class="materials-grid"')
        content = content.replace('class="material-card"', 'class="material-card surface animate-in"')
    # One real notification widget shared by mobile and desktop, before scripts run.
    from app.api.content_studio_activity import notice_html
    from app.api.content_studio_work_ui import CSS as notice_css, JS as notice_js
    notice = original.find(cls='sg-global-status')
    if notice:
        content = content.replace(original.outer(notice), '')
    title_tag = original.outer(original.find(tag='title')) or '<title>Growth OS</title>'
    styles = ''.join(m.group() for m in re.finditer(r'<link\b[^>]*rel="stylesheet"[^>]*>', page))
    styles = re.sub(r'<link[^>]*dispatcher\.css[^>]*>', '', styles)
    # Page-specific form styles and scripts belong to their original modules.
    styles += ''.join(original.outer(e) for e in original.elements if e[0]=='style')
    replacements = [(doc.find(ident='main'), content), (doc.find(ident='leftNav'), ''.join(link(r) for r in links)),
                    (doc.find(ident='crumbTitle'), html.escape(label)),
                    (doc.find(ident='rail'), '<div class="rail-head"><h2>Ваш помощник</h2><span>ServGo AI</span></div>'
                     '<div class="assistant-box"><div class="assistant-avatar">'+icon('spark')+'</div><h3>Что нужно сделать?</h3>'
                     '<p>Опишите задачу обычными словами. Команда предложит следующий шаг.</p>'
                     f'<a class="btn blue" href="{base}">Открыть помощника</a></div>'+''.join('<div class="rail-card">'+link(r)+'</div>' for r in links[:4]))]
    # Offsets belong to the pristine document: apply before title/styles edits.
    pristine = template
    for e,value in sorted(replacements,key=lambda x:x[0][3],reverse=True):
        pristine = pristine[:e[3]]+value+pristine[e[4]:]
    template = re.sub(r'<title>.*?</title>', lambda _:title_tag, pristine,count=1,flags=re.S)
    template = template.replace('<style>', styles+'<style>',1)
    nav = ''.join(link(r,True) for r in links[:3])
    if pid:
        calendar = next((r for r in links if r[0] == 'calendar'), None)
        if calendar:
            nav += link(calendar, True)
    by_key = {r[0]: r for r in links}
    more_groups = [
        ('Контент и продвижение', ('seo','plan','materials','channels','campaigns','comments')),
        ('Аналитика и исследования', ('analytics','intelligence')),
        ('ИИ-команда', ('team','tune','speed')),
        ('Компания и управление', ('profile','accounts','servgo','logout')),
    ]
    more_html = ''.join(
        '<section class="sg-v4-more-group"><strong>'+html.escape(title)+'</strong>'
        + ''.join(link(by_key[key]) for key in keys if key in by_key)+'</section>'
        for title,keys in more_groups
    ) if pid else ''.join(link(r) for r in links)
    theme_button = ('<button type="button" class="nav-btn sg-theme-action" data-sg-theme>'
                    + icon('sun') + '<span>Тема оформления</span></button>')
    template = template.replace('</nav>', theme_button + '</nav>', 1)
    more_html += '<section class="sg-v4-more-group"><strong>Оформление</strong>'+theme_button+'</section>'
    nav += f'<details><summary>{icon("more")}<span>Ещё</span></summary><div class="v4-more">'+more_html+'</div></details>'
    template = template.replace('<nav class="mobile-nav" id="mobileNav"></nav>', '<nav class="mobile-nav" id="mobileNav" aria-label="Быстрая навигация">'+nav+'</nav>')
    template = template.replace('Единый интерфейс 4.0 · демоданные','Growth OS · интерфейс 4.0')
    # Shared legacy handlers still own validation and permissions, but their
    # migration switch is no longer part of the new owner interface.
    template = re.sub(r'<span class="chip"[^>]*>Демо · API не подключены</span>', '', template, count=1)
    for control_id in ('themeTop', 'openSearch'):
        controls = Document(template)
        template = template.replace(controls.outer(controls.find(ident=control_id)), '', 1)
    template = template.replace('</defs>',
        '<symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/>'
        '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></symbol></defs>', 1)
    company_link = original.find(cls='sg-mobile-project')
    company = (company_link[1].get('aria-label', '').removeprefix('Сменить компанию: ')
               if company_link else ('Выберите компанию' if pid is None and current == '/studio' else 'Моя компания'))
    template = template.replace('Студия «Мята»',html.escape(company)).replace('<span class="avatar">М</span>',
                     '<span class="avatar">'+html.escape(company[:1])+'</span>')
    # Keep the header switch in place; populate a scoped dropdown on click.
    if pid:
        template = template.replace('<button class="biz-switch" id="bizBtn">',
            '<div class="sg-org-wrapper" id="sgOrgWrapper">'
            '<button class="biz-switch" id="bizBtn" type="button" '
            'aria-label="Сменить компанию" aria-haspopup="true" aria-expanded="false" '
            'aria-controls="sgOrgMenu">', 1)
        template = template.replace('<svg class="icon sm"><use href="#i-down"/></svg></button>',
            '<svg class="icon sm"><use href="#i-down"/></svg></button>'
            '<div class="sg-org-menu" id="sgOrgMenu" hidden></div></div>', 1)
    # Always display the active company name on mobile, not only an initial.
    template = template.replace('</head>',
        '<style>@media(max-width:780px){'
        '.biz-switch{min-height:38px;max-width:min(33vw,165px)}'
        '.biz-switch .avatar{display:none}'
        '.biz-switch>span:not(.avatar){display:block;min-width:0;max-width:100%}'
        '.biz-switch strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        '.biz-switch small{display:none}.biz-switch .icon{display:block}'
        '}</style></head>', 1)
    template = template.replace('data-page="home"',f'onclick="location.href=\'{base}\'"')
    template = template.replace('<body>', '<body data-growth-design="v4"'+(' class="home-page home-empty"' if home else '')+'>')
    # Retain scripts and outside-main dialogs, avoiding duplicate inline scripts.
    extras = ''.join(original.outer(e) for e in original.elements if
                     e[0]=='script' and (not main or not main[3] <= e[2] < main[4]))
    extras += ''.join(original.outer(e) for e in original.elements if e[0]=='dialog' and not main[3] <= e[2] < main[4])
    extras += original.outer(original.find(ident='authOverlay'))
    # Bust the voice controller cache only in v4; the classic Studio UX is unchanged.
    if home:
        extras = re.sub(r'(/studio/assets/dispatcher\.js\?v=)[^"&]+', r'\g<1>light-polish-20261105', extras)
    if pid and current == base+'/create':
        extras += '<script src="/studio/assets/create-studio.js?v=history-20261104"></script>'
        extras += '<script src="/studio/assets/dispatcher.js?v=light-polish-20261105"></script>'
    if pid and (home or current == base+'/create'):
        # The glow measures the existing recorder stream; load before dispatcher.js.
        extras = '<script src="/studio/assets/voice-glow.js?v=2"></script>' + extras
    if pid:
        button = Document(template).find(ident='notifyBtn')
        template = template.replace(Document(template).outer(button), notice_html(pid).replace('<span aria-hidden="true">🔔</span>', icon('bell')))
        template = template.replace('</head>', '<style>'+notice_css+'</style></head>')
        if '/studio/assets/studio.js' not in extras:
            extras += '<script>'+notice_js+'</script>'
    # ServGo is one same-origin installed PWA (manifest scope="/"), not a
    # second Growth-specific worker or a cache for private Studio pages.
    template = template.replace('</head>', (
        '<meta name="theme-color" content="#ffffff">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
        '<meta name="apple-mobile-web-app-title" content="ServGo">'
        '<link rel="manifest" href="/manifest.webmanifest">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        '<link rel="stylesheet" href="/studio/assets/growth-daily.css?v=4">'
        '</head>'
    ), 1)
    if home:
        drawer = (
            '<button id="sgDailyHandle" class="sg-daily-handle" type="button" '
            'aria-label="Сводка на сегодня" aria-controls="sgDailyDrawer" aria-expanded="false">'
            '<span class="sg-daily-tab" aria-hidden="true">'
            + icon('calendar') + '<span>Сегодня</span></span></button>'
            '<div id="sgDailyBackdrop" class="sg-daily-backdrop" aria-hidden="true"></div>'
            '<aside id="sgDailyDrawer" class="sg-daily-drawer" role="dialog" '
            'aria-modal="true" aria-label="Ежедневная сводка" aria-hidden="true" inert tabindex="-1">'
            '<div class="sg-daily-heading"><div><h2>Сводка на сегодня</h2>'
            '<small id="sgDailyCompany"></small></div>'
            '<button id="sgDailyClose" class="sg-daily-close" type="button" '
            'aria-label="Свернуть сводку" title="Свернуть">×</button></div>'
            '<div id="sgDailyContent" class="sg-daily-content"></div>'
            '<p class="sg-daily-hint">Нажмите вне панели, чтобы свернуть.</p></aside>'
        )
        template = template.replace('</body>', drawer+'</body>', 1)
    if pid:
        extras += '<script src="/studio/assets/growth-daily.js?v=2"></script>'
    template = template.replace('</head>', (
        '<link rel="stylesheet" href="/studio/assets/growth-live.css?v=20">'
        '<link rel="stylesheet" href="/studio/assets/servgo-air-tokens.css?v=1">'
        '<link rel="stylesheet" href="/studio/assets/growth-servgo-air.css?v=10">'
        '<script src="/studio/assets/servgo-air-theme.js?v=1"></script>'
        '</head>'
    ))
    template = template.replace('</body>', extras+'<script src="/studio/assets/growth-live.js?v=12"></script></body>')
    return template
