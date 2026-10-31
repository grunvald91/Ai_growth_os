/* Live bindings only. The preview's local/demo action handlers are not loaded. */
(function(){
 'use strict';
 const body=document.body, main=document.getElementById('main');
 try{body.classList.toggle('dark',localStorage.getItem('servgo-desktop-theme')==='dark')}catch(e){}
 document.getElementById('themeTop').addEventListener('click',()=>{
  const dark=body.classList.toggle('dark');
  try{localStorage.setItem('servgo-desktop-theme',dark?'dark':'light')}catch(e){}
 });
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.mobile-nav details[open]').forEach(d=>d.open=false);closeSearch()}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();search()}});
 const product=location.pathname.match(/\/studio\/products\/([0-9a-f-]{36})/i);
 const base=product?'/studio/products/'+product[1]:null;
 document.getElementById('bizBtn').onclick=()=>location.assign('/studio?choose=1');
 document.getElementById('notifyBtn')?.addEventListener('click',()=>location.assign('/studio?choose=1'));
 document.querySelector('[data-focusvoice]').onclick=()=>{const input=document.getElementById('dp-input');if(input)input.focus();else location.assign(base||'/studio')};
 let searchBox,returnFocus;
 function closeSearch(){if(searchBox){searchBox.remove();searchBox=null;document.querySelector('.shell').inert=false;returnFocus?.focus()}}
 function search(){
  if(searchBox)return;
  returnFocus=document.activeElement;
  searchBox=document.createElement('div');searchBox.className='v4-search';searchBox.setAttribute('role','dialog');searchBox.setAttribute('aria-modal','true');searchBox.setAttribute('aria-label','Поиск раздела');
  searchBox.innerHTML='<section><button class="btn ghost" type="button">Закрыть</button><input aria-label="Поиск раздела" placeholder="Найти раздел…"><nav></nav></section>';
  searchBox.querySelector('button').onclick=closeSearch;
  const input=searchBox.querySelector('input');
  function update(){const nav=searchBox.querySelector('nav');nav.replaceChildren();document.querySelectorAll('#leftNav a').forEach(a=>{if(a.textContent.toLowerCase().includes(input.value.toLowerCase()))nav.append(a.cloneNode(true))})}
  input.oninput=update;document.body.append(searchBox);document.querySelector('.shell').inert=true;update();input.focus();
  searchBox.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const nodes=[...searchBox.querySelectorAll('button,input,a')];if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes.at(-1).focus()}else if(!e.shiftKey&&document.activeElement===nodes.at(-1)){e.preventDefault();nodes[0].focus()}});
 }
 document.getElementById('openSearch').onclick=search;
 const root=document.querySelector('.dp-home');
 if(root){
  const viewport=window.visualViewport;
  const fitConversation=()=>{
   body.style.setProperty('--v4-viewport',(viewport?.height||window.innerHeight)+'px');
   body.style.setProperty('--v4-header',document.querySelector('.topbar').getBoundingClientRect().height+'px');
   body.style.setProperty('--v4-nav',document.getElementById('mobileNav').getBoundingClientRect().height+'px');
  };
  const layoutObserver=new ResizeObserver(fitConversation);
  layoutObserver.observe(document.querySelector('.topbar'));
  layoutObserver.observe(document.getElementById('mobileNav'));
  viewport?.addEventListener('resize',fitConversation);
  window.addEventListener('resize',fitConversation);
  fitConversation();
  const feed=document.getElementById('dp-feed');
  let previousHeight=feed.clientHeight,previousContent=feed.scrollHeight;
  new ResizeObserver(()=>{
   const atBottom=feed.scrollTop>=previousContent-previousHeight-24;
   if(atBottom)feed.scrollTop=feed.scrollHeight;
   previousHeight=feed.clientHeight;previousContent=feed.scrollHeight;
  }).observe(feed);
  requestAnimationFrame(()=>{feed.scrollTop=feed.scrollHeight});
  const hints=document.getElementById('dp-chips');
  function compactHints(){hints.querySelectorAll('.dp-chip').forEach(button=>{
   const label=button.getAttribute('aria-label')||button.textContent;
   if(label.length>34){button.setAttribute('aria-label',label);button.title=label;const short=label.slice(0,31)+'…';if(button.textContent!==short)button.textContent=short}
  })}
  new MutationObserver(compactHints).observe(hints,{childList:true,subtree:true});compactHints();
  const sync=()=>{const has=root.classList.contains('has-feed');body.classList.toggle('home-empty',!has)};
  new MutationObserver(sync).observe(root,{attributes:true,attributeFilter:['class']});sync();
  const commands={seo_factory:'Запусти SEO-завод для моей компании',campaign:'Помоги спланировать маркетинговую кампанию: цель, аудитория, каналы и показатели'};
  const command=new URLSearchParams(location.search).get('command');
  if(commands[command]){const input=document.getElementById('dp-input');input.value=commands[command];input.dispatchEvent(new Event('input',{bubbles:true}));input.focus()}
 }
 const suffix=base?location.pathname.slice(base.length):'';
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const glyph=n=>'<svg class="icon"><use href="#i-'+n+'"/></svg>';
 // Internal worker identifiers belong in diagnostics, not in a business owner's cards.
 const workerLabels={whiteboard_renderer:'Простое видео',company_intake:'Профиль компании',
  distribution:'Публикация',seo_factory:'SEO-завод',content_factory:'Создание контента',
  critic:'Проверка качества',research:'Аналитика'};
 const friendlyWorker=value=>workerLabels[String(value||'').toLowerCase()]||
  (/^[a-z0-9_-]+$/.test(String(value||''))?'ИИ-команда':String(value||'ИИ-команда'));
 const friendlyTaskTitle=t=>{
  const title=String(t.title||'Задание');
  if(/^WHITEBOARD-JOB:\s*[a-f0-9-]{6,}$/i.test(title))
   return String(t.detail||'Создание простого видео').replace(/^Рисованное видео:/i,'Простое видео:');
  if(/^Request approval to publish own-site SEO article/i.test(title))
   return 'Подтвердить публикацию SEO-статьи';
  return title;
 };
 const friendlyTaskDetail=t=>{
  const detail=String(t.detail||'');
  if(/^Human-approval request only:/i.test(detail))
   return 'Статья подготовлена. Проверьте её перед публикацией.';
  return detail.replace(/^Рисованное видео:/i,'Простое видео:');
 };
 if(base&&suffix==='/work'){
  const original=main.querySelector('.route-content');
  const view=document.createElement('section');view.className='v4-records';view.setAttribute('aria-live','polite');view.innerHTML='<p class="v4-loading">Загружаем данные компании…</p>';
  // Keep original filters and server-side controls available until data succeeds.
  original.before(view);
  fetch(base+'/v4/data',{headers:{Accept:'application/json'},credentials:'same-origin'}).then(async r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{
   if(suffix==='/work'){
    const labels={queued:'Запланировано',running:'В процессе',waiting_approval:'Ждут решения',completed:'Готово',failed:'Нужны действия',cancelled:'Отменено'};
    view.innerHTML='<div class="board animate-in">'+Object.entries(labels).map(([status,label])=>{
     const rows=data.tasks.filter(t=>t.status===status);
     return '<section class="board-col"><div class="board-header"><b>'+label+'</b><span>'+rows.length+'</span></div>'+rows.map(t=>'<a class="board-card" href="'+base+'/tasks/'+encodeURIComponent(t.id)+'"><span class="chip '+(status==='waiting_approval'?'amber':'blue')+'">'+esc(friendlyWorker(t.group))+'</span><div style="margin-top:10px"><b>'+esc(friendlyTaskTitle(t))+'</b><p>'+esc(friendlyTaskDetail(t).slice(0,200))+'</p></div><span class="link">Открыть '+glyph('arrow')+'</span></a>').join('')+(rows.length?'':'<div class="empty"><p>Нет заданий</p></div>')+'</section>';
    }).join('')+'</div>';
   }
   original.hidden=true;
   const details=document.createElement('details');details.className='mt14';const summary=document.createElement('summary');summary.textContent='Полный список, поиск и фильтры (карточки: последние 200)';details.append(summary);original.before(details);details.append(original);original.hidden=false;
  }).catch(()=>{view.innerHTML='<p class="v4-error" role="alert">Не удалось загрузить карточки. Ниже доступен рабочий список. <button type="button" class="link" onclick="location.reload()">Повторить</button></p>'});
 }
})();
