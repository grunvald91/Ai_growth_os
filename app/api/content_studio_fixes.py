"""Layout fixes found on a real phone (360 px) — applied as the last cascade layer.

Each block names the defect it removes. Keep selectors narrow: this layer must
not restyle anything it does not need to.
"""

CSS = r"""
/* ── «Ещё инструменты»: меню раньше уходило за левый край экрана ───────────── */
.sg-route-utility{align-items:flex-start}
.sg-route-utility a,.sg-inline-more>summary{min-height:44px;display:inline-flex;align-items:center}
.sg-inline-more>div{left:0;right:auto;width:min(280px,calc(100vw - 32px))}
.sg-inline-more>summary{gap:8px}
.sg-inline-more>summary:after{content:"↓";font-size:14px}
.sg-inline-more[open]>summary:after{content:"↑"}
@media(max-width:920px){
  .sg-inline-more{flex:1 1 100%;align-self:stretch}
  .sg-inline-more>summary{display:flex;justify-content:space-between;width:100%}
  /* Раскрывается внутри страницы и сдвигает контент вниз, а не накрывает его */
  .sg-inline-more>div{position:static;width:100%;margin-top:8px;box-shadow:none;
    grid-template-columns:repeat(2,minmax(0,1fr))}
  .sg-inline-more>div>a{display:flex;align-items:center;min-height:44px;line-height:1.3}
}

/* ── Фильтры-«таблетки»: не растягиваются по высоте, не режутся, прокручиваются ─ */
.tabs{align-items:center}
.tab{flex:0 0 auto;white-space:nowrap;display:inline-flex;align-items:center;
  min-height:40px;padding:8px 15px;line-height:1.2}
/* Даты в календаре — длинная лента, её прокручиваем */
.calendar-date-tabs .tabs{overflow-x:auto;flex-wrap:nowrap;scroll-snap-type:x proximity;
  -webkit-overflow-scrolling:touch;scrollbar-width:thin;padding-bottom:6px}
/* Фильтры материалов — их всего пять, показываем все сразу, без скрытой прокрутки */
.library-controls .tabs,.material-toolbar .tabs{overflow:visible;flex-wrap:wrap;gap:8px}

/* ── Поиск и «Найти» — в одной строке, фильтры ниже ─────────────────────────── */
.library-controls{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center}
.library-controls .search{order:0;grid-column:1;min-width:0;flex:none}
.library-controls>.btn{order:0;grid-column:2;min-height:44px;padding:10px 18px;font-size:14px}
.library-controls .tabs{order:1;grid-column:1/-1}

/* ── Заголовок раздела: иконка рядом с названием, а не ниже него ───────────── */
.panel-title{align-items:flex-start}
.panel-title>div{min-width:0}
.panel-head>.chip{margin-top:2px}

/* ── Свёрнутые блоки: стрелка не переносится и не прыгает ──────────────────── */
.sg-legacy-details>summary,.sg-side-extras>summary{display:flex;align-items:center;
  justify-content:space-between;gap:12px;min-height:44px}
.sg-legacy-details>summary:after{flex:0 0 auto;margin-left:auto}
.sg-dashboard-extra{padding:6px 8px}
.sg-dashboard-extra>summary{padding:8px 10px}

/* ── Лишний воздух в заголовках карточек ───────────────────────────────────── */
.sg-section-head p{margin:6px 0 0}
.sg-section-head{gap:8px 14px;align-items:flex-start}

/* ── Читаемый фокус для клавиатуры и голосового управления ─────────────────── */
a:focus-visible,button:focus-visible,summary:focus-visible,.tab:focus-visible{
  outline:3px solid #8BA4FB;outline-offset:2px;border-radius:10px}


/* ── Размер нажимаемых элементов под палец (не меньше 44 px) и читаемые подписи ─ */
.sg-mini-link,.sg-section-head>a,.sg-notify-all{display:inline-flex;align-items:center;min-height:44px}
.btn.small,.btn.secondary,.btn.ghost{min-height:44px}
.sg-official-brand{min-height:44px;display:inline-flex;align-items:center}
details>summary{min-height:44px;padding-top:10px;padding-bottom:10px;box-sizing:border-box}
.mobile-nav a,.mobile-nav a *{font-size:13px}
.mobile-nav a{min-height:48px}
.eyebrow,.beta,.sg-overline{font-size:12px}

/* ── Почта ─────────────────────────────────────────────────────────────────── */
.sg-mobile-mail{display:inline-grid;place-items:center;width:44px;height:44px;border-radius:12px;
  border:1px solid #DFE7F1;background:#fff;color:#34527B;font-size:20px;line-height:1;flex:0 0 auto}
.sg-mobile-mail:hover{background:#F2F6FF}
.sg-mobile-actions{display:flex;align-items:center;gap:8px;margin-left:auto;min-width:0}
/* Название проекта сокращается «…», а стрелка ⌄ остаётся на месте */
.sg-mobile-project{max-width:none;min-width:0;min-height:44px;flex:0 1 auto}
.sg-mobile-project-name{min-width:0;max-width:112px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sg-mobile-project span[aria-hidden]{flex:0 0 auto}
.sg-side-mail{margin:14px 0 0}

/* ── Упрощение: одно меню «Другие разделы» только на телефоне; на компьютере есть боковая панель ── */
@media(min-width:921px){.sg-route-utility{display:none}}
@media(max-width:920px){.topbar .eyebrow{display:none}}
.sg-side-extras>summary{justify-content:flex-start;gap:8px}
.topbar .eyebrow,.route-page-head .eyebrow{display:none}
@media(max-width:920px){
.mobile-nav{gap:2px}
.mobile-nav a{flex-direction:column!important;gap:2px!important;padding:6px 2px;text-align:center;line-height:1.2}
.mobile-nav a,.mobile-nav a *{font-size:12px}
.mobile-nav .m-ico{font-size:18px}
}
.sg-create-single{max-width:760px}
.sg-create-after{margin:14px 4px 0;font-size:14px;line-height:1.5;color:#51657A}
.sg-create-after a{font-weight:650}
.sg-side-mail{margin-top:10px}
"""

JS = r"""
(function(){
  /* Закрываем «Ещё инструменты», если нажали в любом другом месте */
  document.addEventListener('click',function(e){
    document.querySelectorAll('details.sg-inline-more[open]').forEach(function(d){
      if(!d.contains(e.target)) d.open=false;
    });
  });
  /* Активный фильтр показываем в видимой части прокручиваемой ленты */
  document.querySelectorAll('.tabs .tab.active').forEach(function(t){
    try{ t.scrollIntoView({inline:'center',block:'nearest'}); }catch(_){}
  });
})();
"""
