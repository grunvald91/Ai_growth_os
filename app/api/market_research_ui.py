"""Accessible owner web page for Wordstat market research; data rendered via textContent."""
PAGE = """
<style>
.w-card{border:1px solid #dce5ef;border-radius:14px;background:#fff;padding:18px;margin:0 0 16px}
.w-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px}
.w-card label{display:block;color:#4b5c72;font-size:14px;margin-bottom:10px}
.w-card input:not([type=checkbox]),.w-card select{display:block;width:100%;box-sizing:border-box;border:1px solid #c9d7e5;border-radius:8px;padding:10px;margin-top:5px;font:inherit}
.w-card button{border:0;border-radius:9px;padding:12px 18px;background:#225b9a;color:white;cursor:pointer}
.w-card button:disabled{opacity:.55}
.w-card table{border-collapse:collapse;width:100%}
.w-card th,.w-card td{padding:9px;border-bottom:1px solid #e7ecf2;text-align:left;word-break:break-word}
.w-card th:last-child,.w-card td:last-child{text-align:right}
.w-note{color:#647388;font-size:13px}.w-error{color:#a52222}
.w-bar{background:#e9f0f8;border-radius:5px}.w-fill{height:12px;background:#618dc2;border-radius:5px}
@media(max-width:780px){.w-grid{grid-template-columns:1fr}}
</style>
<section class="w-card">
<p>Проверяем фактический поисковый спрос. Не трактуйте его как количество покупателей или готовность платить.</p>
<form id="wordstat-form">
<div class="w-grid">
<label>Поисковая фраза<input name="phrase" minlength="2" maxlength="200" required placeholder="например: онлайн запись клиентов"></label>
<label>Регион<select name="region_id"><option value="225">Россия</option><option value="213">Москва</option><option value="2">Санкт-Петербург</option><option value="239">Сочи</option></select></label>
<label>Топ<select name="num_phrases"><option value="20">20 запросов</option><option value="50">50 запросов</option><option value="80">80 запросов</option></select></label>
</div>
<label><input type="checkbox" name="include_dynamics"> Динамика за 12 месяцев (дополнительный платный вызов)</label>
<button type="submit">Исследовать спрос</button>
</form>
<p id="wordstat-status" class="w-note">Проверяем подключение…</p><p id="wordstat-error" class="w-error" role="alert"></p>
</section>
<section class="w-card" id="wordstat-result" hidden>
<h2 id="wordstat-title"></h2><p id="wordstat-total"></p>
<h3>Частотные формулировки</h3><div id="wordstat-phrases"></div>
<h3>Похожие запросы</h3><div id="wordstat-associated"></div>
<h3>История по месяцам</h3><div id="wordstat-trend"></div>
<p class="w-note">Источник: <a href="https://aistudio.yandex.ru/ru/docs/search-api/concepts/wordstat" target="_blank" rel="noopener noreferrer">Yandex Cloud Wordstat API</a>. Данные не являются оценкой продаж.</p>
</section>
<script src="/market-research/assets/market.js" defer></script>
"""

SCRIPT = r"""
(function(){
  'use strict';
  const el=id=>document.getElementById(id);
  const format=v=>Number(v).toLocaleString('ru-RU');
  function table(id,items){
    const node=el(id);node.replaceChildren();
    if(!items.length){node.textContent='Нет данных';return;}
    const t=document.createElement('table'),head=document.createElement('tr');
    for(const name of ['Фраза','Показов']){const th=document.createElement('th');th.textContent=name;head.append(th);}
    t.append(head);
    for(const item of items){
      const tr=document.createElement('tr'),a=document.createElement('td'),b=document.createElement('td');
      a.textContent=item.phrase;b.textContent=format(item.count);tr.append(a,b);t.append(tr);
    }
    node.append(t);
  }
  async function status(){
    const r=await fetch('/market-research/status'), d=await r.json();
    el('wordstat-status').textContent=d.configured ?
      'Wordstat подключён. Осталось платных вызовов сегодня: '+format(d.remaining_calls_today) :
      'Wordstat ещё не подключён — требуется API-ключ и Folder ID Yandex Cloud.';
  }
  const form=el('wordstat-form');
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const button=form.querySelector('button');button.disabled=true;
    el('wordstat-error').textContent='';
    try{
      const d=new FormData(form);
      const params={phrase:String(d.get('phrase')),region_id:Number(d.get('region_id')),
        num_phrases:Number(d.get('num_phrases')),include_dynamics:d.has('include_dynamics')};
      const response=await fetch('/market-research/analyze',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)
      });
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Ошибка Wordstat');
      el('wordstat-result').hidden=false;
      el('wordstat-title').textContent=result.phrase;
      el('wordstat-total').textContent='Запросов за последние 30 дней: '+
        format(result.total_count_last_30_days)+' · регион '+result.region_id+
        ' · оставшиеся вызовы: '+format(result.remaining_calls_today);
      table('wordstat-phrases',result.results);
      table('wordstat-associated',result.associations);
      const trend=el('wordstat-trend');trend.replaceChildren();
      if(!result.monthly_dynamics.length)trend.textContent='История не запрашивалась или данных нет.';
      const max=Math.max(1,...result.monthly_dynamics.map(x=>x.count));
      for(const item of result.monthly_dynamics){
        const p=document.createElement('p');p.textContent=item.date.slice(0,7)+' — '+format(item.count);
        const track=document.createElement('div'),fill=document.createElement('div');
        track.className='w-bar';fill.className='w-fill';
        fill.style.width=(100*item.count/max).toFixed(2)+'%';
        track.append(fill);trend.append(p,track);
      }
      await status();
    }catch(err){el('wordstat-error').textContent=err.message;}
    finally{button.disabled=false;}
  });
  status().catch(()=>{el('wordstat-status').textContent='Статус Wordstat недоступен';});
})();
"""
