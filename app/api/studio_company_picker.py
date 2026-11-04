"""Responsive, theme-aware company chooser shared by classic and Growth v4."""
from __future__ import annotations

import html


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


CSS = r"""
.sg-company-picker{--picker-text:#1b2b3d;--picker-muted:#5b6d80;--picker-card:#fff;--picker-border:#dce5f0;--picker-accent:#315edf;
  width:min(100%,730px);margin:0 auto;padding:clamp(18px,4vw,42px) 16px 40px;color:var(--picker-text)}
.sg-company-picker *{box-sizing:border-box}
.sg-company-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px;margin-bottom:24px}
.sg-company-head .sg-official-brand{padding:0;margin:0}
.sg-company-back{color:var(--picker-accent);font-size:13px;font-weight:600;text-decoration:none;min-height:42px;display:inline-flex;align-items:center}
.sg-company-title{font-size:clamp(25px,5vw,33px);line-height:1.16;letter-spacing:-.035em;margin:0 0 7px;color:var(--picker-text)}
.sg-company-caption{color:var(--picker-muted);font-size:14px;line-height:1.5;margin:0 0 22px}
.sg-company-items{display:grid;gap:10px}
.sg-company-choice{display:flex;align-items:center;min-width:0;gap:14px;padding:15px 17px;text-decoration:none;
  color:var(--picker-text);background:var(--picker-card);border:1px solid var(--picker-border);
  border-radius:16px;min-height:78px;transition:border-color .18s ease,transform .18s ease}
.sg-company-choice:hover{border-color:var(--picker-accent);transform:translateY(-1px)}
.sg-company-choice:focus-visible,.sg-company-back:focus-visible,.sg-company-foot a:focus-visible{
  outline:3px solid var(--picker-accent);outline-offset:3px}
.sg-company-avatar{display:grid;place-items:center;width:42px;height:42px;flex:0 0 42px;border-radius:13px;
  background:color-mix(in srgb,var(--picker-accent) 12%,var(--picker-card));color:var(--picker-accent);
  font-size:17px;font-weight:750}
.sg-company-choice-copy{display:grid;gap:3px;min-width:0;flex:1}
.sg-company-choice-copy strong{display:block;color:var(--picker-text);font-size:15px;font-weight:650;line-height:1.35;overflow-wrap:anywhere}
.sg-company-choice-copy small{display:block;color:var(--picker-muted);font-size:12px;line-height:1.35}
.sg-company-arrow{color:var(--picker-accent);font-size:24px;font-weight:400;flex:none}
.sg-company-empty{padding:20px;color:var(--picker-muted);background:var(--picker-card);
  border:1px solid var(--picker-border);border-radius:15px}
.sg-company-foot{display:flex;flex-wrap:wrap;gap:12px 22px;margin:22px 2px 0;align-items:center}
.sg-company-foot a{font-size:13px;color:var(--picker-muted);text-decoration:none}
.sg-company-foot a:hover{color:var(--picker-accent)}
body[data-growth-design="v4"] .sg-company-picker{
  --picker-text:var(--s-text);--picker-muted:var(--s-muted);--picker-card:var(--s-surface);
  --picker-border:var(--s-line-2);--picker-accent:var(--s-accent);
  padding-top:clamp(15px,3vw,30px);padding-bottom:calc(95px + env(safe-area-inset-bottom,0px))}
body[data-growth-design="v4"] .sg-company-head .sg-official-brand{display:none}
body[data-growth-design="v4"] .sg-company-back{color:var(--s-accent)}
body[data-growth-design="v4"] .sg-company-title{color:var(--s-text)}
body[data-growth-design="v4"] .sg-company-caption{color:var(--s-muted)}
body[data-growth-design="v4"] .sg-company-choice{background:var(--s-surface);border-color:var(--s-line-2);color:var(--s-text)}
body[data-growth-design="v4"] .sg-company-choice:hover{border-color:var(--s-accent)}
body[data-growth-design="v4"] .sg-company-choice-copy strong{color:var(--s-text);-webkit-text-fill-color:var(--s-text)}
body[data-growth-design="v4"] .sg-company-choice-copy small{color:var(--s-muted);-webkit-text-fill-color:var(--s-muted)}
body[data-growth-design="v4"] .sg-company-avatar{background:var(--s-accent-soft);color:var(--s-accent)}
body[data-growth-design="v4"] .sg-company-arrow{color:var(--s-accent)}
body[data-growth-design="v4"] .sg-company-foot a{color:var(--s-muted)}
.sg-company-action{width:100%;font:inherit;cursor:pointer;text-align:left}
.sg-company-action:disabled{cursor:wait;opacity:.65}
.sg-growth-connected[hidden]{display:none}
html[data-theme="dark"] .sg-company-picker{
  --picker-text:#f0f5fc;--picker-muted:#b6c4d5;--picker-card:#1e2b40;
  --picker-border:#2a3b55;--picker-accent:#7aabff}
@media(prefers-color-scheme:dark){
  html:not([data-theme="light"]) .sg-company-picker{
    --picker-text:#f0f5fc;--picker-muted:#b6c4d5;--picker-card:#1e2b40;
    --picker-border:#2a3b55;--picker-accent:#7aabff}
}
@media(max-width:600px){
  .sg-company-picker{padding:18px 16px 96px}
  .sg-company-head{margin-bottom:18px}
  .sg-company-choice{border-radius:14px;min-height:70px;padding:12px 13px;gap:11px}
  .sg-company-avatar{width:40px;height:40px;flex-basis:40px}
  .sg-company-caption{margin-bottom:17px}
}
@media(prefers-reduced-motion:reduce){.sg-company-choice{transition:none}.sg-company-choice:hover{transform:none}}
"""


def selector_html(products, *, can_add: bool = False, can_link: bool = False,
                  unified_user_id: int | None = None,
                  linked_business_ids: tuple[int, ...] = ()) -> str:
    entries = "".join(
        '<a class="sg-company-choice" href="/studio/products/' + _esc(product.id) + '">'
        '<span class="sg-company-avatar" aria-hidden="true">' + _esc((product.name or "?").strip()[:1].upper()) + '</span>'
        '<span class="sg-company-choice-copy"><strong>' + _esc(product.name) + '</strong>'
        '<small>Открыть студию компании</small></span>'
        '<span class="sg-company-arrow" aria-hidden="true">›</span></a>'
        for product in products
    )
    if can_add:
        entries += ('<a class="sg-company-choice" href="/studio/new">'
                    '<span class="sg-company-avatar" aria-hidden="true">＋</span>'
                    '<span class="sg-company-choice-copy"><strong>Добавить компанию</strong>'
                    '<small>Создать новое рабочее пространство</small></span>'
                    '<span class="sg-company-arrow" aria-hidden="true">›</span></a>')
    if not entries:
        entries = '<div class="sg-company-empty">Пока нет организаций, к которым у вас есть доступ.</div>'
    unified = unified_user_id is not None
    connect = ('<a href="/workspace/studio-entry">Все организации ServGo</a>' if unified
               else '<a href="/studio/identity">Объединить профиль с ServGo</a>' if can_link else '')
    # The initial Growth list remains a no-JS fallback. On canonical SSO
    # the browser fetches the CURRENT ServGo business list with its JWT;
    # merely having an old Growth link does not grant any new access.
    if unified:
        safe_ids = ','.join(str(int(value)) for value in linked_business_ids)
        chooser = ('<section id="sg-servgo-organisations" data-user-id="' + _esc(unified_user_id)
                   + '" data-linked-businesses="' + _esc(safe_ids) + '">'
                   '<p role="status" class="sg-company-caption">Загружаем все организации ServGo…</p>'
                   '</section>'
                   '<section id="sg-growth-connected"><p class="sg-company-caption">'
                   'Уже подключённые рабочие пространства Growth:</p>'
                   '<div class="sg-company-items">' + entries + '</div></section>')
    else:
        chooser = '<div class="sg-company-items">' + entries + '</div>'
    script = '<script defer src="/studio/assets/servgo-organisations.js"></script>' if unified else ''
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Компании · ServGo</title><link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">'
        '<link rel="stylesheet" href="/studio/assets/studio.css?v=14">'
        '<style>' + CSS + '</style>' + script + '</head><body>'
        '<main class="sg-company-picker">'
        '<div class="sg-company-head">'
        '<a class="sg-official-brand" href="/studio" aria-label="ServGo">'
        '<img class="sg-official-brand-icon" src="/studio/assets/servgo.svg" width="42" height="42" alt="">'
        '<span class="sg-official-brand-name">Serv<span>Go</span></span></a>'
        '<a class="sg-company-back" href="https://servgo.ru/admin">← В админку ServGo</a></div>'
        '<h1 class="sg-company-title">Ваши компании</h1>'
        '<p class="sg-company-caption">Выберите организацию для работы в Growth Studio.</p>'
        + chooser +
        '<div class="sg-company-foot">' + connect +
        '<a href="/studio/logout">Выйти из студии</a></div>'
        '</main></body></html>'
    )
