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


def render_v4(page, request, pid, switch):
    original = Document(page)
    main = original.find(tag='main')
    if not main:
        # Some administrative handlers return a body without a main landmark.
        main = original.find(tag='body')
    content = original.inner(main).replace('class="topbar"','class="v4-module-head"')
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
             ('work','Задания',base+'/work','clock'),('review','Согласование',base+'/review','check'),
             ('materials','Материалы',base+'/materials','folder'),('calendar','Календарь',base+'/calendar','calendar'),
             ('campaigns','Кампании',base+'/campaigns','layers'),('seo','SEO-завод',base+'/seo','globe')]
    from app.api.content_studio import more_links
    if pid:
        seen = {link[2] for link in links}
        glyphs = {'whiteboard':'video','mail':'mail','team':'user','intelligence':'search','analytics':'chart','profile':'settings'}
        links += [(key, 'Простое видео' if key=='whiteboard' else label, href, glyphs.get(key,'layers'))
                  for key,href,_,label in more_links(pid) if href not in seen]
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
        form = form.replace(mic, '').replace('id="dp-input"', 'id="dp-input" aria-describedby="dp-press-hint"', 1)
        attrs = ' '.join(f'{k}="{html.escape(v or "",quote=True)}"' for k,v in root[1].items() if k!='class')
        content = (f'<section class="dp-home empty-hero animate-in" {attrs}>'
                   '<div class="hero-intro"><div class="hero-eyebrow">ServGo · ваш AI-помощник</div>'
                   '<h1 class="hero-h">Добрый день</h1><p class="hero-desc">Скажите, что нужно сделать. '
                   'Помогу создать контент, запустить работу команды или разобраться в показателях бизнеса.</p></div>'
                   '<button class="dp-new link" id="dp-new" type="button">Новая тема</button>'
                   +inner.outer(inner.find(ident='dp-feed'))+
                   '<div class="command-dock">'+inner.outer(inner.find(ident='dp-live'))+
                   inner.outer(inner.find(ident='dp-voice-note'))+form+
                   '<p id="dp-press-hint" class="dp-press-hint">Удерживайте строку, чтобы говорить</p>'
                   '<div class="command-hints" id="dp-chips" aria-label="Подсказки"></div>'
                   '<div class="home-hero-subnote">Результаты и следующие шаги появятся после запроса</div></div></section>')
    if pid and current == base+'/create':
        form = original.outer(original.find(ident='sg-create-form'))
        if form:
            form = form.replace('class="sg-format-grid"', 'class="format-grid sg-format-grid"')
            form = form.replace('class="sg-choice"', 'class="format-option sg-choice"')
            warning = original.outer(original.find(cls='sg-warning'))
            content = ('<div class="page-head animate-in"><div><div class="eyebrow">Создание</div>'
                       '<h1 style="margin-top:7px">Что хотите создать?</h1>'
                       '<p class="subline">Выберите результат — или начните с голосовой команды.</p></div></div>'
                       '<section class="surface form-card animate-in"><h2>Формат</h2>'+warning+form+'</section>'
                       '<div class="module-grid mt14">'+''.join(
                           f'<a class="module-card surface" href="{href}"><div class="module-icon">{icon(glyph)}</div>'
                           f'<h3>{name}</h3><p>{desc}</p><span class="link">Открыть →</span></a>'
                           for name,href,glyph,desc in [
                               ('Простое видео',base+'/whiteboard','video','Объяснения и простая анимированная реклама · MP4'),
                               ('Контент-план',base+'/plan','calendar','Темы и публикации по датам'),
                               ('SEO-статья',base+'?command=seo_factory','globe','Поисковые запросы и собственный блог'),
                               ('Кампания',base+'/campaigns','layers','Цель, аудитория, каналы и показатели'),
                               ('Email-рассылка','https://servgo.ru/mail','mail','Письма и контакты в ServGo Mail'),
                               ('Разведка',f'/intelligence?product_id={pid}','search','Конкуренты, источники и гипотезы')])+'</div>')
    if pid and current == base+'/seo':
        stages = [('Поисковые вопросы','Подсказки Яндекса и Google','search'),
                  ('Семантическое ядро','Группировка по намерениям','layers'),
                  ('SEO-контент-план','Темы и поисковые запросы','calendar'),
                  ('Написание статей','Черновики для собственного блога','pen'),
                  ('Проверка критиком','Контроль качества итогового текста','shield'),
                  ('Публикация','После допуска — в блог','globe')]
        pipeline = '<section class="pipeline-card surface animate-in"><div class="pipeline-head"><h2>Этапы производства</h2><span class="chip">6 шагов</span></div>'+''.join(
            f'<div class="pipe-row"><span class="pipe-index">{i:02}</span><span style="flex:1"><b>{title}</b><p>{desc}</p></span>{icon(glyph)}</div>'
            for i,(title,desc,glyph) in enumerate(stages,1))+'</section>'
        content = content.replace('<div class="route-content">','<div class="route-content">'+pipeline,1)
    # The same reviewed card styles frame the real forms, reports and permissions.
    content = content.replace('class="panel"','class="surface module-card panel"')
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
    mail = next((r for r in links if r[0]=='mail'), links[-1])
    nav += link(mail,True)+f'<details><summary>{icon("layers")}<span>Ещё</span></summary><div class="v4-more">'+''.join(link(r) for r in links)+'</div></details>'
    template = template.replace('<nav class="mobile-nav" id="mobileNav"></nav>', '<nav class="mobile-nav" id="mobileNav" aria-label="Быстрая навигация">'+nav+'</nav>')
    template = template.replace('Единый интерфейс 4.0 · демоданные','Growth OS · интерфейс 4.0')
    template = template.replace('Демо · API не подключены', f'<a href="{switch}">Старый дизайн</a>')
    company_link = original.find(cls='sg-mobile-project')
    company = (company_link[1].get('aria-label', '').removeprefix('Сменить компанию: ')
               if company_link else 'Моя компания')
    template = template.replace('Студия «Мята»',html.escape(company)).replace('<span class="avatar">М</span>',
                     '<span class="avatar">'+html.escape(company[:1])+'</span>')
    template = template.replace('data-page="home"',f'onclick="location.href=\'{base}\'"')
    template = template.replace('<body>', '<body data-growth-design="v4"'+(' class="home-page home-empty"' if home else '')+'>')
    # Retain scripts and outside-main dialogs, avoiding duplicate inline scripts.
    extras = ''.join(original.outer(e) for e in original.elements if
                     e[0]=='script' and (not main or not main[3] <= e[2] < main[4]))
    extras += ''.join(original.outer(e) for e in original.elements if e[0]=='dialog' and not main[3] <= e[2] < main[4])
    extras += original.outer(original.find(ident='authOverlay'))
    if pid:
        button = Document(template).find(ident='notifyBtn')
        template = template.replace(Document(template).outer(button), notice_html(pid).replace('<span aria-hidden="true">🔔</span>', icon('bell')))
        template = template.replace('</head>', '<style>'+notice_css+'</style></head>')
        if '/studio/assets/studio.js' not in extras:
            extras += '<script>'+notice_js+'</script>'
    template = template.replace('</head>', (
        '<link rel="stylesheet" href="/studio/assets/growth-live.css?v=5">'
        '<link rel="stylesheet" href="/studio/assets/servgo-air-tokens.css?v=1">'
        '<link rel="stylesheet" href="/studio/assets/growth-servgo-air.css?v=1">'
        '<script src="/studio/assets/servgo-air-theme.js?v=1"></script>'
        '</head>'
    ))
    template = template.replace('</body>', extras+'<script src="/studio/assets/growth-live.js?v=4"></script></body>')
    return template
