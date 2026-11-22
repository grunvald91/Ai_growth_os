"""Focused task/notification UI layer for the ServGo marketing workspace.

Loaded after the existing ServGo brand typography; avoid overriding the brand.
"""
CSS = r"""
/* v9: one clear route from idea to work-in-progress to result */
.sg-global-status{display:flex;align-items:center;justify-content:flex-end;min-height:44px;margin:0 0 12px;position:relative;z-index:55}
.sg-notifications{position:relative;max-width:100%}
.sg-notifications>summary{display:flex;align-items:center;gap:9px;list-style:none;cursor:pointer;padding:10px 14px;
  background:white;border:1px solid #DEE5EF;border-radius:12px;color:#263B55;font-weight:600;min-height:44px}
.sg-notifications>summary::-webkit-details-marker{display:none}
.sg-notifications>summary:hover,.sg-notifications[open]>summary{background:#F0F4FF;border-color:#C6D6FF}
.sg-notifications>summary:focus-visible{outline:3px solid #8BA4FB;outline-offset:2px}
.sg-notifications>summary>span:first-child{font-size:21px;line-height:1;color:#2F5BEB}
.sg-notify-label{font-size:13px;line-height:1.3;font-weight:600}
.sg-notify-count{display:inline-flex;align-items:center;justify-content:center;
  font-size:12px;min-width:22px;height:22px;border-radius:12px;padding:0 5px;
  background:#2F5BEB;color:#fff}
.sg-notify-count[hidden]{display:none}
.sg-notify-popover{position:absolute;right:0;top:calc(100% + 7px);width:min(390px,calc(100vw - 32px));
  max-height:420px;overflow:auto;z-index:100;background:#fff;
  border:1px solid #E0E6EF;border-radius:15px;padding:17px;
  box-shadow:0 18px 35px rgba(13,27,42,.15)}
.sg-notify-popover>strong{display:block;font-size:15px;font-weight:700;margin:0 0 12px}
.sg-notify-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 12px}
.sg-notify-head strong{font-size:15px;font-weight:700}
.sg-notify-read{border:0;background:#EAF0FF;color:#244DCE;border-radius:9px;padding:7px 11px;font-size:12px;font-weight:600;cursor:pointer}
.sg-notify-read[hidden]{display:none}
.sg-notify-entry{display:block;line-height:1.45;padding:11px 5px;color:#253B54;
  border-top:1px solid #EBEEF3;font-size:13px;text-decoration:none}
.sg-notify-entry:hover{background:#F4F7FD;color:#1F4ED1}
.sg-notify-entry::before{content:"●";color:#2F5BEB;font-size:8px;margin-right:9px;vertical-align:middle}
.sg-notify-entry.sg-notify-error::before{color:#B95842}
.sg-notify-empty{color:#587085;font-size:13px;line-height:1.55;margin:10px 0}
.sg-notify-all{display:block;text-align:center;border-top:1px solid #E5EAF0;
  padding:12px 0 0;color:#2F5BEB;font-weight:600;font-size:13px}
.sg-overview-work{margin:0 0 17px}
.sg-work-tabs{display:flex;flex-wrap:wrap;gap:8px;padding-bottom:14px;border-bottom:1px solid #E5EAF0;margin-bottom:14px}
.sg-work-tabs a{display:inline-flex;align-items:center;min-height:42px;padding:10px 14px;color:#52667B;
  border:1px solid transparent;border-radius:11px;font-size:13px;font-weight:600;text-decoration:none}
.sg-work-tabs a.active{background:#EAF0FF;color:#244DCE;border-color:#D8E3FF}
.sg-work-item{display:flex;align-items:center;min-width:0;gap:14px;padding:16px 10px;
  border-bottom:1px solid #E8EDF2;text-decoration:none;color:inherit}
.sg-work-item:last-child{border-bottom:0}
.sg-work-item:hover{background:#F8FAFD}
.sg-work-item-main{min-width:0;flex:1}
.sg-work-item-main b{display:block;font-size:15px;font-weight:650;color:#263D56;
  line-height:1.42;overflow-wrap:anywhere}
.sg-work-item-main p{font-size:13px;line-height:1.48;color:#5B6E83;margin:5px 0 0}
.sg-work-arrow{color:#8092A6;font-size:18px}
.sg-work-status{display:inline-flex;align-items:center;justify-content:center;
  border-radius:9px;padding:7px 11px;font-size:12px;font-weight:650;line-height:1.35;
  flex-shrink:0;max-width:100%;white-space:normal;text-align:center}
.sg-work-status.sg-work-queued{background:#EDF2FA;color:#385A83}
.sg-work-status.sg-work-running{background:#EAF1FF;color:#204EAE}
.sg-work-status.sg-work-review{background:#FFF3E1;color:#87551B}
.sg-work-status.sg-work-done{background:#E8F5EF;color:#227351}
.sg-work-status.sg-work-error{background:#FFEEEE;color:#A83D4A}
.sg-work-empty{text-align:center;padding:28px 14px}
.sg-work-empty h3{font-size:17px;color:#243D53;margin:0 0 6px}
.sg-work-empty p{font-size:14px;margin:0 0 16px}
.sg-work-detail>h2{font-size:23px;font-weight:700;margin:15px 0 8px;overflow-wrap:anywhere}
.sg-work-detail>p{font-size:14px;line-height:1.6}
.sg-work-steps,.sg-work-results{border-top:1px solid var(--s-line,#E5EAF1);margin:21px 0 0;padding:17px 0 0}
.sg-work-steps h3,.sg-work-results h3{font-size:17px;margin:0 0 10px;font-weight:700}
.sg-work-steps ol{list-style:none;margin:0;padding:0;display:grid;gap:5px}
.sg-work-steps li{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:11px 12px;background:var(--s-surface-2,#F7F9FC);border:1px solid var(--s-line,#E8EDF2);border-radius:10px;font-size:13px;color:var(--s-text,#344D64)}
.sg-work-steps li span{min-width:0;overflow-wrap:anywhere}
.sg-work-steps li b{font-weight:600;white-space:nowrap;color:var(--s-text,#335D92)}
.sg-work-result{display:flex;justify-content:space-between;align-items:center;gap:12px;
  border:1px solid var(--s-line,#E1E7F0);border-radius:11px;padding:12px 14px;margin:9px 0;
  color:var(--s-accent,#234D9E);text-decoration:none}
.sg-work-result>div{display:grid;gap:5px;min-width:0}
.sg-work-result span{font-size:12px;font-weight:600}
.sg-work-result b{font-size:14px;line-height:1.45;font-weight:650;color:#283E55;overflow-wrap:anywhere}
.sg-work-result>span{white-space:nowrap}
.sg-work-refresh{border-radius:10px;background:#EEF4FF;border:1px solid #D8E6FA;
  padding:12px 14px;color:#365B8A!important;font-size:13px!important}
.sg-create-work-link{padding:10px 0 4px;font-size:13px}
.sg-create-work-link a{color:#2F5BEB;font-weight:600}
.sg-side-extras{border-top:1px solid #E9EEF2;margin-top:15px;padding:9px 0 0}
.sg-side-extras>summary{font-size:12px;line-height:1.5;font-weight:600;color:#5B6C80;
  cursor:pointer;padding:12px 9px;border-radius:9px;list-style:none}
.sg-side-extras>summary::before{content:"▸";margin-right:8px;color:#3767DE}
.sg-side-extras[open]>summary::before{content:"▾"}
.sg-side-extras>summary::-webkit-details-marker{display:none}
.sg-side-extras>summary:hover{background:#F1F5FB}
.sg-side-extras .nav{padding:6px 0}
.sg-side-extras .nav a{font-size:12px}
.sg-work-list{min-height:90px}
.sg-legacy-details{margin-top:10px}
.sg-dashboard-extra{margin-top:8px;background:#FAFBFD}
.sg-dashboard-extra>summary{font-size:14px;font-weight:600;padding:12px}
.sg-dashboard-extra .sg-priority-strip{margin:16px 0}
.sg-dashboard-extra .sg-quick-links{margin:0 0 16px}
.sg-dashboard-extra .sg-dashboard-feed{margin:13px 0}
.sg-rendered-article{max-width:790px;margin:0 auto;color:#23384D;font-size:16px;line-height:1.76;overflow-wrap:anywhere}
.sg-rendered-article h2{font-size:24px;margin:26px 0 12px;line-height:1.3}
.sg-rendered-article h3{font-size:20px;margin:22px 0 11px;line-height:1.38}
.sg-rendered-article h4{font-size:18px;margin:19px 0 9px;line-height:1.42}
.sg-rendered-article p{font-size:16px;line-height:1.76;color:#263A51;margin:11px 0}
.sg-rendered-article ul,.sg-rendered-article ol{padding-left:26px;margin:13px 0 19px}
.sg-rendered-article li{font-size:16px;margin:7px 0;line-height:1.7}
.sg-rendered-article blockquote{border-left:3px solid #8BA8EE;padding:9px 16px;background:#F3F6FF;border-radius:0 9px 9px 0;color:#405B77}
.sg-rendered-article code{background:#F0F3F7;padding:2px 4px;border-radius:5px;font-size:14px}
.sg-rendered-article pre{max-width:100%;overflow:auto;padding:14px;border-radius:11px;background:#F2F5F8}
.sg-rendered-article a{color:#2556DF;text-decoration:underline}
.sg-video-file video{width:100%;max-width:410px;max-height:650px;display:block;border-radius:14px;background:#101820;margin:17px auto}
.sg-video-file .sg-inline-actions{gap:9px;margin:20px 0}

.sg-animator-top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;
  align-items:center;margin:0 0 17px;padding:22px 24px}
.sg-animator-top h2{font-size:20px;margin:6px 0}
.sg-animator-top p{max-width:620px;margin:6px 0}
.sg-animator-top small{display:block;color:#51657A;font-size:12px;line-height:1.45;margin-top:6px}
.sg-animator-top .btn{min-width:225px;max-width:260px}
@media(max-width:660px){
 .sg-animator-top{grid-template-columns:1fr;gap:12px;padding:18px 16px}
 .sg-animator-top h2{font-size:18px}
 .sg-animator-top .btn{max-width:none;width:100%;margin:0}
}
.sg-animator-entry{border:1px solid #DDE6FB;background:#F8FAFF}
.sg-animator-entry h2{font-size:18px;line-height:1.35;margin:8px 0}
.sg-animator-entry p{font-size:13px;line-height:1.55;margin:9px 0}
.sg-animator-entry .btn{display:inline-flex;width:100%;align-items:center;justify-content:center;margin-top:12px}
.sg-inline-more{position:relative;align-self:center}
.sg-inline-more>summary{list-style:none;cursor:pointer;border:1px solid #DFE7F1;
  min-height:42px;border-radius:10px;padding:10px 12px;font-size:12px;font-weight:600;color:#34527B;background:#fff}
.sg-inline-more>summary::-webkit-details-marker{display:none}
.sg-inline-more>summary:hover{background:#F2F6FF}
.sg-inline-more>div{position:absolute;top:calc(100% + 7px);right:0;z-index:80;
  width:min(260px,calc(100vw - 24px));border-radius:12px;display:grid;gap:3px;
  padding:9px;background:#fff;border:1px solid #E2E8F2;
  box-shadow:0 15px 30px rgba(13,27,42,.12)}
.sg-inline-more>div>a{display:block;padding:9px 11px;text-decoration:none;
  font-size:13px;color:#354F6B;border-radius:8px;min-height:38px}
.sg-inline-more>div>a:hover{background:#EEF3FF;color:#214AC3}
@media(max-width:920px){
 .sg-global-status{margin:0 0 10px;min-height:42px}
 .sg-notifications>summary{padding:8px 13px}
 .sg-work-item{padding:14px 2px;gap:8px;flex-wrap:wrap}
 .sg-work-item-main{flex:1 1 calc(100% - 20px)}
 .sg-work-status{margin-top:5px}
 .sg-work-arrow{margin:3px 0 0 auto}
 .sg-work-item-main b{font-size:14px}
 .sg-work-item-main p{font-size:13px}
 .sg-work-tabs{gap:3px}
 .sg-work-tabs a{padding:9px 10px;font-size:12px}
 .sg-work-detail{padding:19px 16px}
 .sg-work-detail>h2{font-size:20px}
 .sg-notify-popover{width:min(375px,calc(100vw - 25px))}
 .sg-work-result{flex-wrap:wrap}
}

/* Scoped SEO task progress: avoid a fake article / publish CTA. */
.sg-seo-task-detail .sg-work-results ul{list-style:disc;padding-left:22px;
  margin:9px 0;display:grid;gap:8px}
.sg-seo-task-detail .sg-work-results li{line-height:1.5;
  color:var(--s-text,#263D56);overflow-wrap:anywhere}
.sg-seo-task-stage{margin:10px 0;font-size:14px;
  color:var(--s-text-muted,#587085)}
.sg-seo-task-muted{margin:15px 0;color:var(--s-text-muted,#587085)}
.sg-seo-task-error{padding:11px 13px;margin:12px 0;border:1px solid var(--s-line,#DFE7F0);
  border-radius:10px;color:var(--s-text,#263D56);background:var(--s-surface-2,#F6F8FB)}
.sg-seo-task-actions{display:flex;flex-wrap:wrap;gap:12px;
  align-items:center;margin-top:20px}
.sg-seo-task-actions .btn,.sg-seo-task-actions .sg-mini-link{
  min-height:44px;display:inline-flex;align-items:center;justify-content:center}
@media(max-width:520px){.sg-seo-task-actions .btn{width:100%}}

/* Finished work: keep the result above technical history. */
.sg-work-steps-collapsible>summary{min-height:44px;display:flex;align-items:center;
  cursor:pointer;font-size:17px;font-weight:700;color:var(--s-text,var(--text,#263D56))}
.sg-work-steps-collapsible>summary:focus-visible{outline:2px solid var(--s-accent,var(--blue,#2F5BEB));
  outline-offset:3px;border-radius:7px}
.sg-work-steps-collapsible[open]>ol{margin-top:10px}

/* Filter and pagination are part of the document, not extra fixed bars. */
.sg-work-board .sg-work-search{display:grid;grid-template-columns:minmax(0,1fr) minmax(112px,auto) minmax(122px,auto) auto;
  align-items:center;gap:8px;margin:0 0 13px;min-width:0}
.sg-work-board .sg-work-search-label{position:absolute;width:1px;height:1px;
  padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.sg-work-board .sg-work-search input,.sg-work-board .sg-work-search select{
  box-sizing:border-box;min-height:44px;min-width:0;width:100%;font:inherit;
  padding:10px 12px;border-radius:10px;border:1px solid var(--s-line,#DDE5EE);
  background:var(--s-surface,#fff);color:var(--s-text,#24364F)}
.sg-work-board .sg-work-search input:focus-visible,.sg-work-board .sg-work-search select:focus-visible{
  outline:2px solid var(--s-accent,#2F5BEB);outline-offset:2px}
.sg-work-board .sg-work-search .btn{box-sizing:border-box;min-height:44px;white-space:nowrap}
.sg-work-board .sg-work-tab-count{font-variant-numeric:tabular-nums;opacity:.72;
  font-size:11px;margin-left:4px}
.sg-work-board .sg-work-pagination{display:flex;flex-wrap:wrap;align-items:center;
  justify-content:space-between;gap:10px;margin-top:16px;padding:12px 0 0;
  border-top:1px solid var(--s-line,#DFE7F0)}
.sg-work-board .sg-work-page-summary{font-size:12px;color:var(--s-text-muted,#587085)}
.sg-work-board .sg-work-page-links{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.sg-work-board .sg-work-page-links a,.sg-work-board .sg-work-update{display:inline-flex;
  box-sizing:border-box;align-items:center;justify-content:center;min-height:44px;
  border-radius:10px;padding:9px 12px;border:1px solid var(--s-line,#DFE7F0);
  text-decoration:none;font-size:13px;color:var(--s-text,#263D56);
  background:var(--s-surface,#fff);cursor:pointer}
.sg-work-board .sg-work-page-links a:focus-visible,.sg-work-board .sg-work-update:focus-visible{
  outline:2px solid var(--s-accent,#2F5BEB);outline-offset:2px}
.sg-work-board .sg-work-page-links>span{font-size:12px;white-space:nowrap;
  color:var(--s-text-muted,#587085)}
.sg-work-board .sg-work-update{margin-bottom:10px;width:100%;font-weight:600}
.sg-work-board .sg-work-update[hidden]{display:none}
@media(max-width:880px){
  .sg-work-board .sg-work-search{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .sg-work-board .sg-work-search input{grid-column:1 / -1}
  .sg-work-board .sg-work-search select{min-width:0}
  .sg-work-board .sg-work-search .btn{grid-column:1 / -1}
}
@media(max-width:520px){
  .sg-work-board .sg-work-pagination{gap:7px}
}

/* Context-only desktop rail. The primary card remains an ordinary link. */
.sg-work-board .sg-has-task-peek{position:relative}
.sg-work-board .sg-has-task-peek .sg-work-item-foot{
  min-height:44px;padding-right:101px}
.sg-work-board .sg-task-peek-action{
  box-sizing:border-box;position:absolute;right:12px;bottom:12px;
  display:inline-flex;align-items:center;justify-content:center;
  min-height:44px;min-width:86px;padding:9px 10px;border:1px solid var(--s-line,#DDE5EE);
  border-radius:10px;background:var(--s-surface-2,#F4F7FB);
  color:var(--s-text,#263D56);font-family:inherit;font-size:12px;
  font-weight:600;line-height:1.3;cursor:pointer}
.sg-work-board .sg-task-peek-action[aria-pressed="true"]{
  border-color:var(--s-accent,#2F5BEB);color:var(--s-accent,#2F5BEB)}
.sg-work-board .sg-task-peek-action:focus-visible{
  outline:2px solid var(--s-accent,#2F5BEB);outline-offset:2px}
.sg-work-preview{min-width:0;padding:16px 14px;
  border:1px solid var(--s-line,#DFE7F0);border-radius:15px;
  background:var(--s-surface,#fff)}
.sg-work-preview-announcement{position:absolute;width:1px;height:1px;
  margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);
  white-space:nowrap;border:0}
.sg-work-preview-hint{color:var(--s-text-muted,#587085);
  font-size:13px;line-height:1.65;margin:0}
.sg-work-preview-detail{display:grid;gap:10px;min-width:0}
.sg-work-preview-detail[hidden],.sg-work-preview-hint[hidden]{display:none}
.sg-work-preview-kind{font-size:12px;font-weight:650;color:var(--s-text-muted,#587085)}
.sg-work-preview-detail h3{font-size:16px;font-weight:700;line-height:1.45;
  overflow-wrap:anywhere;margin:0;color:var(--s-text,#263D56)}
.sg-work-preview-status{font-size:12px;font-weight:650;
  color:var(--s-accent,#2F5BEB)}
.sg-work-preview-detail p{font-size:13px;line-height:1.6;
  margin:0;color:var(--s-text-muted,#587085);overflow-wrap:anywhere}
.sg-work-preview-detail .sg-work-preview-date{font-size:11px}
.sg-work-preview-detail .btn{display:inline-flex;align-items:center;
  justify-content:center;min-height:44px;margin-top:6px}
@media(max-width:1080px){
  .sg-work-board .sg-task-peek-action{display:none!important}
  .sg-work-board .sg-has-task-peek .sg-work-item-foot{min-height:0;padding-right:0}
}

/* Tasks page: compact, content-first cards, adaptive without a second toolbar. */
.sg-work-board .sg-work-toolbar{display:flex;justify-content:space-between;
  gap:14px;align-items:center;margin-bottom:12px}
.sg-work-board .sg-work-toolbar p{max-width:630px;margin:0;line-height:1.5;
  color:var(--s-text-muted,var(--muted,#587085));font-size:13px}
.sg-work-board .sg-work-toolbar .btn{flex-shrink:0;min-height:44px;display:inline-flex;
  align-items:center;justify-content:center}
.sg-work-board .sg-work-tabs{display:flex;flex-wrap:nowrap;gap:6px;
  max-width:100%;overflow-x:auto;overscroll-behavior-inline:contain;
  scrollbar-width:thin;scroll-padding-inline:12px;
  padding:0 0 12px;margin:0 0 14px}
.sg-work-board .sg-work-tabs a{flex:0 0 auto;white-space:nowrap;min-height:44px;
  padding:11px 14px;border-radius:10px;border:1px solid transparent;
  text-decoration:none}
.sg-work-board .sg-work-tabs a.active{border-color:var(--s-line,var(--line,#D8E3FF));
  border-bottom:2px solid var(--s-accent,var(--blue,#2F5BEB))}
.sg-work-board .sg-work-list{display:grid;gap:9px;min-width:0}
.sg-work-board .sg-resource-widget{min-width:0;overflow:visible;
  border:1px solid var(--s-line,var(--line,#DFE7F0));
  border-radius:14px;background:var(--s-surface,var(--card,#fff))}
.sg-work-board .sg-work-item{display:block;box-sizing:border-box;
  min-height:104px;padding:14px 16px;border:0;border-radius:14px;
  text-decoration:none;color:var(--s-text,var(--text,#263D56))}
.sg-work-board .sg-work-item:hover{background:var(--s-surface-2,var(--tint,#F6F9FC))}
.sg-work-board .sg-work-item:focus-visible{outline:3px solid var(--s-accent,var(--blue,#2F5BEB));
  outline-offset:2px}
.sg-work-board .sg-work-item-head,.sg-work-board .sg-work-item-foot{display:flex;
  align-items:center;justify-content:space-between;gap:12px;min-width:0}
.sg-work-board .sg-work-kind{font-size:12px;line-height:1.4;
  color:var(--s-text-muted,var(--muted,#587085));font-weight:600}
.sg-work-board .sg-work-item-main{display:grid;gap:4px;margin-top:9px;min-width:0}
.sg-work-board .sg-work-item-main b{font-size:15px;line-height:1.42;
  font-weight:650;color:var(--s-text,var(--text,#263D56));overflow-wrap:break-word}
.sg-work-board .sg-work-summary{font-size:13px;line-height:1.5;
  color:var(--s-text-muted,var(--muted,#587085));overflow-wrap:break-word}
.sg-work-board .sg-work-item-foot{margin-top:11px;font-size:12px;
  color:var(--s-text-muted,var(--muted,#587085))}
.sg-work-board .sg-work-open{color:var(--s-accent,var(--blue,#2F5BEB));font-weight:650;
  white-space:nowrap;margin-left:auto}
.sg-work-board .sg-work-status{margin:0;max-width:60%;flex-shrink:0}
@media(max-width:660px){
  .sg-work-board .sg-work-toolbar p{font-size:12px}
  .sg-work-board .sg-work-toolbar .btn{padding-inline:13px}
  .sg-work-board .sg-work-item{padding:12px 13px}
  .sg-work-board .sg-work-item-head{align-items:flex-start}
  .sg-work-board .sg-work-status{font-size:11px;max-width:64%}
  .sg-work-board .sg-work-item-main b{font-size:14px}
  .sg-work-board .sg-work-tabs{margin-inline:-2px}
}
@media(max-width:360px){
  /* 320px phones: do not squeeze two long filters or a primary CTA
     next to descriptive text. These controls stay 44px minimum. */
  .sg-work-board .sg-work-toolbar{flex-direction:column;align-items:stretch;gap:9px}
  .sg-work-board .sg-work-toolbar .btn{width:100%}
  .sg-work-board .sg-work-search{grid-template-columns:minmax(0,1fr)}
  .sg-work-board .sg-work-search :is(input,select,.btn){grid-column:1/-1}
  .sg-work-board .sg-work-item-head{flex-wrap:wrap;justify-content:flex-start;gap:7px}
  .sg-work-board .sg-work-kind{min-width:0;overflow-wrap:anywhere}
  .sg-work-board .sg-work-status{max-width:100%;min-width:0;overflow-wrap:anywhere}
}
@media(prefers-reduced-motion:reduce){
  .sg-work-board .sg-work-item{transition:none}
}
"""
JS = r"""
(function(){
 const widget=document.querySelector('[data-sg-notifications]');
 if(!widget)return;
 // The same notification dropdown is used by classic Studio and Growth v4.
 // A tap outside closes it without blocking the tap's intended action.
 document.addEventListener('pointerdown',event=>{
   if(widget.open && event.target instanceof Node && !widget.contains(event.target)){
     widget.open=false;
   }
 });
 document.addEventListener('keydown',event=>{
   if(event.key==='Escape' && widget.open){
     const hadFocus=widget.contains(document.activeElement);
     widget.open=false;
     if(hadFocus)widget.querySelector('summary')?.focus();
   }
 });
 const endpoint=widget.getAttribute('data-feed-url');
 const count=widget.querySelector('[data-sg-count]');
 const list=widget.querySelector('[data-sg-notify-list]');
 const detail=document.querySelector('[data-sg-task]');
 const showWork=document.querySelector('[data-sg-work-overview]');
 const workList=document.querySelector('.sg-work-list');
 const workBoard=document.querySelector('[data-sg-work-board]');
 const updateButton=workBoard?.querySelector('[data-sg-work-update]');
 const previewRail=document.querySelector('[data-sg-work-preview]');
 const previewMq=window.matchMedia('(min-width:1081px)');
 let selectedPreviewHref='';
 let pendingBoard=null;
 let lastSignature=null;
 let shown=[];
 const readAll=widget.querySelector('[data-sg-read-all]');
 let ongoing=false;
 function localizeWorkTimes(root){
   const formatter=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'});
   root.querySelectorAll('time[datetime]').forEach(el=>{
     const value=new Date(el.getAttribute('datetime'));
     if(Number.isFinite(value.getTime()))el.textContent='Создано '+formatter.format(value);
   });
 }
 // Contextual desktop preview uses existing, server-escaped cards.
 // It never steals the main card link or sends a new API request.
 function previewAnchor(node){
   const a=node?.querySelector('a.sg-work-item[href]');
   if(!a)return null;
   const url=new URL(a.getAttribute('href'),window.location.origin);
   const root=window.location.pathname.replace(/\/work$/, '')+'/';
   if(url.origin!==window.location.origin||!url.pathname.startsWith(root))return null;
   return {a,url};
 }
 function resetTaskPreview(){
   selectedPreviewHref='';
   if(!previewRail)return;
   const hint=previewRail.querySelector('[data-sg-preview-hint]');
   const detail=previewRail.querySelector('[data-sg-preview-detail]');
   if(hint)hint.hidden=false;
   if(detail)detail.hidden=true;
   const announcement=previewRail.querySelector('[data-sg-preview-announcement]');
   if(announcement)announcement.textContent='';
   workBoard?.querySelectorAll('[data-sg-task-peek]').forEach(b=>
     b.setAttribute('aria-pressed','false'));
 }
 function showTaskPreview(row){
   if(!previewRail||!previewMq.matches)return;
   const verified=previewAnchor(row);
   if(!verified)return;
   const {a,url}=verified;
   const summary=(selector)=>a.querySelector(selector)?.textContent?.trim()||'';
   const values={
     '[data-sg-preview-kind]': summary('.sg-work-kind'),
     '[data-sg-preview-title]': summary('.sg-work-item-main b'),
     '[data-sg-preview-status]': summary('.sg-work-status'),
     '[data-sg-preview-summary]': summary('.sg-work-summary'),
     '[data-sg-preview-date]': summary('.sg-work-item-foot time'),
   };
   for(const [selector,value] of Object.entries(values)){
     const node=previewRail.querySelector(selector);
     if(node)node.textContent=value;
   }
   const link=previewRail.querySelector('[data-sg-preview-link]');
   if(link)link.href=url.pathname+url.search;
   const hint=previewRail.querySelector('[data-sg-preview-hint]');
   const detail=previewRail.querySelector('[data-sg-preview-detail]');
   if(hint)hint.hidden=true;
   if(detail)detail.hidden=false;
   // The live status stays in the accessibility tree even while detail is
   // hidden, so title and status changes are announced after the reveal.
   const announcement=previewRail.querySelector('[data-sg-preview-announcement]');
   if(announcement)announcement.textContent=
     'Выбрано задание: '+values['[data-sg-preview-title]']+
     '. Статус: '+values['[data-sg-preview-status]'];
   selectedPreviewHref=a.getAttribute('href');
   workBoard?.querySelectorAll('[data-sg-task-peek]').forEach(b=>{
     const target=previewAnchor(b.closest('.sg-resource-widget'));
     b.setAttribute('aria-pressed',String(
       Boolean(target&&target.a.getAttribute('href')===selectedPreviewHref)));
   });
 }
 function installPreviewButtons(root){
   if(!previewRail||!previewMq.matches||!root)return;
   root.querySelectorAll('.sg-resource-widget[data-sg-resource-widget="task"]').forEach(row=>{
     if(row.querySelector('[data-sg-task-peek]')||!previewAnchor(row))return;
     const button=document.createElement('button');
     button.type='button';
     button.className='sg-task-peek-action';
     button.dataset.sgTaskPeek='';
     button.textContent='Просмотр';
     button.setAttribute('aria-label','Быстрый просмотр справа: '+
       (row.querySelector('.sg-work-item-main b')?.textContent||'Задание'));
     button.setAttribute('aria-controls','sgWorkPreviewDetail');
     button.setAttribute('aria-pressed','false');
     row.classList.add('sg-has-task-peek');
     row.appendChild(button);
   });
 }
 if(workBoard&&previewRail){
   workBoard.addEventListener('click',event=>{
     const button=event.target instanceof Element ?
       event.target.closest('[data-sg-task-peek]'):null;
     if(button&&workBoard.contains(button)){
       showTaskPreview(button.closest('.sg-resource-widget'));
     }
   });
   previewMq.addEventListener('change',()=>{
     if(previewMq.matches)installPreviewButtons(workBoard);
   });
   installPreviewButtons(workBoard);
 }
 const anchor=(item)=>{
   const a=document.createElement('a');
   a.className='sg-notify-entry '+(item.kind==='error'?'sg-notify-error':'');
   a.textContent=item.text;
   const url=new URL(item.url,window.location.origin);
   if(url.origin===window.location.origin&&url.pathname.startsWith('/studio/products/'))
     a.href=url.pathname+url.search;
   else a.href=widget.querySelector('.sg-notify-all')?.getAttribute('href')||'/studio';
   return a;
 };
 // Refresh only the server-rendered task cards. Never reload the whole page
 // while someone is reading a long card, scrolling, or using the search field.
 function applyBoard(fresh){
   const previous=workBoard?.querySelector('.sg-work-list');
   const next=fresh?.querySelector('.sg-work-list');
   if(!previous||!next)return;
   const oldY=window.scrollY;
   previous.replaceChildren(...Array.from(next.childNodes));
   localizeWorkTimes(previous);
   installPreviewButtons(previous);
   if(selectedPreviewHref){
     const match=Array.from(previous.querySelectorAll('.sg-work-item[href]'))
       .find(a=>a.getAttribute('href')===selectedPreviewHref);
     if(match)showTaskPreview(match.closest('.sg-resource-widget'));
     else resetTaskPreview();
   }
   const oldPages=workBoard.querySelector('.sg-work-pagination');
   const newPages=fresh.querySelector('.sg-work-pagination');
   if(oldPages&&newPages)oldPages.replaceWith(newPages);
   else if(oldPages)oldPages.remove();
   else if(newPages)previous.after(newPages);
   const oldCounts=workBoard.querySelectorAll('.sg-work-tab-count');
   const newCounts=fresh.querySelectorAll('.sg-work-tab-count');
   if(oldCounts.length===newCounts.length){
     oldCounts.forEach((node,i)=>{node.textContent=newCounts[i].textContent;});
   }
   workBoard.dataset.sgListSignature=fresh.dataset.sgListSignature;
   pendingBoard=null;
   if(updateButton)updateButton.hidden=true;
   // Keep the reading position stable if row counts or heights changed.
   window.scrollTo({top:oldY,behavior:'instant'});
 }
 async function refreshBoard(){
   const url=workBoard?.dataset.sgRefreshUrl;
   if(!url)return;
   const response=await fetch(url,{cache:'no-store',credentials:'same-origin',
     headers:{'Accept':'application/vnd.servgo.tasks+html'}});
   if(!response.ok || !(response.headers.get('content-type')||'').includes(
       'application/vnd.servgo.tasks+html'))return;
   const documentHtml=new DOMParser().parseFromString(await response.text(),'text/html');
   const fresh=documentHtml.querySelector('[data-sg-work-board]');
   if(!fresh)return;
   if(fresh.dataset.sgListSignature===workBoard.dataset.sgListSignature){
     pendingBoard=null;
     if(updateButton)updateButton.hidden=true;
     return;
   }
   const list=workBoard.querySelector('.sg-work-list');
   const focusInList=Boolean(list?.contains(document.activeElement));
   const readingList=window.scrollY>240&&list?.getBoundingClientRect().top<120;
   if(focusInList||readingList){
     pendingBoard=fresh;
     if(updateButton)updateButton.hidden=false;
     return;
   }
   applyBoard(fresh);
 }
 if(updateButton)updateButton.addEventListener('click',()=>{
   if(pendingBoard)applyBoard(pendingBoard);
 });
 document.addEventListener('scroll',()=>{
   if(pendingBoard && window.scrollY<=120 &&
       !workList?.contains(document.activeElement))applyBoard(pendingBoard);
 },{passive:true});
 async function poll(){
   if(document.hidden||ongoing)return;
   ongoing=true;
   try{
     // The fragment endpoint includes the selected page, query, date range
     // and exact status counts. It must not rely on the recent 45-job feed.
     if(workBoard){
       try{await refreshBoard();}catch(_){/* list refresh failure must not block notifications */}
     }
     const feedUrl=new URL(endpoint,window.location.origin);
     if(detail?.dataset.sgTask &&
        /^[0-9a-f-]{36}$/i.test(detail.dataset.sgTask)){
       feedUrl.searchParams.set('task_id',detail.dataset.sgTask);
     }
     const res=await fetch(feedUrl,{cache:'no-store',credentials:'same-origin',
       headers:{'Accept':'application/json'}});
     if(!res.ok||!(res.headers.get('content-type')||'').includes('application/json'))return;
     const data=await res.json();
     const notes=Array.isArray(data.notices)?data.notices:[];
     shown=notes.map(x=>x.id);
     if(readAll)readAll.hidden=!notes.length;
     count.hidden=!notes.length;
     count.textContent=String(notes.length);
     const sig=notes.map(x=>x.id+':'+x.kind).join(',');
     if(sig!==lastSignature){
       list.replaceChildren();
       if(!notes.length){const p=document.createElement('p');
          p.className='sg-notify-empty';p.textContent='Пока нет материалов, требующих решения.';
          list.appendChild(p);}
       for(const item of notes)list.appendChild(anchor(item));
       lastSignature=sig;
     }
     const jobs=Array.isArray(data.jobs)?data.jobs:[];
     if(detail){
       const currentJob=jobs.find(j=>j.id===detail.dataset.sgTask);
       if(currentJob){
         const seoPhase=detail.dataset.sgStageMode==='seo';
         if(seoPhase && currentJob.state===detail.dataset.sgState){
           const updatedStage=currentJob.current_stage||
             'Подготовка поисковых запросов и плана';
           if(updatedStage!==detail.dataset.sgStage){
             const stageNode=detail.querySelector('[data-sg-seo-stage-text]');
             if(stageNode){
               stageNode.textContent=updatedStage;
               detail.dataset.sgStage=updatedStage;
             }
           }
         }
         const stateChanged=currentJob.state!==detail.dataset.sgState;
         const otherStageChanged=!seoPhase && detail.hasAttribute('data-sg-stage') &&
           currentJob.stage_signature!==detail.dataset.sgStage;
         if((stateChanged||otherStageChanged) &&
            document.visibilityState==='visible')location.reload();
       }
     }
     if(showWork&&!workBoard){
       const statuses=jobs.map(j=>j.id+':'+j.state+':'+Boolean(j.needs_decision)+':'+j.result_count+':'+(j.artifacts||[]).map(a=>a.id+':'+a.status).join(',')).join('|');
       const store=showWork;
       if(store.hasAttribute('data-sg-signature')&&store.dataset.sgSignature!==statuses&&
          document.activeElement?.tagName!=='INPUT' &&
          document.activeElement?.tagName!=='TEXTAREA' ){
          location.reload();
       }
       store.dataset.sgSignature=statuses;
     }
   }catch(_){/* intermittent network errors do not mean work has stopped */ }
   finally{ongoing=false;}
 }
 if(readAll)readAll.addEventListener('click',async()=>{
   if(!shown.length)return;
   readAll.disabled=true;
   try{
     const res=await fetch(readAll.dataset.readUrl,{method:'POST',credentials:'same-origin',
       headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({ids:shown})});
     if(res.ok){lastSignature=null;await poll();}
   }finally{readAll.disabled=false;}
 });
 if(workBoard)localizeWorkTimes(workBoard);
 poll();
 window.setInterval(poll,20000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll()});
})();
"""
