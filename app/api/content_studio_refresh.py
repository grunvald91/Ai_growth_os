"""Visual redesign layer for the existing server-rendered Growth Studio.

Accessible, light, no external font requests or UI packages. Keeps prior
HTML view functionality while providing focused forms and responsive layouts.
"""
CSS = r"""
/* ServGo Growth Studio v5 — deliberate, calm, task-first interaction */
:root{
 --sg-blue:#2059aa;--sg-blue-600:#184e96;--sg-blue-700:#153d72;
 --sg-sky:#eaf1fa;--sg-cyan:#5aa3af;
 --sg-navy:#172a42;--sg-text:#20334c;--sg-muted:#617289;
 --sg-line:#e1e7ee;--sg-bg:#f7f9fc;--sg-card:#fff;
 --sg-green:#218270;--sg-green-bg:#eaf5f0;--sg-orange:#be7633;
 --sg-orange-bg:#fff5e8;--sg-red:#be5557;--sg-red-bg:#fff0f0;
 --sg-shadow:0 8px 26px rgba(20,45,74,.045);
 --sg-radius:18px;--sg-radius-md:14px;--sg-radius-sm:10px;
 font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
 color:var(--sg-text);
}
body{background:var(--sg-bg);font-size:14px;line-height:1.5}
.sidebar{background:#fff;padding:26px 14px 16px;border-right:1px solid var(--sg-line);box-shadow:none}
.brand{padding-bottom:26px}
.brand-mark{background:var(--sg-navy);color:#fff;box-shadow:none}
.brand-name{font-weight:820;letter-spacing:-.8px;font-size:23px}
.brand-sub{font-size:10px;font-weight:550;letter-spacing:0;color:var(--sg-muted)}
.nav{gap:3px}
.nav-label{margin:22px 10px 10px;font-size:10px;letter-spacing:.13em;color:#90a0b1}
.nav a{min-height:44px;border-radius:11px;padding:10px 13px;color:#64738a;font-size:13px;font-weight:660;gap:12px}
.nav a.active,.nav a:hover{background:#edf3f9;color:#214e7b;box-shadow:none}
.nav-ico{color:inherit;font-size:17px}
.sidebar-promo{background:#f0f5f9;border:1px solid #e0eaf1;box-shadow:none}
.sidebar-promo p{color:#657c8b}
.main{padding:32px clamp(18px,3vw,46px) 100px}
.topbar,.route-page-head{align-items:center;margin-bottom:24px;gap:20px}
.topbar h1,.route-page-head h1{font-weight:800;color:var(--sg-navy);letter-spacing:-.035em}
.topbar h1{font-size:clamp(26px,2.1vw,34px)}
.route-page-head h1{font-size:clamp(27px,2vw,35px)}
.subtitle{font-size:13px;line-height:1.6;max-width:650px;color:var(--sg-muted)}
.eyebrow{color:#55748e;font-weight:750;letter-spacing:.13em}
.beta{background:#eff5fa;color:#66809b;border:1px solid #dfe8ef;box-shadow:none}
.panel,.sg-surface{background:#fff;border:1px solid var(--sg-line);box-shadow:var(--sg-shadow);border-radius:var(--sg-radius)}
.panel{overflow:hidden}
.btn{font-size:13px;font-weight:700;padding:12px 17px;border-radius:11px;min-height:44px;text-align:center;line-height:1.25;white-space:normal}
.btn.primary{background:#1c5a92;color:#fff;box-shadow:none}
.btn.primary:hover{background:#124873}
.btn.secondary,.btn.ghost{border:1px solid #d8e3ec;background:#fff;color:#2c5274;box-shadow:none}
.btn.secondary:hover,.btn.ghost:hover{background:#f0f6fa}
.btn:focus-visible,.nav a:focus-visible,.mobile-nav a:focus-visible,
.sg-choice:focus-within,.sg-filter:focus-visible,input:focus-visible,
textarea:focus-visible,select:focus-visible{outline:3px solid #83b8d8;outline-offset:3px}
.btn:disabled{opacity:.46;cursor:not-allowed}
.kpi{border:1px solid var(--sg-line);border-radius:15px;box-shadow:none}
.kpi strong{color:var(--sg-navy)}
.kpi-icon{background:#f0f5f9!important;color:#376483!important}
.status{font-weight:690;border-radius:9px}
.chip{font-weight:650}
.material-card,.package-card,.calendar-row{transition:background .14s ease,border-color .14s ease}
.material-card:hover,.package-card:hover{transform:none;box-shadow:0 4px 18px rgba(20,45,74,.08)}
.material-grid{gap:13px}
.route-content{display:grid;gap:18px}
.route-content>.panel{margin-bottom:0}
.route-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.back-link{color:#55718e;font-size:12px;font-weight:700}
.sg-overline{color:#63819a;font-size:10px;letter-spacing:.14em;font-weight:800;display:block;margin-bottom:8px}
.sg-surface{padding:24px;min-width:0}
.sg-section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:22px}
.sg-section-head h2,.sg-surface h2{font-size:21px;color:var(--sg-navy);letter-spacing:-.03em;font-weight:790;line-height:1.2;margin:0 0 7px}
.sg-section-head p,.sg-surface p{margin:0;color:var(--sg-muted);line-height:1.55}
.sg-layout-two{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(290px,.85fr);gap:18px;align-items:start}
.sg-aside-stack{display:grid;gap:18px;min-width:0}
.sg-create-form{padding:27px}
.sg-format-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0 0 24px;padding:0;border:0}
.sg-choice{position:relative;display:block;cursor:pointer}
.sg-choice input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}
.sg-choice-copy{display:grid;gap:4px;padding:15px 14px;min-height:80px;border:1px solid #dfe6ed;border-radius:12px;background:#fff;transition:background .15s,border-color .15s}
.sg-choice-copy b{font-size:13px;font-weight:750;color:#243c59}
.sg-choice-copy small{font-size:11px;line-height:1.35;color:#708196}
.sg-choice input:checked + .sg-choice-copy{border:2px solid #2d688e;padding:14px 13px;background:#f2f8fb}
.sg-choice input:checked + .sg-choice-copy b{color:#194a72}
.sg-choice input:focus-visible + .sg-choice-copy{outline:3px solid #83b8d8;outline-offset:2px}
.sg-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px}
.sg-field{min-width:0;display:grid;gap:7px;align-content:start}
.sg-field>span{font-size:12px;font-weight:720;color:#324b68}
.sg-field>span em{font-size:11px;font-weight:500;font-style:normal;color:#8090a0}
.sg-field input,.sg-field textarea,.sg-field select{width:100%;min-height:46px;padding:12px 13px;border:1px solid #d9e3eb;background:#fff;border-radius:11px;color:#1d354e;font-size:14px;line-height:1.45;box-shadow:none;outline:none}
.sg-field textarea{resize:vertical;min-height:100px}
.sg-field small,.sg-footnote{font-size:11px;line-height:1.45;color:#8592a1}
.sg-wide{grid-column:1/-1}
.sg-form-bottom{border-top:1px solid var(--sg-line);padding-top:18px;margin-top:24px;display:flex;align-items:center;justify-content:space-between;gap:16px}
.sg-form-hint{max-width:330px;font-size:11px;line-height:1.5;color:#6d8090}
.sg-form-hint span{color:#0a806d;font-weight:850}
.sg-primary-action{min-width:175px}
.sg-guide{background:#fbfcfe}
.sg-guide-icon{width:44px;height:44px;display:grid;place-items:center;background:#e8f1f6;border-radius:13px;color:#31617e;font-size:25px;margin-bottom:17px}
.sg-guide h2{font-size:20px}
.sg-guide h3{font-size:16px;margin:0 0 10px;color:var(--sg-navy)}
.sg-guide p{margin:0 0 17px}
.sg-steps{padding:5px 0 0;list-style:none;counter-reset:sg-step;margin:0;display:grid;gap:19px}
.sg-steps li{counter-increment:sg-step;display:grid;grid-template-columns:30px 1fr;column-gap:9px;align-items:start}
.sg-steps li:before{content:counter(sg-step);width:27px;height:27px;display:grid;place-items:center;background:#eaf1f6;color:#2e6786;border-radius:9px;font-size:12px;font-weight:820;grid-row:span 2}
.sg-steps b{color:#263d56;font-weight:720}
.sg-steps span{font-size:12px;color:#76869b;line-height:1.4;margin-top:3px}
.sg-notice{padding:13px 15px;border:1px solid #cce9dc;background:#f0faf5;color:#2b6554;border-radius:11px;margin:0 0 17px!important}
.sg-notice a{text-decoration:underline;color:#165c4d;font-weight:800}
.sg-filter{min-height:39px;padding:8px 14px;display:inline-flex;align-items:center;border-radius:9px;color:#63798e;border:1px solid transparent;font-weight:700;font-size:12px}
.sg-filter.is-active{background:#eaf2f8;color:#235479;border-color:#d6e7ef}
.sg-filters{display:flex;gap:5px;border-bottom:1px solid #e6edf2;padding-bottom:15px;overflow:auto;margin-bottom:12px}
.sg-review-list{display:grid;gap:0}
.sg-review-item{padding:21px 1px;border-bottom:1px solid #e6edf2}
.sg-review-item:last-child{border-bottom:0}
.sg-review-top{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.sg-tag,.sg-channel,.sg-warning{padding:5px 9px;border-radius:7px;font-size:10px;font-weight:750;background:#ecf3f8;color:#346381}
.sg-channel{background:#f2f4f7;color:#647588}
.sg-warning{background:#fff3e7;color:#9a6228}
.sg-review-item h3{margin:0 0 7px;font-size:16px;line-height:1.35;color:#203951;letter-spacing:-.015em}
.sg-review-item p{max-width:760px;white-space:pre-line;word-break:break-word;font-size:13px;color:#64788a;line-height:1.55;margin:0 0 16px}
.sg-review-footer,.sg-inline-actions{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
.sg-review-footer>span{font-size:11px;color:#8b98a6}
.sg-inline-actions form{margin:0}
.sg-count{display:inline-flex;align-items:center;padding:7px 10px;font-size:11px;font-weight:750;background:#eef4f8;border-radius:9px;color:#42647e;white-space:nowrap}
.sg-empty{text-align:center;padding:35px 15px}
.sg-empty>div{width:50px;height:50px;border-radius:50%;display:grid;place-items:center;background:#eaf6ef;color:#327d66;font-size:24px;margin:0 auto 13px}
.sg-empty h3{margin:0 0 8px;font-size:17px}
.sg-empty p{margin:0 0 17px}
.sg-channel-list{display:grid;gap:12px}
.sg-channel-line{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid #edf1f6}
.sg-link-dot{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:#e4f3eb;color:#24836a;flex-shrink:0}
.sg-channel-line>div{display:grid;gap:2px;flex:1}
.sg-channel-line b{color:#274159}
.sg-channel-line small{color:#7e8d9e}
.sg-channel-status{font-size:10px;color:#378069;font-weight:800;white-space:nowrap}
.sg-publication-list{margin:0;padding:0;list-style:none}
.sg-publication-row{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid #e6edf2;padding:12px 0}
.sg-publication-row:last-child{border:0}
.sg-publication-row div{display:grid;gap:3px}
.sg-publication-row b{font-size:13px;color:#2f435a}
.sg-publication-row small{font-size:11px;color:#8996a5}
.sg-compact-form{grid-template-columns:1fr;margin-top:19px}
.sg-compact-form .btn{justify-self:start}
.sg-muted{color:#7d8b9b!important;font-size:13px}
.sg-hero-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.sg-workflow-banner{display:grid;grid-template-columns:minmax(0,1.2fr) auto;align-items:center;gap:16px;
 padding:26px 29px;background:#f0f5f9;border:1px solid #deeaf2;border-radius:19px;margin:0 0 22px}
.sg-workflow-banner h2{font-size:22px;color:#173a57;letter-spacing:-.03em;margin:0 0 7px}
.sg-workflow-banner p{font-size:12px;max-width:540px;color:#658198;margin:0;line-height:1.5}
.sg-priority-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:20px}
.sg-priority-item{padding:17px 19px;border:1px solid var(--sg-line);border-radius:15px;background:#fff;display:flex;flex-direction:column;gap:5px}
.sg-priority-item strong{font-size:26px;line-height:1.2;color:#244b6e;letter-spacing:-.03em}
.sg-priority-item span{font-size:11px;color:#697e92;font-weight:680}
.sg-priority-item em{font-size:11px;font-style:normal;color:#356485;font-weight:750}
.sg-priority-item:hover{border-color:#a8c8dc;background:#fafcfe}
.sg-quick-links{display:flex;gap:9px;flex-wrap:wrap;margin:0 0 22px}
.sg-quick-links a{display:inline-flex;align-items:center;min-height:42px;border:1px solid #dce7ee;border-radius:10px;padding:9px 12px;background:#fff;color:#3e6786;font-size:12px;font-weight:740}
.sg-quick-links a:hover{background:#edf5f9}
.sg-inline-form{display:grid;gap:12px;max-width:800px;margin-top:20px;padding-top:18px;border-top:1px solid var(--sg-line)}
.sg-inline-form .sg-field{max-width:100%}
.sg-inline-form button{justify-self:start}
.sg-inline-form textarea{min-height:82px}
.sg-detail-steps{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:19px;font-size:11px;color:#7b8d9c}
.sg-detail-steps span{padding:6px 9px;background:#f0f4f7;border-radius:8px}
.sg-detail-steps .done{color:#285c83;background:#e4f1f8}
.sg-detail-steps .current{color:#286b5a;background:#e8f5ed}
.sg-publish-mode{font-size:11px;color:#8b6a3c;margin-top:10px}
.sg-version-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:11px}
.sg-field-help{font-size:11px;color:#70889c}
.sg-inline-list{display:grid;gap:12px}
.sg-action-tile{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:15px;border:1px solid var(--sg-line);border-radius:12px}
.sg-action-tile h3{margin:0;font-size:14px;color:#2a4863}
.sg-action-tile p{margin:4px 0 0;color:#7c8c9d;font-size:11px}
.sg-dashboard-feed{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:16px;margin-bottom:20px}
.sg-dashboard-feed .sg-surface{padding:20px}
.sg-dashboard-feed .sg-section-head{align-items:center;margin-bottom:8px}
.sg-dashboard-feed .sg-section-head h2{font-size:17px}
.sg-dashboard-feed .sg-section-head p{font-size:11px}
.sg-mini-link{font-size:11px;color:#246288;font-weight:780;white-space:nowrap}
.sg-mini-list{display:grid}
.sg-mini-row{display:flex;align-items:center;gap:11px;border-bottom:1px solid #edf1f5;min-height:63px;padding:10px 2px}
.sg-mini-row:hover{background:#fafcff}
.sg-mini-row:last-child{border-bottom:0}
.sg-mini-row>div{display:grid;gap:4px;min-width:0;flex:1}
.sg-mini-row b{font-size:12px;color:#2f4560;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sg-mini-row small{color:#7a8c9f;font-size:10px}
.sg-mini-row>span:last-child{color:#7f98aa;font-size:15px}
.sg-mini-date{color:#2e627e;font-size:11px;font-weight:770;background:#eaf3f9;border-radius:9px;padding:8px 9px;min-width:79px;text-align:center}
.sg-mini-kind{width:32px;height:32px;display:grid;place-items:center;background:#eff5f9;color:#3f7792;border-radius:9px}
.sg-legacy-details{border:1px solid var(--sg-line);border-radius:14px;padding:13px 17px;background:#fff;margin:0 0 32px}
.sg-legacy-details>summary{cursor:pointer;font-size:12px;font-weight:760;color:#57738d;list-style:none;padding:5px 2px}
.sg-legacy-details>summary::-webkit-details-marker{display:none}
.sg-legacy-details>summary:after{content:"  ↓";font-size:14px}
.sg-legacy-details[open]>summary{margin-bottom:19px}
.sg-legacy-details[open]>summary:after{content:"  ↑"}
.sg-legacy-details .desktop-only-dashboard{display:block}
.sg-legacy-details .desktop-only-dashboard>.kpis{margin-top:6px}
.sg-calendar-entry{border-bottom:1px solid #e5edf2}
.sg-calendar-entry:last-child{border-bottom:0}
.sg-calendar-entry>.calendar-row{border:0}
.sg-calendar-move{padding:0 18px 13px}
.sg-calendar-move summary{list-style:none;display:inline-flex;align-items:center;cursor:pointer;color:#366487;font-size:12px;font-weight:720;border-radius:8px;padding:8px 10px}
.sg-calendar-move summary::-webkit-details-marker{display:none}
.sg-calendar-move summary:hover{background:#eaf3fa}
.sg-calendar-form{display:flex;flex-wrap:wrap;gap:11px;align-items:end;padding:12px 0}
.sg-calendar-form .sg-field{max-width:265px;flex:1;min-width:220px}
.sg-calendar-form .btn{align-self:end}
.sg-route-utility{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin:8px 0 19px}
.sg-route-utility a{font-size:11px;color:#426681;padding:8px 12px;border:1px solid #e1eaf1;background:white;border-radius:9px;font-weight:710}
.sg-route-utility a:hover{background:#f0f5f9}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1180px){.sg-layout-two{grid-template-columns:1fr}.sg-aside-stack{grid-template-columns:repeat(2,minmax(0,1fr))}.sg-dashboard-feed{grid-template-columns:1fr}}
@media(max-width:920px){
 .main{padding:19px 17px 110px}
 .mobile-only-dashboard{display:none!important}
 .sidebar{display:none}
 .mobile-nav{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#fff;border-radius:16px 16px 0 0}
 .mobile-nav a{font-size:10px;min-height:51px}
 .mobile-nav .m-ico{font-size:19px}
 .sg-aside-stack{grid-template-columns:1fr}
 .sg-workflow-banner{padding:20px}
 .sg-layout-two{grid-template-columns:1fr}
 .sg-hero-actions{justify-content:flex-start}
 .route-page-head{align-items:flex-start;padding:10px 0}
}
@media(max-width:620px){
 .main{padding:10px 13px 105px}
 .sg-surface{padding:18px 15px}
 .sg-create-form{padding:20px 15px}
 .sg-workflow-banner{grid-template-columns:1fr;padding:19px 17px;margin-bottom:16px;gap:14px}
 .sg-workflow-banner h2{font-size:20px}
 .sg-workflow-banner .btn{width:100%}
 .sg-priority-strip{grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}
 .sg-priority-item{padding:13px 9px;min-width:0}
 .sg-priority-item strong{font-size:21px}
 .sg-priority-item span{font-size:10px}
 .sg-priority-item em{font-size:10px}
 .sg-format-grid{grid-template-columns:1fr 1fr;gap:8px}
 .sg-choice-copy{min-height:83px;padding:12px 10px}
 .sg-choice-copy b{font-size:12px}
 .sg-choice-copy small{font-size:10px}
 .sg-choice input:checked + .sg-choice-copy{padding:11px 9px}
 .sg-fields{grid-template-columns:1fr}
 .sg-field{grid-column:1}
 .sg-form-bottom{flex-direction:column;align-items:stretch}
 .sg-form-bottom .btn{width:100%}
 .sg-review-footer{align-items:flex-start;gap:12px}
 .sg-inline-actions{width:100%}
 .sg-inline-actions .btn{min-height:44px}
 .sg-review-item{padding:18px 0}
 .sg-inline-actions form{flex:1}
 .sg-inline-actions form .btn{width:100%}
 .sg-quick-links{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none}
 .sg-quick-links a{white-space:nowrap;flex-shrink:0}
 .route-page-head{display:block}
 .route-actions{margin-top:13px}
 .route-actions .btn{width:auto;flex:1}
 .route-page-head h1{font-size:26px}
}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
"""
JS = r"""
(function(){
  const form=document.querySelector('[data-studio-create]');
  if(form){
    const options={
      post:[['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['vc_ru','vc.ru'],['tenchat','TenChat']],
      short_video:[['youtube_shorts','YouTube Shorts'],['instagram_reels','Instagram Reels'],['vk_clips','VK Клипы']],
      story:[['telegram','Telegram Stories'],['vk','Истории ВК'],['instagram_reels','Instagram Stories']],
      static_image:[['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['instagram_reels','Instagram']],
      package:[]
    };
    const select=form.querySelector('select[name=channel]');
    const wrapper=form.querySelector('[data-create-platform]');
    const rerender=()=>{
      const type=form.querySelector('input[name=format]:checked')?.value||'post';
      select.replaceChildren();
      for(const [key,label] of (options[type]||[])){
        const opt=document.createElement('option');opt.value=key;opt.textContent=label;
        select.appendChild(opt);
      }
      const full=type==='package';
      wrapper.hidden=full;
      select.required=!full;
      const button=form.querySelector('button[type=submit]');
      if(button)button.textContent=full?'Создать полный пакет →':'Создать материал →';
    };
    form.querySelectorAll('input[name=format]').forEach(x=>x.addEventListener('change',rerender));
    rerender();
  }
  const alertFields=document.querySelectorAll('[data-confirm]');
  alertFields.forEach(button=>button.addEventListener('click',ev=>{
    if(!window.confirm(button.dataset.confirm||'Подтвердить действие?'))ev.preventDefault();
  }));
})();
"""
