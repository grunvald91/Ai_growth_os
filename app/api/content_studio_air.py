"""ServGo Air: one visual language for Growth Studio plus one-hand layout on phones.

Look: borderless white surfaces on a pale canvas, circular soft-gradient icons,
blue gradient primary buttons, light type, and a thin tapering animated line
that marks the current selection (taken from the booking-flow design).

Layout rule on phones: information sits at the top; everything pressed with a
thumb (navigation, the main action, filters, form submit) sits at the bottom.
Applied as the last CSS layer. No behaviour changes on the server.
"""

CSS = r"""
/* ═════ ServGo Air · токены ═════ */
:root{
  --air-ink:#182b41;--air-muted:#7e8d9e;--air-soft:#96a4b3;--air-blue:#346fda;--air-blue-deep:#2558bc;
  --air-canvas:#f0f4f9;--air-line:#ebf0f6;--air-tint:#f0f5fd;--air-nav-h:62px;
  --air-shadow:0 1px 2px rgba(24,43,65,.04),0 10px 30px rgba(24,43,65,.05);
  --air-grad:linear-gradient(112deg,#2960bd,#3c7fdf 70%,#4e93ea);
  --sg-font-ui:'Onest','Inter','Noto Sans',system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
  --air-safe-b:env(safe-area-inset-bottom,0px);
}
body,.studio-page,.main,.route-main{background:var(--air-canvas);color:var(--air-ink);font-family:var(--sg-font-ui);letter-spacing:-.012em}
h1,h2,h3,h4{font-family:var(--sg-font-ui);color:var(--air-ink);letter-spacing:-.04em}
h1{font-weight:620}h2{font-weight:600}h3,h4{font-weight:600}
.subtitle,.panel-sub,.sg-section-head p{color:var(--air-muted)}
.sg-overline,.eyebrow{color:#7396c2}

/* кнопки */
.btn{border-radius:13px;font-weight:600;letter-spacing:-.012em;border-color:transparent}
.btn.primary,.sg-primary-action{background:var(--air-grad);color:#fff;border:0;box-shadow:0 5px 12px #246ee324}
.btn.primary:active{background:var(--air-blue-deep)}
.btn.secondary,.btn.ghost{background:#fff;color:#245aaf;box-shadow:inset 0 0 0 1px #e3eaf3}

/* поверхности без рамок */
.sg-surface,.panel,.cycle-card,.sg-work-detail,.sg-priority-item,.sg-workflow-banner,.sg-channel-line{
  border:0!important;border-radius:20px;background:#fff;box-shadow:var(--air-shadow)}
.sg-workflow-banner{background:linear-gradient(120deg,#f3f8ff,#fff 70%)}
.sg-work-item,.sg-mini-row,.sg-where-grid a,.sg-work-result,.sg-review-item,.where a{
  border:0!important;border-radius:16px;background:#fff;box-shadow:0 1px 2px rgba(24,43,65,.04)}
.sg-where-ic{background:radial-gradient(circle at 35% 35%,#f8fbff,#e9f1fa 72%,#edf3f9);color:#365473}
.sg-choice-copy{border:0!important;border-radius:16px;background:#fff;box-shadow:inset 0 0 0 1.5px #e8edf3;padding:15px 14px!important}
.sg-choice input:checked + .sg-choice-copy{border:0!important;background:linear-gradient(90deg,transparent,#3878db 30%,#75d5f1 60%,transparent) 50% calc(100% - 3px)/52% 2px no-repeat,radial-gradient(ellipse at 30% 55%,rgba(213,232,254,.55),#fff 78%);box-shadow:inset 0 0 0 1.5px #bcd3f6}
.sg-choice input:checked + .sg-choice-copy b{color:#245aaf}
input,textarea,select{border-radius:13px}

/* тонкая «живая» линия выбранного (как в записи на услугу) */
.mobile-nav>a.active::after,.m-more-sum.active::after,
.tab.active::after,.sg-work-tabs a.active::after,.sg-filter.is-active::after{
  content:"";position:absolute;left:24%;width:52%;bottom:1px;height:2px;pointer-events:none;
  background-image:linear-gradient(95deg,#67a5ed 0%,#75d5f1 18%,#3878db 40%,#2d60c4 54%,#8ccbed 76%,#67a5ed 100%);
  background-size:280% 100%;
  clip-path:polygon(0 50%,8% 46%,22% 33%,40% 13%,60% 13%,78% 33%,92% 46%,100% 50%,92% 54%,78% 67%,60% 87%,40% 87%,22% 67%,8% 54%);
  animation:airFlow 3.8s ease-in-out infinite alternate}
.tab.active,.sg-work-tabs a.active,.sg-filter.is-active{position:relative}
@keyframes airFlow{from{background-position:0 50%}to{background-position:100% 50%}}
@media(prefers-reduced-motion:reduce){.mobile-nav>a.active::after,.m-more-sum.active::after,.tab.active::after,.sg-work-tabs a.active::after,.sg-filter.is-active::after{animation:none;background-position:50% 50%}}

/* боковая панель на компьютере */
.sidebar{background:#fff;box-shadow:1px 0 0 var(--air-line)}
.nav a{border-radius:13px;font-weight:550}
.nav a.active{background:var(--air-tint);color:var(--air-blue-deep)}
.sidebar .nav-ico{width:34px;height:34px;border-radius:50%;display:inline-grid;place-items:center;background:radial-gradient(circle at 35% 35%,#f8fbff,#e9f1fa 72%,#edf3f9)}

/* ═════ Телефон: информация сверху, всё нажимаемое снизу ═════ */
.mobile-nav,.m-more{display:none}
@media(max-width:920px){
  .studio-page,.studio-layout{background:var(--air-canvas)}
  /* верх: только информация */
  .sg-mobile-shell-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:50px;padding:0 18px;background:#fff;position:sticky;top:0;z-index:30}
  .sg-mobile-project-info{max-width:48%;font-size:12.5px;color:var(--air-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .route-mobile-brand{display:none}
  .sg-global-status{margin:0}
  .back-link{display:none}
  /* отступ под нижние панели */
  .main,.route-main{padding:14px 16px calc(var(--air-nav-h) + var(--air-safe-b) + 112px)!important}

  /* нижняя панель вкладок */
  .mobile-nav{display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr));gap:2px;position:fixed!important;left:0!important;right:0!important;bottom:0!important;top:auto!important;
    width:100%;min-height:calc(var(--air-nav-h) + var(--air-safe-b));padding:6px 6px calc(6px + var(--air-safe-b));z-index:60;
    background:#fff;border:0;border-radius:0;box-shadow:0 -6px 24px rgba(24,43,65,.07);backdrop-filter:none}
  .mobile-nav>a,.m-more-sum{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:50px;min-width:0;padding:4px 2px;
    border-radius:14px;color:#8392a3;font-size:11.5px;font-weight:550;line-height:1.15;position:relative;list-style:none;cursor:pointer;white-space:nowrap;background:none}
  .m-more-sum::-webkit-details-marker{display:none}
  .mobile-nav>a.active,.m-more-sum.active{color:var(--air-blue-deep);background:none}
  .m-svg{width:23px;height:23px}
  .mobile-nav>a.active .m-svg{color:var(--air-blue)}
  .m-more{display:block;position:static;min-width:0}
  .m-more>summary{height:100%}
  .m-more-badge{position:absolute;top:5px;right:calc(50% - 20px);width:9px;height:9px;border-radius:50%;background:#e5484d;border:2px solid #fff}
  .m-more[open]::before{content:"";position:fixed;inset:0;background:rgba(24,43,65,.28);z-index:58}
  .m-more[open]>summary{position:relative;z-index:61}
  .m-sheet{position:fixed;left:0;right:0;bottom:calc(var(--air-nav-h) + var(--air-safe-b));z-index:59;max-height:calc(100dvh - var(--air-nav-h) - var(--air-safe-b) - 70px);
    overflow-y:auto;overscroll-behavior:contain;background:#fff;border-radius:22px 22px 0 0;padding:14px 12px 10px;box-shadow:0 -12px 40px rgba(24,43,65,.16)}
  .mobile-nav .m-sheet .m-row{display:flex!important;flex-direction:row!important;align-items:center!important;justify-content:flex-start!important;gap:13px;min-height:58px;padding:6px 8px;border-radius:14px;color:var(--air-ink);text-align:left!important;white-space:normal;font-size:15px;font-weight:400;background:none}
  .mobile-nav .m-sheet .m-row:active{background:#f8fbff}
  .m-row-ic{flex:0 0 42px;width:42px;height:42px;border-radius:50%;display:grid;place-items:center;font-size:18px;color:#365473;
    background:radial-gradient(circle at 35% 35%,#f8fbff,#e9f1fa 72%,#edf3f9)}
  .m-row-text{flex:1;min-width:0}
  .mobile-nav .m-row-text b{display:block;font-size:15px;font-weight:550;line-height:1.25;color:var(--air-ink)}
  .m-row-text small{display:block;font-size:12px;color:var(--air-soft);line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .m-row-go{color:#a4b0bd;font-size:20px}
  .m-more-slot .sg-global-status{display:block;margin:0 0 4px}
  .m-more-slot .sg-notifications>summary{display:flex;min-height:58px;border:0;background:none;box-shadow:none;padding:6px 8px;border-radius:14px}
  .m-more-slot .sg-notify-popover{position:static;width:auto;box-shadow:none;border:0;margin:4px 0 8px;background:#f8fbff;border-radius:14px}

  /* главное действие страницы: у большого пальца */
  .sg-workflow-banner{grid-template-columns:1fr;padding:20px 18px}
  .sg-workflow-banner .sg-hero-actions,.route-actions:not(:empty){position:fixed;left:0;right:0;bottom:calc(var(--air-nav-h) + var(--air-safe-b));z-index:50;
    display:flex;gap:8px;padding:10px 16px;margin:0;width:auto;background:#fff;box-shadow:0 -4px 18px rgba(24,43,65,.06)}
  .sg-workflow-banner .sg-hero-actions .btn,.route-actions .btn{flex:1 1 0;min-height:48px;justify-content:center}
  .route-actions form{flex:1 1 0;display:flex}.route-actions form .btn{width:100%}
  /* «Создать» уже есть во вкладках, не дублируем сверху */
  .sg-section-head>.btn.primary[href$="/create"]{display:none}
  .route-page-head{display:block;padding:4px 0 0}
  .route-page-head .subtitle{margin-top:4px}

  /* форма: кнопка отправки закреплена внизу */
  .sg-create-form,.sg-form{overflow:visible}
  .sg-form-bottom{position:sticky;bottom:calc(var(--air-nav-h) + var(--air-safe-b));z-index:40;background:#fff;margin:14px -18px -18px;padding:10px 18px 12px;
    border-radius:0 0 20px 20px;box-shadow:0 -6px 18px rgba(24,43,65,.06)}
  .sg-form-hint{font-size:12px;color:var(--air-soft);margin:0 0 8px}

  /* фильтры и поиск: тоже внизу */
  .sg-work-tabs,.sg-filters{position:fixed;left:0;right:0;bottom:calc(var(--air-nav-h) + var(--air-safe-b));z-index:50;display:flex;flex-wrap:nowrap;gap:8px;
    overflow-x:auto;margin:0;padding:10px 16px;background:#fff;border:0;box-shadow:0 -4px 18px rgba(24,43,65,.06);scrollbar-width:none}
  .sg-work-tabs a,.sg-filters a{flex:0 0 auto;white-space:nowrap;min-height:44px}
  .library-controls{position:fixed!important;left:0;right:0;bottom:calc(var(--air-nav-h) + var(--air-safe-b));z-index:50;margin:0!important;padding:10px 16px 8px;
    background:#fff;box-shadow:0 -4px 18px rgba(24,43,65,.06);display:grid;gap:8px}
  .library-controls .tabs{overflow-x:auto!important;flex-wrap:nowrap!important;scrollbar-width:none}
  .library-controls .tab{flex:0 0 auto}
  .library-controls:has(.tabs){padding-bottom:10px}
  .main:has(.library-controls),.main:has(.sg-work-tabs),.main:has(.sg-filters){padding-bottom:calc(var(--air-nav-h) + var(--air-safe-b) + 150px)!important}
}
@media(min-width:921px){.mobile-nav{display:none!important}}
"""

JS = r"""
(function(){
  var mq=window.matchMedia?window.matchMedia('(max-width:920px)'):null;
  var status=document.querySelector('.sg-global-status'),slot=document.querySelector('.m-more-slot');
  var home=status?status.parentNode:null, next=status?status.nextSibling:null;
  function place(){
    if(!status||!slot||!mq)return;
    if(mq.matches){slot.appendChild(status)}
    else if(home&&status.parentNode!==home){home.insertBefore(status,next&&next.parentNode===home?next:null)}
  }
  place();if(mq&&mq.addEventListener)mq.addEventListener('change',place);
  var badge=document.querySelector('.m-more-badge'),count=status&&status.querySelector('[data-sg-count]');
  function sync(){if(!badge||!count)return;badge.hidden=count.hidden||!(parseInt(count.textContent,10)>0)}
  if(count&&window.MutationObserver){new MutationObserver(sync).observe(count,{attributes:true,childList:true,characterData:true,subtree:true});sync()}
  document.addEventListener('click',function(e){
    var m=document.querySelector('details.m-more[open]');
    if(m&&!m.contains(e.target)){m.removeAttribute('open')}
    else if(m&&e.target===m){m.removeAttribute('open')}
  });
  document.addEventListener('keydown',function(e){if(e.key==='Escape'){var m=document.querySelector('details.m-more[open]');if(m)m.removeAttribute('open')}});
})();
"""
