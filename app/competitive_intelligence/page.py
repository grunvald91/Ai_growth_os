"""Russian owner UI, using the existing Growth Studio shell."""
from html import escape
from uuid import UUID

from app.api.content_studio import studio_page_shell


def render_page(products, selected: UUID | None):
    if not products:
        return '<!doctype html><html lang="ru"><meta charset="utf-8"><h1>Бизнес-разведка</h1><p>Сначала создайте компанию в AI Growth OS.</p><a href="/studio">Проекты</a></html>'
    product = next(row for row in products if row.id == selected)
    options = "".join(f'<option value="{row.id}" {"selected" if row.id == selected else ""}>{escape(row.name)}</option>' for row in products)
    body = f"""
<style>
.intel-card{{background:white;border:1px solid #e1e7ef;border-radius:16px;padding:24px;margin:16px 0}}
.intel-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}}
.intel-card label{{display:block;margin:8px 0;color:#34435b}}
.intel-card input,.intel-card select,.intel-card textarea{{display:block;width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccd7e5;border-radius:8px;font:inherit;background:white;color:#1b2d45}}
.intel-card textarea{{min-height:90px}}
.intel-actions{{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}}
.intel-card button{{padding:10px 16px;border:1px solid #c5d6ea;border-radius:9px;background:#edf5ff;color:#174987;cursor:pointer;font:inherit}}
.intel-card button:disabled{{opacity:.5;cursor:wait}}
.intel-card button[aria-selected="true"]{{background:#174987;color:white}}
.intel-source{{border-top:1px solid #e1e7ef;padding-top:12px;margin-top:12px}}
.intel-card a{{color:#1759a5;overflow-wrap:anywhere}}
.intel-card pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
.intel-card blockquote{{border-left:3px solid #a5bbd5;margin:12px 0;padding:8px 16px;white-space:pre-wrap}}
.intel-card [hidden]{{display:none!important}}
#intel-error{{color:#a32424}}#intel-stages .current{{font-weight:bold;color:#1759a5}}
details{{margin:12px 0}}summary{{cursor:pointer}}
</style>
<section class="intel-card">
<p><a href="/market-research?product_id={product.id}">⌕ Анализ поискового спроса — Wordstat →</a></p>
<form id="intel-form">
<label>Компания <select id="intel-product">{options}</select></label>
<label>Что хотите узнать? <textarea id="intel-brief" required minlength="3" maxlength="3000" placeholder="Например: какие жалобы на конкурентов повторяются и какой оффер стоит проверить?"></textarea></label>
<div class="intel-actions" id="intel-quick">
<button type="button" data-mode="weaknesses">Найти слабости конкурентов</button>
<button type="button" data-mode="pricing">Сравнить цены</button>
<button type="button" data-mode="reviews">Проанализировать отзывы</button>
</div>
<div class="intel-grid">
<label>Режим <select id="intel-mode"><option value="reviews">Отзывы</option><option value="weaknesses">Слабости конкурентов</option><option value="pricing">Цены и предложения</option></select></label>
<label>Регион <input id="intel-region" maxlength="200" placeholder="Например, Москва"></label>
<label>Глубина <select id="intel-depth"><option value="quick">Быстро</option><option value="standard" selected>Стандарт</option><option value="deep">Глубоко</option></select></label>
<label>Публикации с <input id="intel-from" type="date"></label>
<label>Публикации по <input id="intel-to" type="date"></label>
</div>
<label>Конкуренты, по одному на строку <textarea id="intel-competitors" maxlength="2010" placeholder="Оставьте пустым, чтобы использовать всех конкурентов из добавленных источников"></textarea></label>
<details><summary>Добавить источники к этому исследованию</summary>
<p>Укажите URL и выдержку из первоисточника. Если ничего не добавлять, используются ранее сохранённые источники компании. Поиск по интернету появится на следующем этапе.</p>
<div id="intel-sources"></div><button type="button" id="intel-add-source">Добавить источник</button>
</details>
<div class="intel-actions"><button id="intel-submit" type="submit">Начать исследование</button></div>
</form><p id="intel-error" role="alert"></p>
</section>
<section class="intel-card"><h2>Исследования компании</h2><div id="intel-history"></div></section>
<section id="intel-progress" class="intel-card" hidden aria-live="polite"><h2 id="intel-status">Исследование</h2>
<ol id="intel-stages"><li data-stage="planning">План исследования</li><li data-stage="sources">Источники</li><li data-stage="triage">Отбор данных</li><li data-stage="analysis">Анализ</li><li data-stage="validation">Проверка выводов</li><li data-stage="ready">Готовый отчёт</li></ol>
<p id="intel-counts"></p><div id="intel-plan"></div></section>
<section id="intel-report" class="intel-card" hidden><h2>Результаты исследования</h2>
<div class="intel-actions" role="tablist" aria-label="Представления отчёта" id="intel-tabs">
<button type="button" role="tab" data-view="all">Выводы</button><button type="button" role="tab" data-view="weaknesses">Карта слабостей</button>
<button type="button" role="tab" data-view="pricing">Цены</button><button type="button" role="tab" data-view="opportunities">Возможности</button>
<button type="button" role="tab" data-view="sources">Источники</button><button type="button" role="tab" data-view="method">Методика</button></div>
<div id="intel-result" role="tabpanel"></div>
<form id="intel-followup"><label>Уточнить исследование <textarea id="intel-followup-brief" required minlength="3" maxlength="3000"></textarea></label><button type="submit">Исследовать уточнение</button></form>
</section>
<script src="/intelligence/assets/page.js" defer></script>
"""
    return studio_page_shell(product, active="intelligence", title="Бизнес-разведка",
        subtitle="Изучает рынок, отзывы, цены и позиции конкурентов. Находит слабые места, возможности и точки роста для бизнеса.", body=body)


SCRIPT = r"""
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const base = '/competitive-intelligence/runs';
  const labels = {queued:'В очереди',running:'Исследование выполняется',completed:'Готово',partial:'Готово: есть пробелы в данных',needs_sources:'Нужны источники',failed:'Исследование не завершено'};
  const reasons = {duplicate:'Дубликат',competitor_filter:'Другой конкурент',outside_period:'Вне выбранного периода',unknown_publication_date:'Нет даты публикации',future_publication_date:'Дата публикации в будущем',depth_limit:'За пределами выбранной глубины',irrelevant:'Нерелевантный материал',low_confidence:'Недостаточная уверенность',triage_unavailable:'Не удалось классифицировать'};
  let runId = null, report = null, evidence = [], stream = null, pollTimer = null, generation = 0;
  function node(tag, text, parent) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (parent) parent.append(el); return el; }
  function error(text='') { $('intel-error').textContent = text; }
  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(res.status === 422 ? 'Проверьте поля формы, период и адреса источников.' : res.status === 404 ? 'Исследование или компания не найдены.' : data.detail === 'Report is not ready' ? 'Отчёт ещё не готов.' : 'Не удалось выполнить запрос. Повторите попытку.'); }
    return res.json();
  }
  function addSource() {
    if ($('intel-sources').children.length >= 50) { error('Можно добавить до 50 источников.'); return; }
    const row = node('div',undefined,$('intel-sources')); row.className='intel-source';
    const grid = node('div',undefined,row); grid.className='intel-grid';
    for (const [key,title,type] of [['competitor','Конкурент','text'],['url','URL источника','url'],['published_at','Дата публикации (если известна)','date']]) {
      const label = node('label',title,grid), input=node('input',undefined,label); input.type=type; input.dataset.field=key; input.maxLength=key==='url'?2000:200;
    }
    const label=node('label','Вид источника',grid), select=node('select',undefined,label); select.dataset.field='source_type';
    for (const [value,title] of [['review','Отзыв'],['pricing','Цены / тарифы'],['website','Сайт'],['social','Социальная сеть'],['marketplace','Маркетплейс']]) { const opt=node('option',title,select); opt.value=value; }
    const excerptLabel=node('label','Выдержка (точный текст первоисточника)',row), excerpt=node('textarea',undefined,excerptLabel); excerpt.dataset.field='excerpt'; excerpt.maxLength=4000;
    const remove=node('button','Удалить источник',row); remove.type='button'; remove.onclick=()=>row.remove();
  }
  function suppliedSources() {
    const rows=[];
    for (const row of $('intel-sources').children) {
      const value={}; row.querySelectorAll('[data-field]').forEach(input=>value[input.dataset.field]=input.value.trim());
      if (!value.url && !value.excerpt && !value.competitor) continue;
      if (!value.url || !value.excerpt || !value.competitor) throw new Error('Для каждого источника укажите конкурента, URL и выдержку.');
      if (value.published_at) value.published_at += 'T00:00:00Z'; else value.published_at=null;
      rows.push({...value,title:value.competitor,platform:new URL(value.url).hostname,observed_at:new Date().toISOString()});
    }
    return rows.length ? {label:'Источники бизнес-разведки',sources:rows} : null;
  }
  async function history() {
    const rows = await api(`${base}?product_id=${$('intel-product').value}`);
    $('intel-history').replaceChildren();
    if (!rows.length) node('p','Здесь появятся исследования и их результаты.',$('intel-history'));
    for (const row of rows) { const button=node('button',`${row.title} · ${labels[row.status] || row.status}`,$('intel-history')); button.type='button'; button.onclick=()=>watch(row.id).catch(e=>error(e.message)); }
  }
  function progress(state) {
    $('intel-progress').hidden=false;
    $('intel-status').textContent=labels[state.status] || state.status;
    $('intel-stages').querySelectorAll('li').forEach(el=>el.classList.toggle('current',el.dataset.stage===state.stage));
    const c=state.counts;
    $('intel-counts').textContent=`Найдено: ${c.found} · Отобрано: ${c.retained} · Исключено: ${c.excluded} · Передано на углублённый анализ: ${c.escalated}`;
    $('intel-plan').replaceChildren();
    if (state.plan) { node('h3','Вопросы исследования',$('intel-plan')); const list=node('ul',undefined,$('intel-plan')); state.plan.questions.forEach(q=>node('li',q,list)); }
    if (state.error) error(state.error==='research_model_unconfigured' ? 'Исследование не запущено: сервис анализа ещё не настроен.' : 'Исследование не завершено. Данные сохранены; можно запустить уточнение или новое исследование.');
  }
  function sourceDetails(row,parent) {
    const details=node('details',undefined,parent); node('summary',`${row.competitor} · ${row.source_type === 'pricing' ? 'Цена / тариф' : row.platform}`,details);
    const link=node('a','Открыть первоисточник',details);
    try { const url=new URL(row.source_url); if (['http:','https:'].includes(url.protocol)) link.href=url.href; } catch (_) {}
    link.target='_blank'; link.rel='noopener noreferrer';
    node('blockquote',row.excerpt,details);
    node('p',`Опубликовано: ${row.published_at ? row.published_at.slice(0,10) : 'неизвестно'} · Наблюдение: ${row.observed_at.slice(0,10)}`,details);
    if (row.rating !== null) node('p',`Рейтинг в источнике: ${row.rating}`,details);
    if (row.exclusion_reason) node('p',`Исключён: ${reasons[row.exclusion_reason] || row.exclusion_reason}`,details);
  }
  function render(view='all') {
    const root=$('intel-result'); root.replaceChildren();
    $('intel-tabs').querySelectorAll('button').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.view===view)));
    if (view==='sources') {
      const label=node('label','Показать',root), select=node('select',undefined,label);
      for (const [value,text] of [['all','Все источники'],['retained','Использованные'],['excluded','Исключённые']]) { const opt=node('option',text,select); opt.value=value; }
      const list=node('div',undefined,root);
      function filter() { list.replaceChildren(); evidence.filter(row=>select.value==='all'||row.disposition===select.value).forEach(row=>sourceDetails(row,list)); }
      select.onchange=filter; filter(); return;
    }
    if (view==='method') {
      node('p','Исследование ограничено добавленными выдержками. Выводы — сигналы и гипотезы; они не подтверждают распространённость проблемы во всём рынке.',root);
      node('p','Дата публикации и дата наблюдения хранятся отдельно. Фильтр периода применяется к публикации. Источники без этой даты исключаются при выбранном периоде. Старые материалы сохраняются с меньшим весом.',root);
      node('p','Проверяются ссылки на добавленные источники и точность цитат. Возможности требуют проверки экспериментом. Противоречия сохраняются в отчёте.',root);
      node('p',`Найдено ${report.methodology.counts.found} материалов. Дубликаты определяются по конкуренту и тексту. Максимум материалов для выбранной глубины: ${report.methodology.depth_limit}.`,root);
      report.evidence_gaps.forEach(text=>node('p',text,root)); return;
    }
    const items=report.insights.filter(item=>view==='all'||item.view===view);
    if (!items.length) node('p','Для этого представления пока недостаточно данных.',root);
    for (const insight of items) {
      const article=node('article',undefined,root); article.className='intel-source';
      node('h3',insight.statement,article);
      const impact={low:'низкое',medium:'среднее',high:'высокое'}, states={signal:'Сигнал',inference:'Гипотеза',conflict:'Противоречивые данные'};
      node('p',`${states[insight.state]} · Влияние: ${impact[insight.impact]} · Уверенность анализа: ${Math.round(insight.confidence*100)}% · Материалов: ${insight.evidence_count} · URL: ${insight.source_count}`,article);
      node('p',`Период: ${insight.period.from || 'неизвестен'} — ${insight.period.to || 'неизвестен'}; без даты: ${insight.period.unknown_dates}`,article);
      node('p',`Проверка: ${insight.suggested_test}`,article);
      insight.evidence_ids.forEach(id=>{ const row=evidence.find(item=>item.id===id); if(row) sourceDetails(row,article); });
      const actions=node('div',undefined,article); actions.className='intel-actions';
      for (const [target,title] of [['task','Создать задачу'],['content_hypothesis','Контент-гипотеза'],['offer_hypothesis','Гипотеза оффера']]) {
        const button=node('button',title,actions); button.type='button'; button.onclick=async()=>{
          button.disabled=true;
          try { const result=await api(`${base}/${runId}/export`,{insight_id:insight.id,target}); node('p',`Создано: ${result.task_id.slice(0,8)}`,article); const link=node('a','Открыть материал в студии',article); link.href=`/studio/products/${$('intel-product').value}/materials/${result.artifact_id}`; }
          catch(e) { error(e.message); } finally { button.disabled=false; }
        };
      }
    }
    if(view==='all') report.evidence_gaps.forEach(text=>node('p',text,root));
  }
  async function watch(id) {
    if(stream) stream.close(); if(pollTimer) clearTimeout(pollTimer);
    const current=++generation; runId=id; report=null; evidence=[]; $('intel-report').hidden=true; error();
    const url=new URL(location.href); url.searchParams.set('product_id',$('intel-product').value); url.searchParams.set('run_id',id); window.history.replaceState({},'',url);
    async function refresh() {
      const state=await api(`${base}/${id}`); if(current!==generation) return;
      progress(state);
      if (!['queued','running'].includes(state.status)) {
        if(stream) stream.close(); if(pollTimer) clearTimeout(pollTimer);
        if(state.report_artifact_id) { const values=await Promise.all([api(`${base}/${id}/report`),api(`${base}/${id}/evidence`)]); if(current!==generation) return; [report,evidence]=values; $('intel-report').hidden=false; render(); }
        else if(state.evidence_artifact_id) { evidence=await api(`${base}/${id}/evidence`); $('intel-plan').replaceChildren(); node('h3','Сохранённые источники',$('intel-plan')); evidence.forEach(row=>sourceDetails(row,$('intel-plan'))); }
        await history(); return true;
      }
      return false;
    }
    if(await refresh()) return;
    stream=new EventSource(`${base}/${id}/events`);
    stream.onmessage=()=>refresh().catch(e=>error(e.message));
    stream.addEventListener('done',()=>refresh().catch(e=>error(e.message)));
    stream.onerror=()=>{ stream.close(); async function poll(){ try{if(await refresh()) return;}catch(e){error(e.message);} if(current===generation) pollTimer=setTimeout(poll,3000); } pollTimer=setTimeout(poll,1000); };
  }
  $('intel-add-source').onclick=addSource;
  $('intel-quick').onclick=e=>{ const mode=e.target.dataset.mode; if(mode){$('intel-mode').value=mode; if(!$('intel-brief').value) $('intel-brief').value={weaknesses:'Найти повторяющиеся слабости конкурентов и предложить проверку оффера',pricing:'Сравнить цены и состав предложений конкурентов',reviews:'Проанализировать темы, жалобы и положительные отзывы конкурентов'}[mode];} };
  $('intel-tabs').onclick=e=>{ if(e.target.dataset.view&&report) render(e.target.dataset.view); };
  $('intel-product').onchange=()=>{ if(stream) stream.close(); if(pollTimer) clearTimeout(pollTimer); generation++; runId=null; $('intel-report').hidden=true; $('intel-progress').hidden=true; const url=new URL(location.href); url.searchParams.set('product_id',$('intel-product').value); url.searchParams.delete('run_id'); window.history.replaceState({},'',url); history().catch(e=>error(e.message)); };
  $('intel-form').onsubmit=async e=>{
    e.preventDefault(); error(); $('intel-submit').disabled=true;
    try { const state=await api(base,{product_id:$('intel-product').value,brief:$('intel-brief').value,mode:$('intel-mode').value,region:$('intel-region').value,depth:$('intel-depth').value,date_from:$('intel-from').value||null,date_to:$('intel-to').value||null,competitors:$('intel-competitors').value.split('\n').map(s=>s.trim()).filter(Boolean),source_bundle:suppliedSources()}); await history(); await watch(state.id); }
    catch(e){error(e.message);} finally{$('intel-submit').disabled=false;}
  };
  $('intel-followup').onsubmit=async e=>{e.preventDefault(); if(!runId)return; const button=e.target.querySelector('button'); button.disabled=true; try{const state=await api(`${base}/${runId}/follow-up`,{brief:$('intel-followup-brief').value}); await watch(state.id);}catch(e){error(e.message);}finally{button.disabled=false;}};
  history().catch(e=>error(e.message));
  const saved=new URLSearchParams(location.search).get('run_id'); if(saved) watch(saved).catch(e=>error(e.message));
})();
"""
