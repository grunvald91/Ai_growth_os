"""Схема команды для владельца: кто в ней работает, что кому передаёт и где подключается критик.

Только чтение и только то, что есть в коде:
  * список агентов и их описания берутся из каталога `team_dashboard.role_descriptors()`
    (все роли `AgentRole` плюс стадии конвейеров, которые в нём не перечислены);
  * порядок шагов статьи берётся из `article_production.STAGES`;
  * состояние критика читается из настройки сервера `agent_critic_enabled`.
Тест сверяет схему с этими источниками, поэтому она не разойдётся с кодом тихо.
"""
from __future__ import annotations

import html
from uuid import UUID

# Роли, которые запускаются в Studio, но не входят в каталог `team_dashboard`.
EXTRA_AGENTS = {
    "post_strategist": ("Стратег поста", "Выбирает зацепку, главную мысль и призыв для поста."),
    "post_writer_editor": ("Автор и редактор поста", "Пишет пост и сам вычитывает его."),
    "post_rewriter": ("Редактор правок", "Переписывает пост по вашим замечаниям."),
    "creative_strategist": ("Стратег видео", "Выясняет, кому и зачем снимаем, и выбирает подачу."),
    "editorial_planner": ("Планировщик контента", "Составляет контент-план по площадкам и датам."),
}

# Этапы рисованного видео: это сервисы, а не ИИ-агенты, поэтому в счётчик агентов не входят.
WHITEBOARD_STAGES = (
    ("Сценарий", "Раскадровка: что нарисовать и что сказать."),
    ("Рисунки", "Создаёт картинки для каждой сцены."),
    ("Зоны анимации", "Определяет, какие части рисунка «рисуются»."),
    ("Озвучка", "Озвучивает текст голосом."),
    ("Сборка видео", "Собирает готовый видеофайл."),
)

# Карточки-конвейеры. Шаг: "id" | ("id", "своя подпись") | ("group", "заголовок", "подпись", [ids]).
LANES = (
    {
        "key": "cmo", "short": "Директор и специалисты", "entry": "cmo", "icon": "◎", "title": "Маркетинговый директор и специалисты",
        "who": "Запускается по показателям бизнеса, а не по вашему заказу.",
        "blurb": "Директор разбирает цифры, решает: оставить, остановить, усилить или проверить идею, "
                 "и раздаёт задачи специалистам. Сам он ничего не делает руками.",
        "steps": [
            ("analytics", "Собирает показатели воронки. Выводов не делает."),
            ("cmo", "Решает по каждой идее и ставит задачи специалистам."),
            ("group", "Специалисты под задачу", "Берётся тот, кто нужен: исследование, статья, видео, распространение, конверсия.",
             ["research_pmm", "content_seo", "content_strategist", "seo_editor", "scriptwriter",
              "distribution", "community_manager", "cro"]),
            ("cmo", "Смотрит на результат и ставит следующие задачи."),
        ],
    },
    {
        "key": "article", "short": "Статья и пакет для площадок", "entry": "owner", "icon": "▤", "title": "Статья и материалы для площадок",
        "who": "Запускаете вы: «Создать материал» → полный пакет.",
        "blurb": "Главный конвейер. Каждый шаг получает результат предыдущего и дорабатывает его.",
        "steps": "ARTICLE",   # заполняется из article_production.STAGES
    },
    {
        "key": "post", "short": "Короткий пост", "entry": "owner", "icon": "✎", "title": "Короткий пост",
        "who": "Запускаете вы: «Создать материал» → пост.",
        "blurb": "Быстрый конвейер из двух шагов. Если вы просите поправить пост, подключается редактор правок.",
        "steps": ["post_strategist", "post_writer_editor", ("post_rewriter", "Подключается, только когда вы просите поправить пост.")],
    },
    {
        "key": "creative", "short": "Видео, Stories, картинка", "entry": "owner", "icon": "▶", "title": "Сценарий видео, Stories, картинка",
        "who": "Запускаете вы: «Создать материал» → видео, Stories или картинка.",
        "blurb": "Сначала выясняют аудиторию и подачу, потом пишут сценарий по кадрам. Это текст и раскадровка, не видеофайл.",
        "steps": ["creative_strategist", "creative_director"],
    },
    {
        "key": "plan", "short": "Контент-план", "entry": "owner", "icon": "▦", "title": "Контент-план",
        "who": "Запускаете вы или по расписанию.",
        "blurb": "Один агент составляет план по площадкам и датам. Пока вы не утвердили план, ничего по нему не готовится.",
        "steps": ["editorial_planner"],
    },
    {
        "key": "whiteboard", "short": "Рисованное видео", "entry": "owner", "icon": "✐", "title": "Рисованное видео",
        "who": "Запускаете вы. Перед платными вызовами спрашивает вашего согласия.",
        "blurb": "Не ИИ-агенты, а этапы с картинками и озвучкой. Для картинок предусмотрен отдельный визуальный критик.",
        "steps": "WHITEBOARD", "critic": "visual",
    },
    {
        "key": "research", "short": "Исследование рынка", "entry": "cmo", "icon": "⌕", "title": "Исследование рынка",
        "who": "Запускается по задаче от маркетингового директора.",
        "blurb": "Руководитель делит работу, четверо исследователей берут каждый свою часть, потом он собирает общие выводы.",
        "steps": [
            ("research_lead", "Делит исследование на части."),
            ("group", "Четыре исследователя", "Каждый работает только со своей частью и со своими источниками.",
             ["segment_researcher", "channel_researcher", "problem_researcher", "competitor_voc_researcher"]),
            ("research_lead", "Собирает выводы и проверяет, что они обоснованы."),
        ],
    },
    {
        "key": "niche", "short": "Изучение ниши и компании", "entry": "event", "icon": "☍", "title": "Изучение ниши и компании",
        "who": "Запускается, когда нужно пополнить данные.",
        "blurb": "Три агента работают независимо: собирают сведения о компании и ищут, что популярно в нише.",
        "steps": [("group", "Три независимых агента", "Результаты попадают в исследования и в задания для других агентов.",
                   ["company_intake", "viral_video_researcher", "viral_content_researcher"])],
    },
    {
        "key": "comments", "short": "Комментарии", "entry": "event", "icon": "☷", "title": "Комментарии под постами",
        "who": "Запускается, когда приходит комментарий.",
        "blurb": "Готовит черновик ответа. Отправляете его вы, сложные случаи агент передаёт человеку.",
        "steps": ["comment_sales"], "critic": "none",
    },
)

CRITIC_STEPS = (
    ("Агент делает черновик", "Свою часть работы, как обычно."),
    ("Критик ищет слабые места", "Выдуманные факты, лишние обещания, пропущенное из задания. Мелочи и вкус его не интересуют."),
    ("Агент решает сам", "По каждому замечанию: принять или отклонить, и объяснить почему. Критик только советует."),
    ("Одна правка и готово", "Не больше двух дополнительных обращений к модели на шаг. Спора по кругу нет."),
)

SHIELD = ('<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 2.5 4.5 5.5v5.8c0 4.6 3.1 8.2 7.5 10.2 '
          '4.4-2 7.5-5.6 7.5-10.2V5.5L12 2.5Zm-1.2 13.2-3-3 1.4-1.4 1.6 1.6 4.2-4.2 1.4 1.4-5.6 5.6Z" fill="currentColor"/></svg>')


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 100 in range(11, 15):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def catalog() -> dict[str, dict]:
    """id -> {name, text}: каталог ролей плюс стадии, которые есть только в Studio."""
    from app.api.team_dashboard import role_descriptors
    data = {row["id"]: {"name": row["name"], "text": row["description"]} for row in role_descriptors()}
    for key, (name, text) in EXTRA_AGENTS.items():
        data[key] = {"name": name, "text": text}
    return data


def article_steps() -> list:
    from app.workflows.article_production import STAGES
    steps = [name for name, _schema, _instruction in STAGES]
    steps += [("group", "Готовые тексты для площадок", "Добавляются при заказе полного пакета: статьи для Дзена и vc.ru, пост для TenChat и Telegram, FAQ.",
               ["platform_writer"]),
              ("group", "Сценарии коротких видео", "Для YouTube Shorts и Instagram Reels, по 30 секунд.",
               ["youtube_shorts_director", "instagram_reels_director"])]
    return steps


def lane_steps(lane: dict) -> list:
    if lane["steps"] == "ARTICLE":
        return article_steps()
    if lane["steps"] == "WHITEBOARD":
        return [("plain", title, text) for title, text in WHITEBOARD_STAGES]
    return list(lane["steps"])


def lane_agents(lane: dict) -> list[str]:
    seen: list[str] = []
    for step in lane_steps(lane):
        ids = step[3] if isinstance(step, tuple) and step[0] == "group" else (
            [] if isinstance(step, tuple) and step[0] == "plain" else [step[0] if isinstance(step, tuple) else step])
        for agent in ids:
            if agent not in seen:
                seen.append(agent)
    return seen


def all_placed_agents() -> list[str]:
    placed: list[str] = []
    for lane in LANES:
        for agent in lane_agents(lane):
            if agent not in placed:
                placed.append(agent)
    return placed


# ── отрисовка ──────────────────────────────────────────────────────────────
def _shield(on: bool) -> str:
    return (f'<span class="sg-shield" role="img" aria-label="Работу проверяет критик" title="Работу проверяет критик">{SHIELD}</span>'
            if on else "")


def _state_class(states: dict, agent: str) -> str:
    return {"completed": " done", "running": " run", "failed": " err", "queued": " wait"}.get(states.get(agent, ""), "")


def _node(n: int, agent: str, text: str | None, data: dict, critic: bool, states: dict) -> str:
    item = data.get(agent, {"name": agent, "text": ""})
    return (f'<li class="sg-node{_state_class(states, agent)}"><span class="sg-node-n">{n}</span>'
            f'<div class="sg-node-b"><b>{_e(item["name"])}</b><span>{_e(text or item["text"])}</span></div>{_shield(critic)}</li>')


def _group(n: int, title: str, text: str, ids: list[str], data: dict, critic: bool, states: dict) -> str:
    minis = "".join(
        f'<li class="sg-mini{_state_class(states, a)}"><b>{_e(data.get(a, {"name": a})["name"])}</b>'
        f'<span>{_e(data.get(a, {"text": ""})["text"])}</span>{_shield(critic)}</li>' for a in ids)
    return (f'<li class="sg-node sg-node-group"><span class="sg-node-n">{n}</span><div class="sg-node-b">'
            f'<b>{_e(title)}</b><span>{_e(text)}</span><ul class="sg-minis">{minis}</ul></div></li>')


ENTRIES = (
    ("owner", "Вы ставите задачу", "«Создать материал» на главной. Задание сразу идёт на нужный конвейер."),
    ("cmo", "Маркетинговый директор ставит задачи сам", "Разбирает показатели бизнеса и раздаёт работу специалистам."),
    ("event", "По событию", "Включается, когда нужно пополнить данные или пришёл комментарий."),
)


def _lane_count(lane: dict) -> str:
    count = len(lane_agents(lane))
    return f'{count} {_pl(count, "агент", "агента", "агентов")}' if count else f'{len(WHITEBOARD_STAGES)} этапов'


def _lane_marked(lane: dict, states: dict) -> bool:
    return any(a in states for a in lane_agents(lane))


def org_html(critic_enabled: bool, states: dict) -> str:
    blocks = []
    for key, title, text in ENTRIES:
        chips = []
        for lane in LANES:
            if lane["entry"] != key:
                continue
            shield = _shield(lane.get("critic", "agents") == "agents")
            mark = " hl" if _lane_marked(lane, states) else ""
            chips.append(f'<a class="sg-org-chip{mark}" href="#lane-{lane["key"]}" data-lane="{lane["key"]}">'
                         f'<span class="sg-org-ic" aria-hidden="true">{lane["icon"]}</span><b>{_e(lane["short"])}</b>'
                         f'<small>{_lane_count(lane)}</small>{shield}</a>')
        blocks.append(f'<div class="sg-org-entry sg-org-{key}"><header><b>{_e(title)}</b><span>{_e(text)}</span></header>'
                      f'<div class="sg-org-chips">{"".join(chips)}</div></div>')
    crit = "включён" if critic_enabled else "сейчас выключен"
    gap = '<div class="sg-org-gap" aria-hidden="true"></div>'
    return ('<div class="sg-org">' + gap.join(blocks)
            + f'<div class="sg-org-arrow"><i></i><span>на каждом шаге со щитом работу проверяет критик ({crit})</span></div>'
            '<div class="sg-org-end"><b>Готовый материал ждёт вашего «Утвердить»</b>'
            '<span>Сами агенты ничего не публикуют.</span></div></div>')


def lane_html(lane: dict, data: dict, states: dict) -> str:
    mode = lane.get("critic", "agents")
    critic = mode == "agents"
    nodes, n = [], 0
    for step in lane_steps(lane):
        n += 1
        if isinstance(step, tuple) and step[0] == "group":
            nodes.append(_group(n, step[1], step[2], step[3], data, critic, states))
        elif isinstance(step, tuple) and step[0] == "plain":
            nodes.append(f'<li class="sg-node"><span class="sg-node-n">{n}</span><div class="sg-node-b">'
                         f'<b>{_e(step[1])}</b><span>{_e(step[2])}</span></div></li>')
        elif isinstance(step, tuple):
            nodes.append(_node(n, step[0], step[1], data, critic, states))
        else:
            nodes.append(_node(n, step, None, data, critic, states))
    foot = {
        "agents": "Щит у шага означает: его работу проверяет критик.",
        "visual": "Критик по картинкам читает все три сцены сразу, один раз, и только советует.",
        "none": "Критика здесь нет: каждый ответ проверяете и отправляете вы.",
    }[mode]
    opened = " open" if _lane_marked(lane, states) else ""
    return (f'<details class="sg-surface sg-lane" id="lane-{lane["key"]}"{opened}><summary class="sg-lane-head">'
            f'<span class="sg-lane-ic" aria-hidden="true">{lane["icon"]}</span><div><h2>{_e(lane["title"])}</h2>'
            f'<p class="sg-lane-who">{_e(lane["who"])}</p></div><span class="sg-lane-count">{_lane_count(lane)}</span></summary>'
            f'<div class="sg-lane-body"><p class="sg-lane-blurb">{_e(lane["blurb"])}</p>'
            f'<ol class="sg-flow">{"".join(nodes)}</ol><p class="sg-lane-foot">{_e(foot)}</p></div></details>')


def critic_html(enabled: bool) -> str:
    steps = "".join(
        f'<li><span class="sg-node-n">{i}</span><div><b>{_e(t)}</b><span>{_e(d)}</span></div></li>'
        for i, (t, d) in enumerate(CRITIC_STEPS, 1))
    state = (('<span class="sg-crit-state on">Сейчас включён</span>',
              "Под каждым шагом задания видно, что нашёл критик и что решил автор.")
             if enabled else
             ('<span class="sg-crit-state off">Сейчас выключен</span>',
              "Он добавляет до двух обращений к модели на каждый шаг, то есть время и расходы, поэтому включается настройкой сервера. "
              "Пока он выключен, шаги работают без второго мнения, а вы проверяете результат сами."))
    return ('<details class="sg-surface sg-critic-map" id="critic"><summary class="sg-lane-head">'
            '<span class="sg-lane-ic" aria-hidden="true">'
            f'{SHIELD.replace("16", "22")}</span><div><h2>Как работает критик</h2>'
            '<p class="sg-lane-who">Второй ИИ рядом с каждым агентом</p></div>'
            f'{state[0]}</summary><div class="sg-lane-body"><ol class="sg-crit-flow">{steps}</ol>'
            f'<p class="sg-proc-note">{_e(state[1])}</p></div></details>')


def team_map_html(product_id: UUID, critic_enabled: bool, states: dict | None = None,
                  task_label: str = "") -> str:
    states = states or {}
    data = catalog()
    placed = all_placed_agents()
    reserve = [a for a in data if a not in placed]
    total = len(data)
    base = f"/studio/products/{product_id}"
    tiles = (
        f'<div class="sg-tile"><b>{total}</b><span>{_pl(total, "агент", "агента", "агентов")} в команде</span>'
        f'<small>из них {len(placed)} работают в конвейерах Studio</small></div>'
        f'<div class="sg-tile"><b>{len(LANES)}</b><span>{_pl(len(LANES), "конвейер", "конвейера", "конвейеров")}</span>'
        '<small>для разных задач: статья, пост, видео, план, исследование</small></div>'
        f'<div class="sg-tile{"" if critic_enabled else " crit-off"}"><b>{"Включён" if critic_enabled else "Выключен"}</b><span>Критик</span>'
        '<small>второй ИИ проверяет работу каждого агента</small></div>'
    )
    banner = (f'<div class="sg-map-banner" role="status"><b>Ваше задание на схеме</b>'
              f'<span>{_e(task_label)}. Агенты этого задания подсвечены: зелёные закончили, синие работают.</span></div>'
              if states else "")
    intro = ('<section class="sg-surface sg-map-intro"><div class="sg-section-head"><div><h2>Команда ИИ-помощников</h2>'
             '<p>Над материалом работает не один ИИ, а команда: у каждого своя роль, и результат переходит от одного к другому. '
             'Нажмите на конвейер, чтобы увидеть всех его агентов.</p>'
             f'</div></div><div class="sg-proc-tiles">{tiles}</div>{org_html(critic_enabled, states)}</section>')
    lanes = "".join(lane_html(lane, data, states) for lane in LANES)
    reserve_html = ""
    if reserve:
        names = "".join(f'<li><b>{_e(data[a]["name"])}</b><span>{_e(data[a]["text"])}</span></li>' for a in reserve)
        reserve_html = ('<details class="sg-surface sg-lane" id="lane-reserve"><summary class="sg-lane-head"><span class="sg-lane-ic" aria-hidden="true">◌</span>'
                        '<div><h2>В штате, но пока без своего конвейера</h2><p class="sg-lane-who">Зарегистрированы в системе, Studio их не запускает.</p></div>'
                        f'<span class="sg-lane-count">{len(reserve)}</span></summary><div class="sg-lane-body"><ul class="sg-reserve">{names}</ul></div></details>')
    decide = ('<section class="sg-surface sg-decide"><div class="sg-section-head"><div><h2>Где решаете вы</h2>'
              '<p>Ни один агент ничего не публикует сам.</p></div></div><ul>'
              '<li>Готовый материал получает статус «ждёт решения», пока вы не нажмёте «Утвердить».</li>'
              '<li>Платные вызовы (рисованное видео) начинаются только после вашего согласия.</li>'
              '<li>Тексты можно поправить руками или попросить команду переделать.</li></ul>'
              f'<a class="btn primary" href="{base}/create">Создать материал</a></section>')
    return f'{banner}{intro}{critic_html(critic_enabled)}{lanes}{reserve_html}{decide}'


JS = r"""
/* Схема команды: переход по чипу открывает нужный конвейер */
(function(){
  function openLane(hash){
    if(!hash||hash.indexOf('#lane-')!==0)return;
    var el=document.getElementById(hash.slice(1));
    if(el&&el.tagName==='DETAILS'){el.open=true;setTimeout(function(){el.scrollIntoView({behavior:'smooth',block:'start'})},30)}
  }
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a.sg-org-chip');
    if(a){e.preventDefault();var h=a.getAttribute('href');try{history.replaceState(null,'',h)}catch(_){}openLane(h)}
  });
  window.addEventListener('hashchange',function(){openLane(location.hash)});
  if(location.hash)openLane(location.hash);
})();
"""

CSS = r"""
/* ── Схема команды ── */
.sg-map-intro,.sg-decide{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
@media(max-width:920px){.sg-map-intro .sg-tile:nth-child(3){grid-column:1 / -1}}
.sg-map-intro .sg-tile b{font-size:26px}
.sg-org{display:grid;gap:0;margin-top:6px}
.sg-org-entry{display:grid;gap:10px;padding:14px;border-radius:18px;background:#F7F9FD}
.sg-org-entry>header{display:grid;gap:2px}
.sg-org-entry>header b{font-size:16px;line-height:1.3}
.sg-org-entry>header span{font-size:13.5px;line-height:1.45;color:#56677C}
.sg-org-chips{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
.sg-org-chip{display:grid;grid-template-columns:34px minmax(0,1fr) auto;grid-template-areas:"ic t sh" "ic c sh";gap:0 12px;align-items:center;padding:10px 12px;border-radius:14px;background:#fff;border:1px solid #DFE6F0;text-decoration:none;color:inherit;min-height:56px}
.sg-org-chip .sg-org-ic{grid-area:ic;width:34px;height:34px;border-radius:11px;background:#EAF0FF;color:#2349C4;display:grid;place-items:center;font-size:16px}
.sg-org-chip b{grid-area:t;font-size:15px;line-height:1.3}
.sg-org-chip small{grid-area:c;font-size:13px;color:#56677C}
.sg-org-chip .sg-shield{grid-area:sh}
.sg-org-chip.hl{border-color:#2F5BEB;box-shadow:0 0 0 2px rgba(47,91,235,.18)}
.sg-org-gap{height:12px}
.sg-org-arrow{display:grid;justify-items:center;gap:4px;padding:10px 0 8px;font-size:13px;line-height:1.35;color:#56677C;text-align:center}
.sg-org-arrow i{width:2px;height:16px;background:#9DB4F0;position:relative;display:block}
.sg-org-arrow i::after{content:"";position:absolute;left:-5px;bottom:-4px;border:6px solid transparent;border-top-color:#9DB4F0;border-bottom:0}
.sg-org-end{display:grid;gap:2px;padding:14px;border-radius:18px;background:#EAF7EF;color:#14784A}
.sg-org-end b{font-size:16px}
.sg-org-end span{font-size:13.5px;color:#2F6B4B}
.sg-crit-flow{list-style:none;margin:0;padding:0;display:grid;gap:0}
.sg-crit-flow li{display:grid;grid-template-columns:36px minmax(0,1fr);gap:12px;position:relative;padding-bottom:14px}
.sg-crit-flow li:not(:last-child)::before{content:"";position:absolute;left:17px;top:38px;bottom:2px;width:2px;background:#C9D7FA}
.sg-crit-flow b{display:block;font-size:16px;line-height:1.3}
.sg-crit-flow span:not(.sg-node-n){display:block;font-size:14px;line-height:1.45;color:#56677C}
.sg-node-n{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:14px;background:#E8EEFF;color:#2349C4;flex:none}
.sg-crit-state{font-size:13px;font-weight:700;padding:5px 12px;border-radius:999px;white-space:nowrap;align-self:start}
.sg-crit-state.off{background:#FFF0D2;color:#6B4300}
.sg-crit-state.on{background:#E0F3E8;color:#14784A}
details.sg-lane,details.sg-critic-map{display:block}
.sg-lane-head{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:12px;align-items:center;cursor:pointer;list-style:none;min-height:48px}
.sg-lane-head::-webkit-details-marker{display:none}
.sg-lane-head::after{content:"+";font-size:22px;color:#56677C;width:20px;text-align:center}
details[open]>.sg-lane-head::after{content:"–"}
.sg-lane-head h2{margin:0;font-size:18px;line-height:1.25}
.sg-lane-ic{width:44px;height:44px;border-radius:14px;background:linear-gradient(145deg,#EAF0FF,#DCE6FF);color:#2349C4;display:grid;place-items:center;font-size:20px}
.sg-lane-who{margin:2px 0 0;font-size:13.5px;color:#56677C;line-height:1.4}
.sg-lane-count{font-size:13px;font-weight:700;padding:5px 11px;border-radius:999px;background:#F2F6FF;color:#2349C4;white-space:nowrap}
.sg-lane-head{grid-template-columns:44px minmax(0,1fr) 20px;grid-template-areas:"ic t mk" "ic c mk"}
.sg-lane-head>.sg-lane-ic{grid-area:ic}
.sg-lane-head>div{grid-area:t}
.sg-lane-head>.sg-lane-count,.sg-lane-head>.sg-crit-state{grid-area:c;justify-self:start;margin-top:6px}
.sg-lane-head::after{grid-area:mk}
.sg-lane-body{display:grid;gap:14px;margin-top:16px}
.sg-lane-blurb{margin:0;font-size:15px;line-height:1.5;color:#334961}
.sg-lane-foot{margin:0;font-size:13px;line-height:1.45;color:#56677C}
.sg-flow{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.sg-node{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:12px;align-items:start;padding:12px;border-radius:16px;background:#F7F9FD;position:relative}
.sg-node:not(:last-child)::after{content:"";position:absolute;left:29px;bottom:-11px;width:2px;height:12px;background:#C9D7FA}
.sg-node-b{min-width:0;display:grid;gap:2px}
.sg-node-b>b{font-size:15.5px;line-height:1.3}
.sg-node-b>span{font-size:14px;line-height:1.45;color:#56677C}
.sg-shield{color:#2F5BEB;display:inline-grid;place-items:center;align-self:start}
.sg-node.done{background:#EAF7EF}.sg-node.done .sg-node-n{background:#14784A;color:#fff}
.sg-node.run{background:#EAF0FF;box-shadow:0 0 0 2px #2F5BEB inset}.sg-node.run .sg-node-n{background:#2F5BEB;color:#fff}
.sg-node.err{background:#FEF3F2}.sg-node.err .sg-node-n{background:#B3261E;color:#fff}
.sg-minis{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:8px}
.sg-mini{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 8px;padding:10px 12px;border-radius:12px;background:#fff;border:1px solid #E3EAF5}
.sg-mini b{font-size:14.5px}
.sg-mini span:not(.sg-shield){grid-column:1;font-size:13.5px;line-height:1.4;color:#56677C}
.sg-mini .sg-shield{grid-row:1;grid-column:2}
.sg-mini.done{background:#EAF7EF;border-color:#CFE9DA}.sg-mini.run{background:#EAF0FF;border-color:#2F5BEB}
.sg-map-banner{display:grid;gap:2px;padding:12px 14px;border-radius:14px;background:#EAF0FF;color:#1D3FA6;font-size:14px;line-height:1.45}
.sg-map-banner b{font-size:15px}
.sg-reserve{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.sg-reserve li{padding:10px 12px;border-radius:12px;background:#F7F9FD;display:grid;gap:2px}
.sg-reserve span{font-size:13.5px;color:#56677C}
.sg-decide ul{margin:0;padding:0 0 0 20px;display:grid;gap:6px;font-size:15px;line-height:1.5}
@media(min-width:921px){
  .sg-lane-head{grid-template-columns:44px minmax(0,1fr) auto 20px;grid-template-areas:"ic t c mk"}
  .sg-lane-head>.sg-lane-count,.sg-lane-head>.sg-crit-state{margin-top:0;justify-self:end}
  .sg-org-chips{grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
  .sg-org-entry{grid-template-columns:240px minmax(0,1fr);align-items:start}
  .sg-flow{grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
  .sg-node:not(:last-child)::after{display:none}
  .sg-node-group{grid-column:1 / -1}
  .sg-minis{grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}
  .sg-crit-flow{grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
  .sg-crit-flow li{grid-template-columns:1fr;padding:0}
  .sg-crit-flow li:not(:last-child)::before{left:44px;right:-12px;top:17px;bottom:auto;width:auto;height:2px}
}
"""
