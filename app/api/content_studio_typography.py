"""Consistent typography and spacing across ALL Growth Studio work areas.

This is the final, narrow cascade after the official ServGo identity layer.
Deliberately uses local/system fonts; no external font request or fake logo.
"""
CSS = r"""
/* v8: comprehensive ServGo typography + contrast system */
:root{
  --sg-font-ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Arial,sans-serif;
  --sg-font-brand:Montserrat,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  --sg-fs-body:15px;
  --sg-fs-small:13px;
  --sg-fs-caption:12px;
  --sg-fs-title:21px;
  --sg-fs-section:18px;
  --sg-text-strong:#192A3D;
  --sg-text-secondary:#4F6175;
}
body,.studio-page,.studio-layout,.main,.route-main,
.studio-page button,.studio-page select,.studio-page textarea,.studio-page input{
  font-family:var(--sg-font-ui);
}
body,.studio-page{font-size:var(--sg-fs-body);font-weight:400;line-height:1.55;color:var(--sg-text-strong)}
.sg-official-brand-name{font-family:var(--sg-font-brand);font-weight:800}
h1,h2,h3,h4,h5,h6{font-family:var(--sg-font-ui);color:var(--sg-text-strong);line-height:1.32}
h1,.topbar h1,.route-page-head h1{font-size:clamp(25px,2.25vw,32px);font-weight:700;letter-spacing:-.024em}
h2,.panel-title h2,.sg-surface h2,.sg-section-head h2,.sg-dashboard-feed .sg-section-head h2{
  font-size:var(--sg-fs-title);font-weight:700;letter-spacing:-.015em;line-height:1.36;
}
h3,.sg-review-item h3,.sg-action-tile h3,.sg-guide h3,.material-detail-body h3{
  font-size:16px;font-weight:650;letter-spacing:-.012em;line-height:1.43;
}
b,strong,.panel-title strong,.calendar-row b,.material-card b{font-weight:650}
p,.subtitle,.panel-sub,.sg-section-head p,.sg-surface p{font-size:var(--sg-fs-small);line-height:1.6;color:var(--sg-text-secondary);font-weight:400}
small,.sg-field small,.sg-footnote,.sg-field-help{font-size:var(--sg-fs-caption);line-height:1.5;color:var(--sg-text-secondary)}
.eyebrow,.sg-overline,.nav-label{font-size:11px;line-height:1.4;font-weight:650;letter-spacing:.075em;color:#516781}
.sg-steps b,.sg-choice-copy b,.sg-field>span,.sg-guide h3{font-weight:650}
.sg-steps span,.sg-choice-copy small,.sg-form-hint,.sg-guide p{
  font-size:var(--sg-fs-small);line-height:1.55;color:var(--sg-text-secondary);
}
.btn,.btn.primary,.btn.secondary,.btn.ghost,
.sg-primary-action,.sg-draft-alert-action{
  font-size:14px;line-height:1.35;font-weight:600;letter-spacing:0;
  min-height:44px;white-space:normal;text-wrap:balance;
}
.btn.small,.sg-inline-actions .btn.small{font-size:12px;line-height:1.45;min-height:38px;font-weight:600}
.studio-page input,.studio-page select,.studio-page textarea{
  font-size:15px;line-height:1.45;font-weight:400;color:#1D3044;
}
.sg-field>span,.sg-field>label,.library-controls label,.material-toolbar label{
  font-size:13px;font-weight:600;line-height:1.48;color:#334B62;
}
.sg-field>span em{font-size:12px;font-weight:400}
.sg-field input,.sg-field select,.sg-field textarea,.search input{
  font-size:15px;min-height:44px;line-height:1.5;
}
.sg-field input::placeholder,.sg-field textarea::placeholder,.search input::placeholder{color:#687B91;opacity:1;font-weight:400}
.nav-label{font-size:11px}
.sidebar .nav a,.sidebar .nav a.active{font-size:13px;line-height:1.45}
.sidebar .nav a{font-weight:500}
.sidebar .nav a.active{font-weight:650}
.sidebar-promo b{font-size:13px;font-weight:650;line-height:1.4}
.sidebar-promo p{font-size:12px;line-height:1.55}
.sidebar-foot,.sg-brand-caption{font-size:11px;font-weight:400}
.sg-workflow-banner h2{font-size:clamp(22px,2vw,27px);font-weight:700;line-height:1.32;letter-spacing:-.015em}
.sg-workflow-banner p{font-size:14px;line-height:1.58}
.sg-priority-item strong,.kpi strong{font-size:26px;font-weight:700;line-height:1.2}
.sg-priority-item span,.kpi small{font-size:12px;line-height:1.5;font-weight:400}
.sg-priority-item em,.sg-mini-link,.sg-quick-links a{font-size:12px;font-weight:600}
.sg-count,.chip,.status{font-size:12px;font-weight:600;line-height:1.4}
.sg-count{font-size:12px;padding:7px 11px}
.sg-mini-row b{font-size:14px;line-height:1.45;font-weight:600}
.sg-mini-row small{font-size:12px;line-height:1.45;color:#53667D}
.sg-mini-date{font-size:12px;font-weight:600;line-height:1.35}
.sg-mini-list{gap:0}
.sg-dashboard-feed .sg-section-head h2{font-size:19px}
.sg-dashboard-feed .sg-section-head p{font-size:13px}
.sg-draft-alert h2{font-size:19px;font-weight:700;line-height:1.4}
.sg-draft-alert p{font-size:14px;line-height:1.55}
.sg-draft-alert-eyebrow{font-size:11px;font-weight:650}
.sg-legacy-details>summary{font-size:13px;line-height:1.55;font-weight:600}
.panel-sub,.route-summary p{font-size:13px;line-height:1.55}
.panel-title h2{font-size:18px}
.panel-head{align-items:flex-start;gap:12px}
.panel-head .btn{flex-shrink:0}
.tab,.sg-filter{font-size:13px;line-height:1.4;font-weight:600;min-height:38px}
.material-body h4,.material-card h4{font-size:14px;line-height:1.42;font-weight:650}
.material-body p,.material-card p{font-size:12px;line-height:1.52;color:#56697E}
.material-kind,.sg-tag,.sg-channel,.sg-warning{font-size:11px;line-height:1.4;font-weight:600}
.material-toolbar .tab{min-height:38px}
.package-body h3,.package-card h3{font-size:15px;line-height:1.45;font-weight:650}
.package-objective{font-size:13px;line-height:1.5;color:#50647A}
.package-foot,.package-slot-row small{font-size:12px;line-height:1.45;color:#51657B}
.package-slot-row b,.route-list-row b{font-size:14px;line-height:1.45;font-weight:650}
.slot-time,.calendar-time,.calendar-prepare{font-size:12px;line-height:1.45;font-weight:600}
.slot-title,.calendar-topic b,.calendar-platform b{font-size:13px;line-height:1.5;font-weight:600}
.slot-meta,.calendar-topic small{font-size:12px;line-height:1.45;color:#51657B}
.platform-mini{font-size:11px;font-weight:600}
.calendar-row{min-width:0}
.calendar-row>div{min-width:0}
.calendar-topic b,.calendar-topic small{white-space:normal;overflow-wrap:anywhere}
.calendar-row .status{white-space:normal;overflow-wrap:anywhere}
.search input{min-height:44px}
.sg-review-item h3{font-size:17px;font-weight:650}
.sg-review-item p{font-size:14px;line-height:1.6}
.sg-review-footer>span,.sg-channel-status{font-size:12px;line-height:1.45}
.sg-review-item .btn{font-size:13px}
.sg-channel-line b,.sg-publication-row b{font-size:14px;line-height:1.42;font-weight:650}
.sg-channel-line small,.sg-publication-row small{font-size:12px;line-height:1.48}
.sg-storyboard-card h3{font-size:14px;line-height:1.4;font-weight:650}
.sg-storyboard-card p,.sg-storyboard-card details p{font-size:12px;line-height:1.6}
.sg-storyboard-counter,.sg-storyboard-card summary{font-size:12px;font-weight:600;line-height:1.5}
.sg-post-preview,.article-text,.preview-copy,.material-detail-body .article-text{
  font-size:15px;line-height:1.75;font-weight:400;color:#243950;
}
.sg-native-post-label{font-size:12px;font-weight:600}
.dialog-head h3{font-size:18px;font-weight:650}
.dialog-head .status,.dialog-actions .btn{font-size:13px}
.story-shot b,.story-shot p,.story-shot time,.story-prompt{font-size:12px;line-height:1.55}
.sg-route-utility a,.sg-pagination span{font-size:12px;line-height:1.4;font-weight:600}
.back-link{font-size:13px;line-height:1.45;font-weight:600}
.sg-draft-alert,.sg-surface,.panel{overflow-wrap:break-word}
@media(max-width:920px){
  .mobile-nav a{font-size:12px;line-height:1.35;font-weight:600}
  .sg-mobile-project{font-size:12px;font-weight:600}
  .sg-mobile-shell-head .sg-official-brand-name{font-size:22px}
  .sg-dashboard-feed .sg-section-head h2{font-size:19px}
  .sg-workflow-banner p{font-size:14px}
  .sg-draft-alert p{font-size:13px}
  .sg-form-hint{font-size:13px}
  .sg-mini-row b{font-size:14px}
  .sg-mini-row small{font-size:12px}
  .sg-mini-date{font-size:12px}
  .sg-priority-item span{font-size:12px}
  .sg-priority-item em{font-size:12px}
  .sg-quick-links a{font-size:13px;line-height:1.45}
  .calendar-time,.calendar-topic b,.calendar-topic small{font-size:12px}
  .status{font-size:12px}
  .sg-review-item .btn{min-height:44px}
}
@media(max-width:620px){
  .sg-draft-alert h2{font-size:18px}
  .sg-draft-alert p{font-size:13px}
  .panel-head{flex-wrap:wrap}
  .sg-dashboard-feed .sg-section-head h2{font-size:18px}
  .sg-dashboard-feed .sg-section-head p{font-size:13px}
  .sg-choice-copy b{font-size:13px}
  .sg-choice-copy small{font-size:12px}
  .sg-field>span{font-size:13px}
  .sg-workflow-banner h2{font-size:23px}
  .sg-mini-row b{font-size:14px}
  .sg-mini-row small{font-size:12px}
  .material-card h4,.material-body h4{font-size:14px}
  .material-body p{font-size:12px;white-space:normal}
}
@media(max-width:375px){
  .mobile-nav a{font-size:11px}
  .sg-draft-alert h2{font-size:17px}
  .sg-workflow-banner h2{font-size:21px}
  .sg-mini-row b{font-size:13px}
}
"""
