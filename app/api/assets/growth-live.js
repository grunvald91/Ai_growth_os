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
 document.getElementById('notifyBtn').onclick=()=>location.assign(base?base+'/work':'/studio');
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
 if(base&&['/work','/materials'].includes(suffix)){
  const original=main.querySelector('.route-content');
  const view=document.createElement('section');view.className='v4-records';view.setAttribute('aria-live','polite');view.innerHTML='<p class="v4-loading">Загружаем данные компании…</p>';
  // Keep original filters and server-side controls available until data succeeds.
  original.before(view);
  fetch(base+'/v4/data',{headers:{Accept:'application/json'},credentials:'same-origin'}).then(async r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{
   if(suffix==='/work'){
    const labels={queued:'Запланировано',running:'В процессе',waiting_approval:'Ждут решения',completed:'Готово',failed:'Нужны действия',cancelled:'Отменено'};
    view.innerHTML='<div class="board animate-in">'+Object.entries(labels).map(([status,label])=>{
     const rows=data.tasks.filter(t=>t.status===status);
     return '<section class="board-col"><div class="board-header"><b>'+label+'</b><span>'+rows.length+'</span></div>'+rows.map(t=>'<a class="board-card" href="'+base+'/tasks/'+encodeURIComponent(t.id)+'"><span class="chip '+(status==='waiting_approval'?'amber':'blue')+'">'+esc(t.group)+'</span><div style="margin-top:10px"><b>'+esc(t.title)+'</b><p>'+esc(t.detail.slice(0,200))+'</p></div><span class="link">Открыть '+glyph('arrow')+'</span></a>').join('')+(rows.length?'':'<div class="empty"><p>Нет заданий</p></div>')+'</section>';
    }).join('')+'</div>';
   }else{
    let filter='all';const types=[['all','Все'],['post','Посты'],['article','SEO-статьи'],['video','Видео']];
    function draw(){const kinds={post:['channel_text','dzen_brief','telegram_brief'],video:['video_script','short_video_brief','video_render','whiteboard_video']};const rows=data.materials.filter(a=>filter==='all'||(kinds[filter]||[filter]).includes(a.kind));view.innerHTML='<div class="tab-row mb14">'+types.map(([id,label])=>'<button class="tab '+(filter===id?'active':'')+'" data-kind="'+id+'">'+label+'</button>').join('')+'</div><div class="materials-grid">'+rows.map(a=>'<a class="material-card surface animate-in" href="'+base+'/materials/'+encodeURIComponent(a.id)+'"><span class="type-icon">'+glyph(a.kind==='article'?'globe':'file')+'</span><b>'+esc(a.title)+'</b><p>'+esc(a.kind)+'</p><div class="split" style="width:100%;margin-top:14px"><span class="chip">'+esc(({draft:'Черновик',awaiting_approval:'Ждёт решения',approved:'Одобрено',published:'Опубликовано',rejected:'На доработку'})[a.status]||a.status)+'</span><span class="link">Открыть '+glyph('arrow')+'</span></div></a>').join('')+'</div>'+(rows.length?'':'<div class="empty">Материалов пока нет</div>');view.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{filter=b.dataset.kind;draw()})}draw();
   }
   original.hidden=true;
   const details=document.createElement('details');details.className='mt14';const summary=document.createElement('summary');summary.textContent='Полный список, поиск и фильтры (карточки: последние 200)';details.append(summary);original.before(details);details.append(original);original.hidden=false;
  }).catch(()=>{view.innerHTML='<p class="v4-error" role="alert">Не удалось загрузить карточки. Ниже доступен рабочий список. <button type="button" class="link" onclick="location.reload()">Повторить</button></p>'});
 }
})();
