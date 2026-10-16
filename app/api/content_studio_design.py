STUDIO_CSS = r"""
:root{
  --sg-blue:#0b7cff;
  --sg-blue-600:#0666dc;
  --sg-blue-700:#0754b7;
  --sg-sky:#eaf5ff;
  --sg-cyan:#35c8ff;
  --sg-navy:#0d1b3f;
  --sg-text:#13203d;
  --sg-muted:#6f7f9f;
  --sg-line:#e5edf8;
  --sg-bg:#f4f8fd;
  --sg-card:#ffffff;
  --sg-green:#0bbf7a;
  --sg-green-bg:#e8fbf3;
  --sg-orange:#f59b23;
  --sg-orange-bg:#fff5e7;
  --sg-red:#e44c5a;
  --sg-red-bg:#fff0f2;
  --sg-purple:#8055f8;
  --sg-purple-bg:#f2edff;
  --sg-shadow:0 10px 30px rgba(31,75,128,.08);
  --sg-shadow-soft:0 4px 16px rgba(31,75,128,.06);
  --sg-radius:22px;
  --sg-radius-md:16px;
  --sg-radius-sm:12px;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  color:var(--sg-text);
  background:var(--sg-bg);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:
  radial-gradient(circle at 10% -10%,rgba(53,200,255,.10),transparent 26rem),
  radial-gradient(circle at 95% 0%,rgba(11,124,255,.07),transparent 32rem),
  var(--sg-bg);color:var(--sg-text)}
button,input{font:inherit}
button{cursor:pointer}
a{color:inherit;text-decoration:none}
svg{display:block}
.studio-page{min-height:100vh}
.studio-layout{display:grid;grid-template-columns:238px minmax(0,1fr);min-height:100vh}
.sidebar{position:sticky;top:0;height:100vh;padding:24px 16px 20px;background:rgba(255,255,255,.92);border-right:1px solid var(--sg-line);backdrop-filter:blur(14px);display:flex;flex-direction:column;z-index:20}
.brand{display:flex;align-items:center;gap:11px;padding:0 10px 22px}
.brand-mark{width:40px;height:40px;border-radius:14px;background:linear-gradient(145deg,var(--sg-cyan),var(--sg-blue));display:grid;place-items:center;box-shadow:0 8px 18px rgba(11,124,255,.24);color:#fff;font-weight:900;font-size:22px;transform:rotate(-5deg)}
.brand-name{font-size:24px;font-weight:850;letter-spacing:-.6px;color:var(--sg-navy)}
.brand-sub{font-size:10px;color:var(--sg-muted);margin-top:1px}
.nav-label{padding:18px 13px 7px;font-size:11px;text-transform:uppercase;letter-spacing:.11em;color:#9aa8c2;font-weight:800}
.nav{display:grid;gap:5px}
.nav a{display:flex;align-items:center;gap:11px;padding:11px 13px;border-radius:13px;color:#53627e;font-size:14px;font-weight:700;transition:.18s ease}
.nav a:hover{background:#f1f7ff;color:var(--sg-blue)}
.nav a.active{background:linear-gradient(90deg,#e9f5ff,#f5faff);color:var(--sg-blue);box-shadow:inset 3px 0 0 var(--sg-blue)}
.nav-ico{width:21px;height:21px;display:grid;place-items:center;color:currentColor}
.sidebar-spacer{flex:1}
.sidebar-promo{margin:18px 0 12px;padding:16px;border-radius:18px;background:linear-gradient(145deg,#eef7ff 0%,#dbeeff 55%,#eaf6ff 100%);border:1px solid #dbeeff;position:relative;overflow:hidden}
.sidebar-promo:after{content:"";position:absolute;width:110px;height:110px;border-radius:50%;right:-44px;bottom:-45px;background:linear-gradient(145deg,rgba(11,124,255,.20),rgba(53,200,255,.04))}
.sidebar-promo b{display:block;font-size:14px;margin:5px 0 4px}
.sidebar-promo p{font-size:12px;line-height:1.45;color:#607495;margin:0}
.sidebar-foot{padding:10px 12px;color:#9aa8c2;font-size:11px}
.main{min-width:0;padding:24px 26px 90px}
.topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:20px}
.eyebrow{display:flex;align-items:center;gap:8px;color:var(--sg-blue);font-weight:800;font-size:12px}
.beta{padding:4px 8px;border-radius:999px;background:#dff0ff;color:var(--sg-blue);font-size:10px;letter-spacing:.05em}
h1{font-size:31px;line-height:1.06;margin:5px 0 4px;letter-spacing:-1.1px;color:var(--sg-navy)}
.subtitle{margin:0;color:var(--sg-muted);font-size:14px}
.top-actions{display:flex;align-items:center;gap:10px}
.cycle-card,.product-pill{background:rgba(255,255,255,.9);border:1px solid var(--sg-line);box-shadow:var(--sg-shadow-soft);border-radius:16px;padding:10px 13px}
.cycle-card{min-width:220px}
.cycle-head{display:flex;justify-content:space-between;gap:12px;font-size:11px;color:var(--sg-muted)}
.cycle-head b{color:var(--sg-text)}
.progress{height:5px;background:#e8eff8;border-radius:999px;overflow:hidden;margin-top:8px}
.progress>span{display:block;height:100%;background:linear-gradient(90deg,var(--sg-blue),var(--sg-cyan));border-radius:inherit}
.product-pill{display:flex;gap:10px;align-items:center;min-width:190px}
.product-avatar{width:34px;height:34px;border-radius:11px;background:linear-gradient(145deg,#f2f8ff,#d9efff);display:grid;place-items:center;color:var(--sg-blue);font-weight:900}
.product-pill b{display:block;font-size:13px}
.product-pill span{display:block;font-size:11px;color:var(--sg-muted);margin-top:2px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.kpi{background:rgba(255,255,255,.93);border:1px solid var(--sg-line);border-radius:18px;padding:16px;display:flex;align-items:center;gap:13px;box-shadow:var(--sg-shadow-soft);min-height:91px}
.kpi-icon{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;font-weight:900;font-size:17px;flex:0 0 auto}
.kpi.blue .kpi-icon{background:var(--sg-sky);color:var(--sg-blue)}
.kpi.green .kpi-icon{background:var(--sg-green-bg);color:var(--sg-green)}
.kpi.purple .kpi-icon{background:var(--sg-purple-bg);color:var(--sg-purple)}
.kpi.orange .kpi-icon{background:var(--sg-orange-bg);color:var(--sg-orange)}
.kpi strong{font-size:23px;display:block;letter-spacing:-.5px;color:var(--sg-navy)}
.kpi small{color:var(--sg-muted);font-size:11px;line-height:1.35}
.workspace{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(315px,.55fr);gap:16px;align-items:start}
.stack{display:grid;gap:16px;min-width:0}
.panel{background:rgba(255,255,255,.95);border:1px solid var(--sg-line);border-radius:var(--sg-radius);box-shadow:var(--sg-shadow);overflow:hidden}
.panel-head{padding:17px 19px 10px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.panel-title{display:flex;align-items:center;gap:10px}
.panel-title h2{margin:0;font-size:17px;color:var(--sg-navy);letter-spacing:-.25px}
.panel-title .ico{width:31px;height:31px;border-radius:10px;background:#edf6ff;color:var(--sg-blue);display:grid;place-items:center}
.panel-sub{font-size:11px;color:var(--sg-muted);margin-top:2px}
.btn{border:0;border-radius:12px;padding:10px 14px;font-weight:800;font-size:12px;display:inline-flex;align-items:center;justify-content:center;gap:7px;transition:.18s ease}
.btn:hover{transform:translateY(-1px)}
.btn.primary{background:linear-gradient(135deg,var(--sg-blue),#1769f5);color:#fff;box-shadow:0 7px 16px rgba(11,124,255,.23)}
.btn.secondary{background:#f2f7fd;color:var(--sg-blue);border:1px solid #dbeafa}
.btn.ghost{background:#fff;color:#63718c;border:1px solid var(--sg-line)}
.btn.danger{background:#fff1f2;color:#c83342;border:1px solid #ffd9de}
.btn.small{padding:8px 10px;font-size:11px;border-radius:10px}
.calendar-scroll{overflow-x:auto;padding:5px 16px 18px;scrollbar-width:thin}
.calendar-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;min-width:710px}
.day-card{background:linear-gradient(180deg,#f9fbff,#f5f9fe);border:1px solid #eaf0f8;border-radius:15px;padding:11px;min-height:154px}
.day-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.day-head strong{font-size:12px;color:var(--sg-navy)}
.count{min-width:22px;height:22px;padding:0 6px;border-radius:999px;background:#e8f2ff;color:var(--sg-blue);display:grid;place-items:center;font-size:10px;font-weight:900}
.slot{background:#fff;border:1px solid #e6edf6;border-radius:11px;padding:9px 9px 9px 10px;margin-top:7px;box-shadow:0 2px 8px rgba(40,72,114,.04)}
.slot-top{display:flex;justify-content:space-between;gap:8px;align-items:center}
.slot-time{font-size:10px;color:#7183a2;font-weight:800}
.platform-mini{font-size:9px;font-weight:900;border-radius:7px;padding:4px 6px;background:#edf6ff;color:var(--sg-blue)}
.slot-title{font-size:11px;line-height:1.35;font-weight:800;color:#263655;margin-top:5px}
.slot-meta{font-size:9px;color:#8795ad;margin-top:4px}
.status{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border-radius:999px;font-size:9px;font-weight:850}
.status.ready_for_review,.status.completed{background:var(--sg-green-bg);color:#078e5c}
.status.preparing,.status.running,.status.queued{background:var(--sg-orange-bg);color:#ad6810}
.status.failed,.status.missed{background:var(--sg-red-bg);color:#c33e4b}
.status.planned,.status.draft{background:#eef3f9;color:#667690}
.status.active{background:#e8f5ff;color:var(--sg-blue)}
.status.paused{background:#fff2e4;color:#bd6c0b}
.package-tools{padding:0 19px 11px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{border:0;border-radius:999px;background:#f2f6fb;color:#71819d;padding:7px 11px;font-size:10px;font-weight:800}
.tab.active{background:#e4f2ff;color:var(--sg-blue)}
.package-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:3px 16px 18px}
.package-card{border:1px solid #e5edf7;border-radius:17px;background:#fff;overflow:hidden;box-shadow:0 4px 14px rgba(40,72,114,.05);transition:.18s ease}
.package-card:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(40,72,114,.10)}
.package-visual{height:94px;position:relative;overflow:hidden;background:
 radial-gradient(circle at 75% 20%,rgba(255,255,255,.75),transparent 18%),
 linear-gradient(130deg,#dff1ff,#edf7ff 45%,#cfe9ff)}
.package-visual.v2{background:linear-gradient(135deg,#eaf1ff,#f0ebff 48%,#e8f9ff)}
.package-visual.v3{background:linear-gradient(135deg,#e5fbf3,#ecf8ff 50%,#d9efff)}
.package-visual.v4{background:linear-gradient(135deg,#fff2e1,#eff6ff 52%,#e1edff)}
.package-orb{position:absolute;border-radius:50%;filter:blur(.2px);opacity:.8}
.package-orb.one{width:90px;height:90px;right:18px;top:10px;background:linear-gradient(145deg,rgba(11,124,255,.42),rgba(53,200,255,.05))}
.package-orb.two{width:65px;height:65px;right:73px;top:35px;border:14px solid rgba(255,255,255,.7)}
.package-symbol{position:absolute;left:16px;bottom:13px;color:#fff;width:36px;height:36px;border-radius:12px;background:linear-gradient(145deg,var(--sg-blue),var(--sg-cyan));display:grid;place-items:center;font-weight:900;box-shadow:0 5px 12px rgba(11,124,255,.25)}
.package-state{position:absolute;right:10px;top:9px}
.package-body{padding:13px}
.package-body h3{font-size:14px;line-height:1.3;margin:0 0 5px;color:var(--sg-navy)}
.package-objective{font-size:10px;color:var(--sg-muted);line-height:1.4;min-height:29px}
.chips{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}
.chip{display:inline-flex;align-items:center;gap:5px;border-radius:8px;background:#f1f6fb;color:#60718e;padding:5px 7px;font-size:9px;font-weight:800}
.chip.blue{background:#e9f4ff;color:var(--sg-blue)}
.package-foot{margin-top:10px;padding-top:9px;border-top:1px solid #edf2f7;display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:9px;color:#7b8ba7}
.material-toolbar{padding:0 18px 12px;display:flex;gap:9px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.search{position:relative;min-width:210px;flex:0 1 280px}
.search input{width:100%;border:1px solid var(--sg-line);background:#f9fbfe;border-radius:11px;padding:9px 11px 9px 31px;color:var(--sg-text);outline:none;font-size:11px}
.search input:focus{border-color:#b5d9ff;box-shadow:0 0 0 3px rgba(11,124,255,.08)}
.search:before{content:"⌕";position:absolute;left:11px;top:6px;color:#8aa0bf;font-size:17px}
.material-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px;padding:2px 16px 18px}
.material-card{border:1px solid #e5edf7;background:#fff;border-radius:15px;padding:0;overflow:hidden;text-align:left;min-width:0;transition:.18s ease}
.material-card:hover{box-shadow:0 8px 22px rgba(40,72,114,.09);transform:translateY(-1px)}
.material-cover{height:74px;background:linear-gradient(145deg,#e9f4ff,#f8fbff);position:relative;display:flex;align-items:flex-end;padding:10px}
.material-cover.article{background:linear-gradient(145deg,#edf5ff,#dcedff)}
.material-cover.channel_text{background:linear-gradient(145deg,#e8fbf4,#eaf5ff)}
.material-cover.video_script{background:linear-gradient(145deg,#f2ecff,#e9f5ff)}
.material-kind{font-size:9px;font-weight:900;border-radius:7px;background:rgba(255,255,255,.9);padding:4px 7px;color:#425473}
.material-body{padding:10px 11px 12px}
.material-body h4{font-size:11px;line-height:1.35;margin:0;color:#243451;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.material-body p{font-size:9px;color:#8897ae;margin:5px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.preview{position:sticky;top:20px}
.preview-empty{padding:34px 24px;text-align:center;color:var(--sg-muted)}
.preview-hero{height:155px;background:linear-gradient(145deg,#dff1ff,#eef7ff 55%,#d8eaff);position:relative;overflow:hidden}
.preview-hero:before,.preview-hero:after{content:"";position:absolute;border-radius:50%}
.preview-hero:before{width:180px;height:180px;right:-30px;top:-80px;background:linear-gradient(145deg,rgba(11,124,255,.30),rgba(53,200,255,.02))}
.preview-hero:after{width:110px;height:110px;right:95px;bottom:-55px;border:20px solid rgba(255,255,255,.7)}
.preview-hero .brand-mark{position:absolute;left:20px;bottom:18px;width:48px;height:48px;font-size:26px}
.preview-content{padding:18px}
.preview-kicker{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.preview-content h3{font-size:20px;line-height:1.15;margin:0 0 10px;color:var(--sg-navy);letter-spacing:-.4px}
.preview-copy{font-size:11px;line-height:1.65;color:#53637f;max-height:240px;overflow:auto;padding-right:4px;white-space:pre-wrap}
.preview-actions{display:flex;gap:8px;margin-top:15px}
.unknowns{padding:3px 18px 18px}
.unknowns details{border:1px dashed #dbe6f3;border-radius:14px;background:#fbfdff;padding:10px 12px}
.unknowns summary{cursor:pointer;font-size:11px;font-weight:850;color:#5c6e8b}
.unknowns ul{font-size:10px;color:#71819b;line-height:1.55;padding-left:18px}
.empty{padding:28px;text-align:center;color:var(--sg-muted);font-size:12px}
.mobile-nav{display:none}
.dialog{border:0;padding:0;border-radius:22px;width:min(860px,calc(100vw - 32px));max-height:88vh;box-shadow:0 30px 90px rgba(12,35,70,.28);background:#fff;color:var(--sg-text)}
.dialog::backdrop{background:rgba(12,26,52,.55);backdrop-filter:blur(5px)}
.dialog-head{position:sticky;top:0;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);z-index:2;padding:15px 18px;border-bottom:1px solid var(--sg-line);display:flex;justify-content:space-between;gap:12px;align-items:center}
.dialog-head h3{margin:0;font-size:18px;color:var(--sg-navy)}
.dialog-close{width:34px;height:34px;border:1px solid var(--sg-line);background:#fff;border-radius:11px;color:#63728c;font-size:19px}
.dialog-body{padding:20px}
.article-text{font-size:13px;line-height:1.75;color:#42526e;white-space:pre-wrap}
.storyboard-grid{display:grid;gap:10px}
.story-shot{border:1px solid #e5edf6;background:#f9fbfe;border-radius:14px;padding:13px}
.story-shot-head{display:flex;justify-content:space-between;gap:8px;margin-bottom:8px}
.story-shot time{font-size:10px;font-weight:900;color:var(--sg-blue)}
.story-shot b{font-size:11px;color:#344665}
.story-shot p{font-size:10px;line-height:1.55;color:#6c7e99;margin:6px 0}
.story-prompt{padding:9px;border-radius:10px;background:#eef6ff;color:#48617f;font-size:9px;line-height:1.5}
.dialog-actions{position:sticky;bottom:0;background:#fff;border-top:1px solid var(--sg-line);padding:12px 18px;display:flex;gap:8px;justify-content:flex-end}
.product-home{max-width:980px;margin:0 auto;padding:48px 22px}
.product-home .hero{text-align:center;padding:34px}
.product-home .hero .brand{justify-content:center;padding:0}
.product-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-top:24px}
.product-link{display:block;padding:19px;border:1px solid var(--sg-line);border-radius:18px;background:#fff;box-shadow:var(--sg-shadow-soft)}
.product-link strong{font-size:17px;color:var(--sg-navy)}
.product-link small{display:block;color:var(--sg-muted);margin-top:4px}
@media(max-width:1180px){
  .workspace{grid-template-columns:1fr}
  .preview{display:none}
  .material-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
}
@media(max-width:920px){
  .studio-layout{grid-template-columns:1fr}
  .sidebar{display:none}
  .main{padding:18px 14px 92px}
  .topbar{align-items:center}
  .cycle-card{display:none}
  .product-pill{min-width:auto}
  .product-pill div:last-child{display:none}
  h1{font-size:27px}
  .kpis{display:flex;overflow-x:auto;gap:9px;padding-bottom:3px;scroll-snap-type:x proximity}
  .kpi{min-width:162px;scroll-snap-align:start}
  .workspace{display:block}
  .stack{gap:13px}
  .panel{margin-bottom:13px;border-radius:19px}
  .package-grid{display:flex;overflow-x:auto;scroll-snap-type:x proximity;padding-bottom:18px}
  .package-card{min-width:235px;scroll-snap-align:start}
  .material-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .mobile-nav{display:grid;grid-template-columns:repeat(4,1fr);position:fixed;bottom:0;left:0;right:0;background:rgba(255,255,255,.95);backdrop-filter:blur(14px);border-top:1px solid var(--sg-line);padding:7px 8px max(7px,env(safe-area-inset-bottom));z-index:40;box-shadow:0 -8px 24px rgba(31,75,128,.08)}
  .mobile-nav a{display:grid;place-items:center;gap:2px;color:#7585a0;font-size:9px;font-weight:800;padding:5px;border-radius:10px}
  .mobile-nav a.active{color:var(--sg-blue);background:#edf6ff}
  .mobile-nav .m-ico{font-size:16px}
}
@media(max-width:620px){
  .main{padding-top:14px}
  .topbar{margin-bottom:14px}
  .top-actions{gap:6px}
  .product-pill{padding:7px}
  .product-avatar{width:31px;height:31px}
  .subtitle{font-size:12px;max-width:310px}
  .kpi{min-width:145px;padding:13px;min-height:82px}
  .kpi-icon{width:40px;height:40px;border-radius:13px}
  .kpi strong{font-size:20px}
  .panel-head{padding:14px 14px 9px}
  .panel-title h2{font-size:15px}
  .calendar-scroll{padding-left:12px;padding-right:12px}
  .calendar-grid{grid-template-columns:repeat(var(--days,5),152px);min-width:max-content}
  .day-card{min-height:150px}
  .package-grid{padding-left:12px;padding-right:12px}
  .package-tools{padding-left:13px;padding-right:13px}
  .material-toolbar{padding-left:13px;padding-right:13px}
  .material-grid{display:flex;overflow-x:auto;scroll-snap-type:x proximity;padding-left:12px;padding-right:12px}
  .material-card{min-width:155px;scroll-snap-align:start}
  .search{order:2;min-width:100%;flex-basis:100%}
  .dialog{width:100vw;max-height:100dvh;height:100dvh;border-radius:0;margin:0;max-width:none}
  .dialog-head{padding-top:max(14px,env(safe-area-inset-top))}
  .dialog-body{padding:17px 15px 90px}
  .dialog-actions{position:fixed;bottom:0;left:0;right:0;padding-bottom:max(12px,env(safe-area-inset-bottom))}
  .article-text{font-size:14px}
}

/* Mockup-parity mobile composition */
.mobile-only-dashboard{display:none}
.m-brand,.m-hero,.m-kpis,.m-card{display:none}

@media(max-width:920px){
  body{background:linear-gradient(180deg,#f8fbff 0,#eef6ff 46%,#f5f9fd 100%)}
  .main{padding:0 14px 92px}
  .topbar,.desktop-only-dashboard{display:none!important}
  .mobile-only-dashboard{display:block;max-width:560px;margin:0 auto;padding-top:14px}
  .m-brand{display:flex;align-items:center;gap:10px;height:54px}
  .m-logo-mark{
    width:37px;height:37px;border-radius:14px;display:grid;place-items:center;
    background:linear-gradient(145deg,#20c5ff 0%,#0879ff 55%,#5a65ff 100%);
    color:#fff;font-size:23px;font-weight:950;box-shadow:0 8px 18px rgba(8,121,255,.20);
    transform:rotate(-7deg)
  }
  .m-logo-word{font-size:25px;font-weight:900;letter-spacing:-.8px;color:#071634}
  .m-logo-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
  .m-bell,.m-avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:#fff;border:1px solid #e5eef9;color:#72829e;font-weight:900}
  .m-avatar{background:linear-gradient(145deg,#e9f3ff,#fff);color:var(--sg-blue);box-shadow:0 4px 14px rgba(28,77,133,.08)}
  .m-hero{display:block;padding:10px 2px 15px}
  .m-title-row{display:flex;align-items:center;gap:10px}
  .m-title-row h1{margin:0;font-size:29px;line-height:1.04;letter-spacing:-1px;color:#071634}
  .m-hero p{margin:7px 0 0;color:#6d80a0;font-size:13px;line-height:1.5;max-width:360px}
  .m-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}
  .m-kpi{
    min-width:0;min-height:154px;background:rgba(255,255,255,.96);border:1px solid #e6eef8;border-radius:22px;
    padding:14px 12px 13px;box-shadow:0 10px 26px rgba(49,83,126,.07);display:flex;flex-direction:column;align-items:flex-start
  }
  .m-kpi-icon{width:45px;height:45px;border-radius:15px;display:grid;place-items:center;font-size:18px;font-weight:900;margin-bottom:13px}
  .m-kpi.blue .m-kpi-icon{background:#e8f4ff;color:#087cff}
  .m-kpi.green .m-kpi-icon{background:#e5fbf2;color:#0cbb78}
  .m-kpi.purple .m-kpi-icon{background:#f1eaff;color:#8558f7}
  .m-kpi strong{display:flex;flex-direction:column;font-size:25px;line-height:.9;color:#071634;letter-spacing:-.7px}
  .m-kpi strong small{font-size:12px;line-height:1.2;margin-top:6px;color:#071634;letter-spacing:0}
  .m-kpi p{font-size:10px;line-height:1.35;color:#8493ad;margin:9px 0 0}
  .m-mini-platforms{display:flex;gap:4px;margin-top:auto;flex-wrap:wrap}
  .m-mini-platforms span{width:23px;height:23px;border-radius:7px;background:#edf5ff;color:#0b7cff;display:grid;place-items:center;font-size:8px;font-weight:900}
  .m-card{
    display:block;background:rgba(255,255,255,.97);border:1px solid #e5edf7;border-radius:24px;
    box-shadow:0 12px 30px rgba(47,80,122,.07);margin-bottom:14px;padding:14px
  }
  .m-section-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:13px;color:#071634}
  .m-section-head>div{display:flex;align-items:center;gap:9px;min-width:0}
  .m-section-head b{font-size:16px;letter-spacing:-.35px}
  .m-section-head>a,.m-section-head>span{color:#087cff;font-size:11px;font-weight:850}
  .m-section-icon{width:28px;height:28px;border-radius:10px;background:#e9f5ff;color:#087cff;display:grid;place-items:center}
  .m-days{display:grid;grid-template-columns:repeat(5,minmax(58px,1fr));gap:6px;margin-bottom:16px}
  .m-day{border:0;border-radius:15px;background:#f5f9ff;color:#263858;padding:9px 4px 8px;min-height:78px;display:flex;flex-direction:column;align-items:center;justify-content:center}
  .m-day b{font-size:11px}.m-day span{font-size:10px;margin-top:3px}.m-day i{width:5px;height:5px;border-radius:50%;background:#92a9cf;margin-top:8px}
  .m-day.active{background:linear-gradient(150deg,#1b8cff,#096fff);color:#fff;box-shadow:0 8px 18px rgba(11,124,255,.24)}
  .m-day.active i{background:#fff}
  .m-timeline{display:grid;gap:8px;position:relative}
  .m-timeline:before{content:"";position:absolute;left:47px;top:17px;bottom:17px;width:1px;background:#dbe8f7}
  .m-event{position:relative;display:grid;grid-template-columns:39px 36px minmax(0,1fr) 18px;align-items:center;gap:7px;min-height:61px}
  .m-event time{font-size:11px;color:#6c80a1;font-weight:800}
  .m-platform{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(145deg,#1da4ff,#0872f8);color:#fff;font-weight:950;font-size:9px;z-index:1;box-shadow:0 6px 14px rgba(10,116,249,.18)}
  .m-event-copy{background:#fff;border:1px solid #e8eff8;border-radius:14px;padding:10px 11px;box-shadow:0 4px 12px rgba(36,69,112,.04);min-width:0}
  .m-event-copy b{display:block;font-size:11px;line-height:1.3;color:#182946;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .m-event-copy small{display:block;font-size:9px;color:#8998b1;margin-top:3px}
  .m-more{color:#7f90ab;font-size:18px}
  .m-calendar-open{height:51px;border-radius:15px;background:#edf6ff;color:#0b75f4;margin-top:14px;display:flex;align-items:center;gap:10px;padding:0 14px;font-size:12px;font-weight:900}
  .m-calendar-open b{margin-left:auto;font-size:17px}
  .m-all-pill{padding:5px 10px;border-radius:999px;background:#ecf5ff;color:#087cff!important}
  .m-package-actions{margin:-2px 0 10px}
  .m-package-actions form{display:inline-block}
  .m-package-actions .btn{height:37px;border-radius:11px}
  .m-package-carousel{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding:2px 2px 8px;scrollbar-width:none}
  .m-package-carousel::-webkit-scrollbar{display:none}
  .m-package{
    min-width:248px;width:248px;border:1px solid #e6edf6;border-radius:18px;background:#fff;overflow:hidden;
    box-shadow:0 7px 18px rgba(36,72,120,.06);scroll-snap-align:start
  }
  .m-package-cover{height:112px;position:relative;overflow:hidden;background:linear-gradient(145deg,#deefff,#bfdcff)}
  .m-package-cover:before,.m-package-cover:after{content:"";position:absolute}
  .m-package-cover:before{left:-10%;right:-10%;bottom:-42px;height:100px;border-radius:50% 50% 0 0;background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(31,116,196,.40))}
  .m-package-cover:after{width:92px;height:92px;border:16px solid rgba(255,255,255,.65);border-radius:50%;right:20px;top:20px}
  .m-scene-1 .m-package-cover{background:linear-gradient(160deg,#ffd9bd,#d9e9ff 58%,#9dc8f7)}
  .m-scene-2 .m-package-cover{background:linear-gradient(160deg,#d8f4ed,#cfe6ff 55%,#9bc5f2)}
  .m-scene-3 .m-package-cover{background:linear-gradient(160deg,#f5dfc7,#e4eefc 52%,#9fc7ee)}
  .m-package-status{position:absolute;left:10px;top:9px;z-index:2;background:rgba(255,255,255,.88);backdrop-filter:blur(8px);border-radius:999px;padding:5px 8px;font-size:9px;font-weight:900;color:#5d6d86}
  .m-package-status.ready_for_review{background:#e9fbf3;color:#079262}
  .m-package-body{padding:11px}
  .m-package-body h3{font-size:13px;line-height:1.25;margin:0;color:#0b1934;min-height:33px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .m-package-body p{font-size:9px;line-height:1.4;color:#7b8ba4;margin:5px 0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .m-channel-row{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
  .m-channel-row span{min-width:22px;height:22px;padding:0 5px;border-radius:7px;background:#eaf4ff;color:#087cff;display:grid;place-items:center;font-size:8px;font-weight:900}
  .m-channel-row em{font-style:normal;margin-left:auto;font-size:8px;color:#8897ad}
  .m-package-date{border-top:1px solid #eef2f7;margin-top:9px;padding-top:8px;font-size:9px;color:#637794}
  .m-dots{display:flex;justify-content:center;gap:5px;margin-top:8px}
  .m-dots i{width:6px;height:6px;border-radius:50%;background:#cfdced}.m-dots i.active{background:#087cff}
  .m-material-strip{display:flex;gap:9px;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;padding-bottom:2px}
  .m-material-strip::-webkit-scrollbar{display:none}
  .m-material{min-width:142px;width:142px;border:1px solid #e6edf6;background:#fff;border-radius:16px;padding:8px;text-align:left;scroll-snap-align:start}
  .m-material-type{display:inline-flex;padding:4px 6px;border-radius:7px;background:#eaf5ff;color:#087cff;font-size:8px;font-weight:900}
  .m-material-thumb{height:68px;border-radius:11px;margin:7px 0 8px;display:grid;place-items:center;font-size:23px;color:#fff;background:linear-gradient(150deg,#9dc9ff,#4f91ee)}
  .m-material.post .m-material-thumb{background:linear-gradient(150deg,#9de3d3,#4aa8cb)}
  .m-material.video .m-material-thumb{background:linear-gradient(150deg,#c9b7ff,#677ff1)}
  .m-material b{display:block;font-size:10px;line-height:1.25;color:#13233e;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .m-material small{display:block;font-size:8px;line-height:1.35;color:#8a98af;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mobile-nav{border-radius:20px 20px 0 0;padding-top:9px}
}
@media(max-width:390px){
  .m-kpis{gap:7px}.m-kpi{padding:12px 9px;min-height:146px}.m-kpi strong{font-size:22px}
  .m-day{min-height:72px}.m-package{min-width:230px;width:230px}
}


/* Functional multi-page Growth Studio */
.route-main{min-width:0}
.route-mobile-brand{display:none}
.route-page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:18px}
.route-page-head h1{margin:5px 0 4px}
.back-link{display:inline-flex;margin-bottom:6px;color:#6f7f9f;font-size:11px;font-weight:800}
.back-link:hover{color:var(--sg-blue)}
.route-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.route-content{display:grid;gap:16px}
.route-summary{overflow:visible}
.route-package-grid{padding:0;grid-template-columns:repeat(3,minmax(0,1fr))}
.route-list{padding:0 18px 18px;display:grid;gap:8px}
.route-list-row{display:grid;grid-template-columns:30px 1fr;gap:10px;align-items:start;border:1px solid var(--sg-line);border-radius:13px;background:#fbfdff;padding:11px}
.route-list-row>span{width:27px;height:27px;border-radius:9px;background:#eef6ff;color:var(--sg-blue);display:grid;place-items:center;font-weight:900}
.route-list-row p{margin:3px 0 0;font-size:11px;line-height:1.5;color:#52627e}
.calendar-date-tabs{padding:0 18px 13px;overflow-x:auto}
.calendar-date-tabs .tabs{flex-wrap:nowrap;width:max-content}
.calendar-list{display:grid;border-top:1px solid var(--sg-line)}
.calendar-row{display:grid;grid-template-columns:120px 145px minmax(0,1fr) 150px 130px 24px;gap:12px;align-items:center;padding:14px 18px;border-bottom:1px solid #edf2f7;transition:.16s ease;background:#fff}
.calendar-row:hover{background:#f8fbff}
.calendar-time{font-size:12px;font-weight:850;color:#334563}
.calendar-platform{display:flex;align-items:center;gap:8px;min-width:0}
.calendar-platform b{font-size:11px;color:#334563}
.calendar-topic{min-width:0}
.calendar-topic b{display:block;font-size:12px;color:#172845;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.calendar-topic small{display:block;font-size:9px;color:#8493ad;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.calendar-prepare{font-size:9px;color:#8796ad;line-height:1.35}
.calendar-prepare b{color:#536580}
.calendar-arrow{font-size:20px;color:#8aa0bf}
.library-controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:0 18px 14px}
.library-controls .search{flex:1 1 280px}
.route-material-grid{padding-top:2px;grid-template-columns:repeat(4,minmax(0,1fr))}
.material-detail-card{overflow:visible}
.material-detail-body{padding:0 22px 24px}
.material-detail-body h2{font-size:22px;color:var(--sg-navy);margin:14px 0}
.detail-meta{display:flex;gap:7px;flex-wrap:wrap}
.material-detail-body .article-text{font-size:14px;line-height:1.8;max-height:none;overflow:visible;background:#fbfdff;border:1px solid #e8eef7;border-radius:16px;padding:18px}
.package-detail-slots{display:grid;padding:0 18px 18px}
.package-slot-row{display:grid;grid-template-columns:1.2fr 1fr 1fr 130px;gap:12px;align-items:center;padding:13px 0;border-top:1px solid #edf2f7}
.package-slot-row:first-child{border-top:0}
.package-slot-row>div{display:grid;gap:3px}
.package-slot-row>div:first-child{display:flex;align-items:center;gap:8px}
.package-slot-row b{font-size:11px;color:#344663}
.package-slot-row small{font-size:9px;color:#8a98ad}
.analytics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.analytics-statuses{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;padding:0 18px 18px}
.analytics-statuses>div{border:1px solid var(--sg-line);background:#fbfdff;border-radius:13px;padding:12px;display:flex;align-items:center;justify-content:space-between;gap:10px}
.analytics-statuses b{font-size:18px;color:var(--sg-navy)}

@media(max-width:1100px){
  .route-package-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .route-material-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .calendar-row{grid-template-columns:100px 120px minmax(0,1fr) 130px 110px 20px}
}
@media(max-width:920px){
  .route-main{padding-top:0}
  .route-mobile-brand{display:block}
  .route-mobile-brand .m-brand{display:flex}
  .route-page-head{padding:8px 0 4px;align-items:flex-end}
  .route-page-head .eyebrow{display:none}
  .route-page-head h1{font-size:25px}
  .route-page-head .subtitle{font-size:12px}
  .back-link{margin-top:2px}
  .route-actions{max-width:46%;justify-content:flex-end}
  .route-package-grid{display:flex;overflow-x:auto;scroll-snap-type:x proximity;padding-bottom:4px}
  .route-package-grid .package-card{min-width:245px;scroll-snap-align:start}
  .route-material-grid{grid-template-columns:repeat(2,minmax(0,1fr));padding-left:12px;padding-right:12px}
  .calendar-row{grid-template-columns:68px 52px minmax(0,1fr) 24px;padding:12px 13px;gap:8px}
  .calendar-platform b,.calendar-prepare,.calendar-row>.status{display:none}
  .calendar-topic b{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .package-slot-row{grid-template-columns:1fr auto;gap:8px}
  .package-slot-row>div:nth-child(2),.package-slot-row>div:nth-child(3){grid-column:1 / -1;grid-template-columns:100px 1fr;align-items:center}
  .analytics-grid{display:flex;overflow-x:auto;scroll-snap-type:x proximity}
  .analytics-grid .kpi{min-width:175px;scroll-snap-align:start}
}
@media(max-width:560px){
  .route-page-head{display:block}
  .route-actions{max-width:none;margin-top:10px;justify-content:flex-start}
  .route-actions .btn{width:auto}
  .route-material-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:0 10px 12px}
  .library-controls{padding:0 12px 12px}
  .library-controls .tabs{width:100%;overflow-x:auto;flex-wrap:nowrap}
  .material-detail-body{padding:0 14px 18px}
  .material-detail-body h2{font-size:19px}
  .material-detail-body .article-text{padding:14px;font-size:13px}
  .package-detail-slots{padding:0 13px 14px}
}

"""

STUDIO_JS = r"""
(function(){
  const q=(s,r=document)=>r.querySelector(s), qa=(s,r=document)=>Array.from(r.querySelectorAll(s));
  qa('[data-open-dialog]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const d=document.getElementById(btn.dataset.openDialog);
      if(d&&d.showModal)d.showModal();
    });
  });
  qa('[data-close-dialog]').forEach(btn=>btn.addEventListener('click',()=>btn.closest('dialog')?.close()));
  qa('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d)d.close()}));
  qa('[data-filter]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      qa('[data-filter]').forEach(x=>x.classList.remove('active')); btn.classList.add('active');
      applyFilters();
    });
  });
  const search=q('#material-search');
  if(search) search.addEventListener('input',applyFilters);
  function applyFilters(){
    const active=q('[data-filter].active')?.dataset.filter||'all';
    const term=(search?.value||'').trim().toLowerCase();
    qa('[data-material]').forEach(card=>{
      const kind=card.dataset.kind||'';
      const hay=(card.dataset.search||'').toLowerCase();
      card.hidden=!((active==='all'||kind===active)&&(!term||hay.includes(term)));
    });
  }
  qa('[data-copy]').forEach(btn=>btn.addEventListener('click',async()=>{
    const el=document.getElementById(btn.dataset.copy);
    if(!el)return;
    const text=el.tagName==='TEXTAREA'?el.value:(el.innerText||el.textContent||'');
    try{await navigator.clipboard.writeText(text);const old=btn.textContent;btn.textContent='Скопировано';setTimeout(()=>btn.textContent=old,1400)}catch(_){
      const range=document.createRange();range.selectNodeContents(el);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      btn.textContent='Текст выделен: нажмите Ctrl+C / ⌘C';btn.setAttribute('role','status');
    }
  }));
})();
"""

# Task-first redesign layered over the existing dashboard and its legacy selectors.
from app.api.content_studio_refresh import CSS as STUDIO_REFRESH_CSS, JS as STUDIO_REFRESH_JS
STUDIO_CSS += "\n" + STUDIO_REFRESH_CSS
STUDIO_CSS += """
/* Keep publication state visible on compact screens. */
@media(max-width:920px){.calendar-row>.status{display:inline-flex;grid-column:3;white-space:normal}.calendar-row{grid-template-columns:60px 32px minmax(0,1fr) 16px}.calendar-arrow{grid-column:4;grid-row:1}}
.sg-muted,.sg-footnote,.panel-sub,.sg-field small,.sg-overline,.sg-form-hint,.sg-channel-line small{color:#526176}
.sg-publication-row{flex-wrap:wrap;gap:12px}.sg-publication-row>div{display:flex;flex-direction:column;gap:6px}
.sg-manual-editor textarea{width:100%;min-height:160px}.sg-manual-editor{margin-top:16px}
"""
STUDIO_JS += "\n" + STUDIO_REFRESH_JS

# v6: official ServGo assets, brand colors and non-overlay mobile navigation.
from app.api.content_studio_brand_fix import CSS as STUDIO_BRAND_FIX_CSS
STUDIO_CSS += "\n" + STUDIO_BRAND_FIX_CSS

# Last cascade: consistent product typography for every Studio view.
from app.api.content_studio_typography import CSS as STUDIO_TYPOGRAPHY_CSS
STUDIO_CSS += "\n" + STUDIO_TYPOGRAPHY_CSS

from app.api.content_studio_work_ui import CSS as STUDIO_WORK_CSS, JS as STUDIO_WORK_JS
STUDIO_CSS += "\n" + STUDIO_WORK_CSS
STUDIO_JS += "\n" + STUDIO_WORK_JS

# Fixes found on a real phone: loaded last so they win over every earlier layer.
from app.api.content_studio_fixes import CSS as STUDIO_FIXES_CSS, JS as STUDIO_FIXES_JS
STUDIO_CSS += "\n" + STUDIO_FIXES_CSS
STUDIO_JS += "\n" + STUDIO_FIXES_JS
from app.api.content_studio_guide import CSS as STUDIO_GUIDE_CSS
STUDIO_CSS += "\n" + STUDIO_GUIDE_CSS
from app.api.content_studio_air import CSS as STUDIO_AIR_CSS, JS as STUDIO_AIR_JS
STUDIO_CSS += "\n" + STUDIO_AIR_CSS
STUDIO_JS += "\n" + STUDIO_AIR_JS
