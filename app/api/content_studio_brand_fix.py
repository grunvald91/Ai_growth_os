"""Growth Studio v6: canonical ServGo identity and layout corrections.

This layer deliberately comes after v5 and overrides its stale navy/blue
palette and a viewport-fixed mobile toolbar that obscured content.
"""
CSS = r"""
/* Brand source: booking_bot/web/frontend/src/components/Logo.tsx and landing.css */
:root{
  --sg-blue:#2F5BEB;
  --sg-blue-600:#244BD6;
  --sg-blue-700:#1F40B9;
  --sg-navy:#0D1B2A;
  --sg-text:#1A2939;
  --sg-muted:#596B7E;
  --sg-line:#E6E9EF;
  --sg-bg:#F2F4F7;
  --sg-card:#FFFFFF;
  --sg-sky:#E7EEFE;
  --sg-shadow:0 1px 2px rgba(13,27,42,.03),0 8px 23px rgba(13,27,42,.055);
  --sg-radius:17px;
  --sg-radius-md:13px;
  --sg-radius-sm:10px;
  font-family:Montserrat,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
body{background:var(--sg-bg);color:var(--sg-text);font-family:Montserrat,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;-webkit-font-smoothing:antialiased}
.studio-page,.studio-layout,.main,.route-main{min-width:0}
.studio-layout{grid-template-columns:248px minmax(0,1fr)}
.main,.route-main{width:100%;max-width:none;padding:26px clamp(16px,2.1vw,34px) 64px}
.sidebar{border-color:var(--sg-line);padding:22px 15px 17px;background:#fff}
.sidebar .sg-official-brand{margin:0 8px 17px}
.sg-official-brand{display:inline-flex;align-items:center;gap:10px;text-decoration:none;max-width:100%;min-width:0;color:var(--sg-navy)}
.sg-official-brand:hover{text-decoration:none}
.sg-official-brand-icon{display:block;width:43px;height:43px;flex:0 0 43px;object-fit:contain}
.sg-official-brand-wordmark{display:grid;align-items:center;gap:1px;min-width:0}
.sg-official-brand-name{font-size:23px;font-weight:800;line-height:1.04;color:#0D1B2A;letter-spacing:-.04em;white-space:nowrap}
.sg-official-brand-name span{color:#2F5BEB}
.sg-brand-caption{display:block;font-size:10px;font-weight:600;color:#6B7280;line-height:1.25;white-space:nowrap}
.sg-mobile-shell-head{display:none}
.nav-label{font-size:10px;letter-spacing:.08em;color:#7F8B9B;margin:18px 10px 9px}
.sidebar .nav a{font-size:13px;line-height:1.35;padding:10px 12px;min-height:45px;border-radius:10px;color:#435569;font-weight:600}
.sidebar .nav a.active{color:var(--sg-blue);background:#EDF2FF;font-weight:750}
.sidebar .nav a:hover{color:var(--sg-blue);background:#F4F7FF}
.sidebar .nav a .nav-ico{font-size:17px;min-width:21px;text-align:center}
.sidebar-promo{border:1px solid #E6E9EF;background:#F8FAFD;box-shadow:none}
.sidebar-promo b{font-size:12px}
.sidebar-promo p{font-size:11px;line-height:1.5}
.topbar,.route-page-head{margin-bottom:19px}
.topbar h1{font-size:clamp(26px,2.4vw,33px);font-weight:800;letter-spacing:-.025em}
.subtitle{font-size:13px;line-height:1.6;color:#596B7E}
.sg-surface,.panel{border-color:#E6E9EF;box-shadow:var(--sg-shadow)}
.btn.primary{background:#2F5BEB;color:#fff;border-color:#2F5BEB;box-shadow:none}
.btn.primary:hover{background:#244BD6;color:#fff}
.btn.secondary,.btn.ghost{color:#284E9C;border:1px solid #DAE0EA;background:#fff}
.btn.secondary:hover,.btn.ghost:hover{background:#F3F6FF;color:#244BD6}
.btn:focus-visible,.mobile-nav :is(a,button):focus-visible,.sg-official-brand:focus-visible,.sg-mobile-project:focus-visible{outline:3px solid #819DFF;outline-offset:2px}
.sg-overline{font-size:11px;font-weight:750;letter-spacing:.1em;color:#697991}
.sg-workflow-banner{background:linear-gradient(120deg,#F0F4FF,#F8FAFF);border:1px solid #DFE7FA;box-shadow:none;padding:24px 27px}
.sg-workflow-banner h2{color:#0D1B2A;font-size:clamp(21px,2vw,26px);font-weight:800;line-height:1.28}
.sg-workflow-banner p{color:#4B5E72;font-size:13px;max-width:540px}
.sg-priority-item{min-width:0;border-color:#E1E6EF;box-shadow:none}
.sg-priority-item strong{color:#0D1B2A;font-weight:800;font-size:25px}
.sg-priority-item span{font-size:12px;line-height:1.4;color:#4F6178}
.sg-priority-item em{font-size:12px;color:#2F5BEB}
.sg-quick-links{overflow:visible}
.sg-quick-links a{color:#3054AD;border:1px solid #E0E7F0;font-size:12px;min-height:44px}
.sg-dashboard-feed{grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}
.sg-dashboard-feed .sg-surface{padding:20px;min-width:0}
.sg-dashboard-feed .sg-section-head{gap:8px}
.sg-dashboard-feed .sg-section-head h2{font-size:18px;line-height:1.3}
.sg-dashboard-feed .sg-section-head p{font-size:12px;line-height:1.5}
.sg-mini-date{font-size:12px;line-height:1.3;min-width:90px;white-space:nowrap;color:#24479E;background:#EEF2FF}
.sg-mini-row{gap:12px;min-height:68px}
.sg-mini-row b{font-size:13px;font-weight:700;line-height:1.45;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.sg-mini-row small{font-size:11px;color:#57687C;line-height:1.4;white-space:normal}
.sg-mini-row>div{gap:5px}
.sg-mini-link{font-size:12px;color:#2F5BEB}
.sg-priority-strip{gap:11px}
.sg-legacy-details{border-color:#E2E7EF;box-shadow:none}
.sg-legacy-details>summary{font-size:13px;line-height:1.5;padding:8px}
.sg-official-brand-icon,.sg-product-avatar{background:transparent;border-radius:0;box-shadow:none;padding:0}
.product-pill .sg-product-avatar{object-fit:contain;flex:0 0 38px}
.mobile-nav{display:none}
@media(max-height:720px) and (min-width:921px){.sidebar-promo{display:none}}
/* v6.1: readable native typography. Montserrat stays in the official brand
   wordmark only; interface text uses platform fonts with natural 400–700 weights. */
:root{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Arial,sans-serif;
}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Arial,sans-serif;
  font-size:15px;font-weight:400;line-height:1.55;letter-spacing:0;
}
button,input,select,textarea{font:inherit}
h1,h2,h3,h4,h5,h6{font-weight:700;letter-spacing:-.015em;line-height:1.35}
p,small,.subtitle{font-weight:400;line-height:1.55}
b,strong{font-weight:650}
.sg-official-brand-name{
  font-family:Montserrat,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  font-weight:800;letter-spacing:-.035em;
}
.topbar h1,.route-page-head h1{font-weight:700;letter-spacing:-.025em}
.sg-section-head h2,.sg-surface h2{font-weight:700;letter-spacing:-.02em;line-height:1.35}
.sg-workflow-banner h2{font-weight:700;letter-spacing:-.015em}
.sg-mini-row b{font-size:14px;font-weight:600;line-height:1.45}
.sg-mini-row small{font-size:12px;line-height:1.45}
.sg-dashboard-feed .sg-section-head h2{font-weight:700;font-size:18px}
.sg-dashboard-feed .sg-section-head p{font-size:13px}
.sg-overline,.sg-draft-alert-eyebrow{font-weight:650;letter-spacing:.07em}
.sg-priority-item strong{font-weight:700}
.sg-priority-item span{font-weight:400}
.sg-priority-item em{font-weight:600}
.sidebar .nav a{font-weight:500}
.sidebar .nav a.active{font-weight:650}
.btn,.sg-quick-links a,.mobile-nav :is(a,button){font-weight:600}
.sg-mini-link,.sg-legacy-details>summary{font-weight:600}
.sg-draft-alert{
  display:grid;grid-template-columns:44px minmax(0,1fr) auto;
  align-items:center;gap:16px;
  min-width:0;
  background:#F2F5FF;border:1px solid #DBE3FA;
  border-radius:16px;padding:19px 21px;
  margin:0 0 18px;
  box-shadow:0 1px 3px rgba(13,27,42,.035);
}
.sg-draft-alert-icon{
  display:grid;place-items:center;width:44px;height:44px;flex:0 0 44px;
  border-radius:12px;background:#DEE8FF;color:#2F5BEB;
  font-size:21px;font-weight:650;
}
.sg-draft-alert-copy{min-width:0}
.sg-draft-alert-eyebrow{display:block;font-size:11px;line-height:1.25;color:#3055B3;margin-bottom:5px}
.sg-draft-alert h2{font-size:18px;line-height:1.35;font-weight:700;margin:0 0 5px;letter-spacing:-.015em;color:#17283E}
.sg-draft-alert p{font-size:13px;line-height:1.5;font-weight:400;margin:0;color:#576B83;max-width:690px}
.sg-draft-alert-action{
  flex-shrink:0;min-height:44px;
  padding:11px 17px;
  font-size:13px;white-space:nowrap;
  text-decoration:none;
}
.sg-draft-alert-action span{font-size:16px}
@media(max-width:780px){
  .sg-draft-alert{grid-template-columns:38px minmax(0,1fr);gap:9px 12px;padding:16px}
  .sg-draft-alert-icon{width:38px;height:38px;grid-row:1}
  .sg-draft-alert-copy{grid-column:2;grid-row:1}
  .sg-draft-alert-action{grid-column:1/-1;width:100%;margin-top:5px}
}
@media(max-width:375px){
  .sg-draft-alert{padding:14px;gap:10px}
  .sg-draft-alert h2{font-size:17px}
  .sg-draft-alert p{font-size:12px}
  .sg-draft-alert-action{font-size:13px}
}
@media(max-width:1180px) and (min-width:921px){
 .studio-layout{grid-template-columns:218px minmax(0,1fr)}
 .sidebar .sg-official-brand-name{font-size:21px}
 .sg-dashboard-feed{grid-template-columns:repeat(2,minmax(0,1fr))}
 .sg-dashboard-feed .sg-surface{padding:16px}
 .main,.route-main{padding:21px 19px 50px}
 .sg-mini-date{min-width:76px;font-size:11px;padding:8px 6px}
 .sg-mini-row{gap:8px}
 .sg-workflow-banner{padding:21px}
}
@media(max-width:920px){
 .studio-layout{display:flex;flex-direction:column;min-height:100vh}
 .sidebar{display:none!important}
 .sg-mobile-shell-head{display:flex;align-items:center;justify-content:space-between;gap:12px;
   padding:9px 15px;background:white;border-bottom:1px solid #E8ECF2;min-height:62px}
 .sg-mobile-shell-head .sg-official-brand-icon{width:38px;height:38px;flex-basis:38px}
 .sg-mobile-shell-head .sg-official-brand-name{font-size:21px}
 .sg-mobile-project{display:flex;align-items:center;gap:5px;border:1px solid #E4E9F0;
   padding:8px 10px;border-radius:10px;font-size:12px;font-weight:680;color:#42586E;
   max-width:43%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .sg-mobile-project span{font-size:17px;color:#536A90}
 .mobile-nav{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;
   position:sticky!important;top:0!important;right:auto!important;left:auto!important;bottom:auto!important;
   width:100%;min-height:55px;z-index:45;padding:6px 10px;
   background:rgba(255,255,255,.98);border-top:0;border-bottom:1px solid #E5EAF2;
   border-radius:0;box-shadow:none;backdrop-filter:blur(12px)}
 .mobile-nav :is(a,button){display:flex;align-items:center;justify-content:center;gap:6px;flex-direction:row;
   text-align:center;font-weight:700;color:#5A6A7F;font-size:12px;min-width:0;min-height:43px;
   border:0;background:transparent;font-family:inherit;cursor:pointer;border-radius:10px;padding:7px 3px;white-space:nowrap}
 .mobile-nav :is(a,button).active{color:#2F5BEB;background:#EDF2FF}
 .mobile-nav .m-ico{font-size:17px;line-height:1}
 .main,.route-main{padding:18px 15px 38px;min-width:0;width:100%;flex:1}
 .route-mobile-brand{display:none!important}
 .mobile-only-dashboard{display:none!important}
 .sg-dashboard-feed{grid-template-columns:repeat(2,minmax(0,1fr))}
 .sg-workflow-banner{margin-top:0}
}
@media(max-width:680px){
 .sg-dashboard-feed{grid-template-columns:1fr;gap:13px}
 .sg-dashboard-feed .sg-surface{padding:17px 16px}
 .sg-workflow-banner{grid-template-columns:1fr;gap:16px;padding:19px}
 .sg-workflow-banner .sg-hero-actions{display:grid;grid-template-columns:1fr;gap:9px;width:100%}
 .sg-workflow-banner .sg-hero-actions .btn{width:100%}
 .sg-priority-strip{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
 .sg-priority-item:first-child{display:none}
 .sg-priority-item{padding:16px 13px;min-height:114px}
 .sg-priority-item span{font-size:12px}
 .sg-priority-item strong{font-size:24px}
 .sg-quick-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;overflow:visible}
 .sg-quick-links a{display:flex;justify-content:center;white-space:normal;
   line-height:1.3;min-height:46px;font-size:12px;padding:9px 8px}
 .sg-dashboard-feed .sg-section-head h2{font-size:18px}
 .sg-mini-row{min-height:69px}
 .sg-mini-date{min-width:81px;padding:8px 6px}
 .topbar,.route-page-head{margin:0 0 19px}
 .sg-surface{border-radius:15px}
 .sg-legacy-details{margin-bottom:8px}
 .sg-mobile-project{font-size:11px;max-width:42%}
 .route-page-head h1{font-size:25px}
}
@media(max-width:375px){
 .mobile-nav :is(a,button){font-size:11px;gap:4px}
 .mobile-nav .m-ico{font-size:16px}
 .sg-mobile-shell-head{padding:8px 11px}
 .sg-workflow-banner{padding:17px 15px}
 .main,.route-main{padding:15px 11px 33px}
 .sg-dashboard-feed .sg-surface{padding:17px 14px}
 .sg-priority-item{padding:13px 10px}
}
"""
