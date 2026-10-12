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
.sg-work-steps,.sg-work-results{border-top:1px solid #E5EAF1;margin:21px 0 0;padding:17px 0 0}
.sg-work-steps h3,.sg-work-results h3{font-size:17px;margin:0 0 10px;font-weight:700}
.sg-work-steps ol{list-style:none;margin:0;padding:0;display:grid;gap:5px}
.sg-work-steps li{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:11px 12px;background:#F7F9FC;border-radius:10px;font-size:13px;color:#344D64}
.sg-work-steps li b{font-weight:600;white-space:nowrap;color:#335D92}
.sg-work-result{display:flex;justify-content:space-between;align-items:center;gap:12px;
  border:1px solid #E1E7F0;border-radius:11px;padding:12px 14px;margin:9px 0;
  color:#234D9E;text-decoration:none}
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
"""
JS = r"""
(function(){
 const widget=document.querySelector('[data-sg-notifications]');
 if(!widget)return;
 const endpoint=widget.getAttribute('data-feed-url');
 const count=widget.querySelector('[data-sg-count]');
 const list=widget.querySelector('[data-sg-notify-list]');
 const detail=document.querySelector('[data-sg-task]');
 const showWork=document.querySelector('[data-sg-work-overview]');
 const workList=document.querySelector('.sg-work-list');
 let lastSignature=null;
 let ongoing=false;
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
 async function poll(){
   if(document.hidden||ongoing)return;
   ongoing=true;
   try{
     const res=await fetch(endpoint,{cache:'no-store',credentials:'same-origin',
       headers:{'Accept':'application/json'}});
     if(!res.ok||!(res.headers.get('content-type')||'').includes('application/json'))return;
     const data=await res.json();
     const notes=Array.isArray(data.notices)?data.notices:[];
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
       const changed=jobs.find(j=>j.id===detail.dataset.sgTask &&
           j.state!==detail.dataset.sgState);
       if(changed&&document.visibilityState==='visible')location.reload();
     }
     if(workList||showWork){
       const active=jobs.filter(j=>j.state==='queued'||j.state==='running');
       if(!workList&&!active.length)return;
       const statuses=jobs.slice(0,30).map(j=>j.id+':'+j.state+':'+j.artifacts.length).join('|');
       const store=workList||showWork;
       if(store.dataset.sgSignature&&store.dataset.sgSignature!==statuses&&
          document.activeElement?.tagName!=='INPUT' &&
          document.activeElement?.tagName!=='TEXTAREA' ){
          location.reload();
       }
       store.dataset.sgSignature=statuses;
     }
   }catch(_){/* intermittent network errors do not mean work has stopped */ }
   finally{ongoing=false;}
 }
 poll();
 window.setInterval(poll,20000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll()});
})();
"""
