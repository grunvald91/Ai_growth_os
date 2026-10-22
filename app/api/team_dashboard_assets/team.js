(function(){
'use strict';
// Same prefix as the page: the browser resends the Studio login only under /studio/.
var API='/studio/team/api';
var token='', state=null, preview=null, dirty=false, available={}, catalog=null;
// Controls that change or read the loaded configuration.
var CONTROLS=['previewButton','saveButton','enableRouter','budget','addModel','catalogButton','usageButton'];
var $=function(id){return document.getElementById(id);};
var asText=function(node,value){node.textContent=String(value);return node;};
function element(tag,cls,text){
 var node=document.createElement(tag);
 if(cls)node.className=cls;
 if(text!==undefined)asText(node,text);
 return node;
}
function alertMessage(message,error){
 $('notice').hidden=!!error;$('error').hidden=!error;
 asText($(error?'error':'notice'),message);
}
function errorMessage(error){
 if(error&&error.status===401){token='';$('authOverlay').hidden=false;}
 alertMessage(error.message||'Ошибка запроса',true);
}
async function api(path,method,body){
 var headers={'Accept':'application/json'};
 if(token)headers.Authorization='Bearer '+token;
 if(body!==undefined)headers['Content-Type']='application/json';
 var response=await fetch(path,{method:method||'GET',headers:headers,
   body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
 var data={};try{data=await response.json();}catch(e){}
 if(!response.ok){
  var detail=data.detail, message=typeof detail==='string'?detail:
    (detail&&detail.message)||'Ошибка: '+response.status;
  if(detail&&Array.isArray(detail.issues))message+=': '+detail.issues.slice(0,3).join('; ');
  var err=new Error(message);err.status=response.status;throw err;
 }
 return data;
}
function dropdown(value,rows,handler){
 var select=element('select');
 function add(parent,row){
  var option=element('option','',row[1]);option.value=row[0];
  option.selected=row[0]===value;parent.appendChild(option);
 }
 rows.forEach(function(row){
  if(row.group){
   var group=element('optgroup');group.label=row.group;
   row.rows.forEach(function(item){add(group,item);});
   if(row.rows.length)select.appendChild(group);
  }else add(select,row);
 });
 select.addEventListener('change',function(){handler(select.value);});
 return select;
}
function field(label,node,className){
 var wrapper=element('label',className||'');
 wrapper.appendChild(element('span','',label));wrapper.appendChild(node);return wrapper;
}
function markDirty(){
 dirty=true;$('dirty').hidden=false;$('statIssues').textContent='Проверить';
 $('statEnabled').textContent=state.config.enabled?'Включён*':'Выключен';
 $('notice').hidden=true;
}
// A profile without the field is enabled, exactly as the router reads it.
function isEnabled(item){return item.enabled!==false;}
function enabledModels(){return state.config.profiles.filter(isEnabled);}
var TIER_NAMES={economy:'Эконом',balanced:'Баланс',premium:'Премиум'};
function profileFor(id){return state.config.profiles.filter(function(p){return p.model===id;})[0];}
// Every model the gateway lists can be chosen: one that is not in the team's catalog yet is
// added (enabled, with a suggested class) the moment it is picked.
function possibleModels(current,defaultLabel){
 var rows=[['',defaultLabel]],mine=[],others=[],rest=[],seen={};
 enabledModels().forEach(function(model){mine.push([model.model,model.model]);seen[model.model]=1;});
 if(current&&!seen[current]){
  var known=profileFor(current);
  mine.push([current,current+(known?' (отключена)':' (нет в каталоге)')]);seen[current]=1;
 }
 ((catalog&&catalog.gateway_models)||[]).forEach(function(m){
  if(seen[m.model])return;
  (m.text?others:rest).push([m.model,m.model+(profileFor(m.model)?' (отключена)':'')]);seen[m.model]=1;
 });
 rows.push({group:'В каталоге команды',rows:mine});
 rows.push({group:'Доступны в ClaudeHub — добавятся при выборе',rows:others});
 rows.push({group:'Прочие модели ClaudeHub (картинки, голос и др.)',rows:rest});
 return rows;
}
function ensureProfile(id){
 if(!id)return;
 var known=profileFor(id);
 if(known){
  if(!isEnabled(known)){known.enabled=true;alertMessage('Модель '+id+' включена. Проверьте тариф на вкладке «Модели и тарифы».',false);}
  return;
 }
 if(state.config.profiles.length>=100){alertMessage('Достигнут предел в 100 моделей каталога команды.',true);return;}
 var info=((catalog&&catalog.gateway_models)||[]).filter(function(m){return m.model===id;})[0];
 var tier=info?info.suggested_tier:'balanced';
 state.config.profiles.push({model:id,tier:tier,enabled:true,input_usd_per_million:null,
  output_usd_per_million:null,web_search_verified:false,priority:100});
 alertMessage('Модель '+id+' добавлена в каталог команды (класс: '+TIER_NAMES[tier]+
  ', тариф неизвестен). Изменить можно на вкладке «Модели и тарифы».',false);
}
// One role has up to three models in order: the main one and two reserves.
function setChain(key,chain){
 var values=[];
 chain.forEach(function(id){if(id&&values.indexOf(id)<0&&values.length<3)values.push(id);});
 if(values.length)state.config.overrides[key]=values;else delete state.config.overrides[key];
 markDirty();schedulePreview();
}
function roleCard(role){
 var bad=role.main.status!=='ready'||role.critic.status!=='ready';
 var card=element('article','card'+(bad?' unavailable':''));
 var head=element('div','card-title'),title=element('div');
 title.appendChild(element('h3','',role.name));
 title.appendChild(element('small','',role.description));
 head.appendChild(title);
 head.appendChild(element('span','badge '+role.main.tier,TIER_NAMES[role.main.tier]));
 card.appendChild(head);
 var mainKey=role.id,criticKey=role.id+':critic';
 var mainChain=(state.config.overrides[mainKey]||[]).slice(),criticChain=(state.config.overrides[criticKey]||[]).slice();
 function picker(label,chain,index,key,defaultLabel,className){
  var current=chain[index]||'';
  var node=dropdown(current,possibleModels(current,defaultLabel),function(value){
   ensureProfile(value);
   chain[index]=value;
   // "Automatically" in the first slot hands the whole chain back to the router.
   if(index===0&&!value)chain.length=0;
   setChain(key,chain.filter(Boolean));renderRoles();renderModels();
  });
  if(index>0&&!chain[0])node.disabled=true;
  return field(label,node,className);
 }
 var grid=element('div','card-grid');
 grid.appendChild(picker('Основная модель',mainChain,0,mainKey,'Автоматически'));
 grid.appendChild(picker('Модель критика',criticChain,0,criticKey,'Автоматически'));
 card.appendChild(grid);
 var reserve=element('details','reserve');
 reserve.open=mainChain.length>1||criticChain.length>1;
 reserve.appendChild(element('summary','','Резервные модели (если основная не отвечает)'));
 var reserveGrid=element('div','card-grid');
 reserveGrid.appendChild(picker('Резервная 1 — основная',mainChain,1,mainKey,'Без резервной'));
 reserveGrid.appendChild(picker('Резервная 1 — критик',criticChain,1,criticKey,'Без резервной'));
 reserveGrid.appendChild(picker('Резервная 2 — основная',mainChain,2,mainKey,'Без резервной'));
 reserveGrid.appendChild(picker('Резервная 2 — критик',criticChain,2,criticKey,'Без резервной'));
 reserve.appendChild(reserveGrid);card.appendChild(reserve);
 var note=element('div','card-summary');
 var mainText=role.main.status==='ready'?role.main.models.join(' → '):role.main.reason;
 var criticText=role.critic.status==='ready'?role.critic.models.join(' → '):role.critic.reason;
 note.appendChild(element('strong',bad?'bad-note':'','Основная: '+mainText));
 note.appendChild(element('span','','· Критик: '+criticText));
 card.appendChild(note);return card;
}
function renderRoles(){
 if(!state||!preview)return;
 $('roleGroups').replaceChildren();
 var query=$('roleSearch').value.toLocaleLowerCase('ru').trim();
 var groups={};
 preview.roles.forEach(function(role){
  var hay=(role.name+' '+role.id+' '+role.department).toLocaleLowerCase('ru');
  if(query&&!hay.includes(query))return;
  if(!groups[role.department])groups[role.department]=[];
  groups[role.department].push(role);
 });
 Object.keys(groups).forEach(function(department){
  var group=element('section','department'),heading=element('h3','department-title');
  heading.appendChild(element('span','',department));
  heading.appendChild(element('span','department-count',groups[department].length+' ролей'));
  group.appendChild(heading);
  var cards=element('div','cards');
  groups[department].forEach(function(role){cards.appendChild(roleCard(role));});
  group.appendChild(cards);$('roleGroups').appendChild(group);
 });
}
function renderModels(){
 if(!state)return;
 $('modelRows').replaceChildren();
 state.config.profiles.forEach(function(model){
  var row=element('article','model-row'),identity=element('div','identity');
  identity.appendChild(element('div','model-id',model.model));
  var status=available[model.model]===true?'Есть в каталоге API · поиск не проверен':
    available[model.model]===false?'Не найден в каталоге API':'Доступность не проверена';
  identity.appendChild(element('div','model-meta',status));row.appendChild(identity);
  row.appendChild(field('Класс',dropdown(model.tier,[
   ['economy','Эконом'],['balanced','Баланс'],['premium','Премиум']
  ],function(v){model.tier=v;markDirty();schedulePreview();})));
  var priority=element('input');priority.type='number';priority.min='1';priority.max='999';priority.step='1';
  priority.value=String(model.priority||100);
  priority.addEventListener('change',function(){var v=parseInt(priority.value,10);
   model.priority=(v>=1&&v<=999)?v:100;priority.value=String(model.priority);markDirty();schedulePreview();});
  row.appendChild(field('Приоритет (1 — первая)',priority));
  ['input_usd_per_million','output_usd_per_million'].forEach(function(key,index){
   var input=element('input');input.type='text';input.inputMode='decimal';
   input.placeholder='Неизвестно';input.value=model[key]===undefined||model[key]===null?'':String(model[key]);
   input.addEventListener('change',function(){model[key]=input.value.trim()||null;markDirty();});
   row.appendChild(field(index?'Выход / 1 млн токенов, USD':'Вход / 1 млн токенов, USD',input));
  });
  var checkbox=element('input');checkbox.type='checkbox';checkbox.checked=isEnabled(model);
  checkbox.addEventListener('change',function(){model.enabled=checkbox.checked;markDirty();renderModels();renderRoles();schedulePreview();});
  var label=element('label','model-enable');
  label.appendChild(checkbox);label.appendChild(element('span','','Разрешена'));
  row.appendChild(label);$('modelRows').appendChild(row);
 });
 $('statModels').textContent=enabledModels().length;
 renderCatalogAdd();
}
function applyCatalog(result){
 catalog=result;available={};
 result.models.forEach(function(m){available[m.model]=m.listed_by_gateway;});
}
function renderCatalogAdd(){
 var box=$('catalogAdd');box.replaceChildren();
 if(!catalog){box.appendChild(element('p','empty','Каталог ClaudeHub не загружен. Нажмите «Проверить ClaudeHub».'));return;}
 var fresh=catalog.gateway_models.filter(function(m){return !profileFor(m.model);});
 var text=fresh.filter(function(m){return m.text;});
 box.appendChild(element('p','catalog-result','В ClaudeHub '+catalog.listed_count+' моделей, в каталоге команды '+
  state.config.profiles.length+'. Не добавлено: '+fresh.length+' (текстовых: '+text.length+').'));
 if(!fresh.length)return;
 var all=element('button','button light','+ Добавить все текстовые ('+text.length+')');all.type='button';
 all.disabled=!text.length;
 all.addEventListener('click',function(){text.forEach(function(m){ensureProfile(m.model);});markDirty();renderModels();renderRoles();schedulePreview();});
 box.appendChild(all);
 var list=element('div','fresh-list');
 fresh.forEach(function(m){
  var item=element('button','chip'+(m.text?'':' other'),'+ '+m.model);item.type='button';
  item.title=m.text?'Добавить в каталог команды':'Не похоже на текстовую модель, но добавить можно';
  item.addEventListener('click',function(){ensureProfile(m.model);markDirty();renderModels();renderRoles();schedulePreview();});
  list.appendChild(item);
 });
 box.appendChild(list);
}
async function loadCatalog(manual){
 $('catalogButton').disabled=true;
 try{
  applyCatalog(await api(API+'/catalog'));
  renderModels();renderRoles();
  $('catalogResult').textContent='Живой поиск не проверен, платная генерация не запускалась.';
  if(manual)alertMessage('Каталог ClaudeHub обновлён: '+catalog.listed_count+' моделей.',false);
 }catch(error){
  if(manual)errorMessage(error);
  else $('catalogResult').textContent='Каталог ClaudeHub сейчас недоступен: в списках только модели каталога команды.';
 }finally{$('catalogButton').disabled=false;}
}
var previewTimer=null;
function schedulePreview(){
 clearTimeout(previewTimer);
 previewTimer=setTimeout(function(){previewChanges(true);},350);
}
function renderOverview(){
 $('statRoles').textContent=preview.roles.length;
 $('statModels').textContent=enabledModels().length;
 $('statIssues').textContent=preview.issues.length;
 $('statEnabled').textContent=state.config.enabled?'Включён':'Выключен';
 $('enableRouter').checked=state.config.enabled;
 $('budget').value=state.config.max_call_usd||'';
 $('dirty').hidden=!dirty;
}
function renderAll(){
 renderOverview();renderRoles();renderModels();
 var products=$('productSelect'),selected=products.value;
 products.replaceChildren();
 state.products.forEach(function(product){
  var opt=element('option','',product.name);
  opt.value=product.id;opt.selected=product.id===selected;products.appendChild(opt);
 });
}
function setReady(ready){
 CONTROLS.forEach(function(id){$(id).disabled=!ready;});
 $('retryButton').hidden=ready;
}
async function load(){
 var result=await api(API);
 state={revision:result.revision,config:result.config,products:result.products};
 preview=result.preview;dirty=false;available={};renderAll();setReady(true);
 $('authOverlay').hidden=true;$('error').hidden=true;
 loadCatalog(false);
}
function loadFailed(error){
 state=null;setReady(false);errorMessage(error);
 if(!$('authOverlay').hidden)return;
 alertMessage('Не удалось загрузить настройки: '+(error.message||'ошибка запроса')+'. Нажмите «Повторить».',true);
}
function reload(){setReady(false);load().catch(loadFailed);}
$('authForm').addEventListener('submit',async function(ev){
 ev.preventDefault();token=$('ownerToken').value;
 try{await load();$('ownerToken').value='';$('authError').textContent='';}
 catch(error){token='';$('authError').textContent=error.message||'Не удалось войти';}
});
$('roleSearch').addEventListener('input',renderRoles);
$('enableRouter').addEventListener('change',function(){state.config.enabled=this.checked;markDirty();});
$('budget').addEventListener('change',function(){state.config.max_call_usd=this.value.trim()||null;markDirty();});
document.querySelectorAll('.tab').forEach(function(button){
 button.addEventListener('click',function(){
  document.querySelectorAll('.tab').forEach(function(item){
   item.classList.toggle('selected',item===button);
  });
  ['team','models','expenses'].forEach(function(name){
   $(name+'Panel').hidden=button.dataset.tab!==name;
  });
 });
});
$('addModel').addEventListener('click',function(){
 var id=$('newModel').value.trim();
 if(!id||id.length>120||/\s/.test(id)||state.config.profiles.some(function(p){return p.model===id;})){
  alertMessage('Укажите уникальный ID модели без пробелов.',true);return;
 }
 if(state.config.profiles.length>=40){alertMessage('Достигнут предел в 40 моделей.',true);return;}
 state.config.profiles.push({model:id,tier:$('newTier').value,enabled:false,
  input_usd_per_million:null,output_usd_per_million:null,web_search_verified:false,priority:100});
 $('newModel').value='';markDirty();renderModels();renderRoles();schedulePreview();
 alertMessage('Модель добавлена выключенной; включите её и проверьте тариф.',false);
});
async function previewChanges(silent){
 try{
  var result=await api(API+'/preview','POST',state.config);
  preview=result;renderRoles();renderOverview();
  if(!silent||!result.ready){
   alertMessage(result.ready?'Основные маршруты настроены. Это не проверка генерации.':
    'Не готовы маршруты: '+result.issues.length+'. Детали показаны в карточках.',!result.ready);
  }
  return result;
 }catch(error){errorMessage(error);return null;}
}
$('previewButton').addEventListener('click',function(){previewChanges(false);});
$('saveButton').addEventListener('click',async function(){
 var result=await previewChanges(false);if(!result)return;
 if(state.config.enabled&&!result.ready){
  alertMessage('Нельзя включить роутер с незавершёнными маршрутами.',true);return;
 }
 try{
  var saved=await api(API,'PUT',{
   expected_revision:state.revision,config:state.config
  });
  state.revision=saved.revision;state.config=saved.config;preview=saved.preview;
  dirty=false;renderAll();
  alertMessage('Сохранено: будущие вызовы получат новую конфигурацию.',false);
 }catch(error){errorMessage(error);}
});
$('catalogButton').addEventListener('click',function(){loadCatalog(true);});
$('usageButton').addEventListener('click',async function(){
 var id=$('productSelect').value;
 if(!id){alertMessage('Нет выбранного проекта.',true);return;}
 try{
  var data=await api(API+'/usage?product_id='+encodeURIComponent(id));
  var amount=Number(data.estimated_usd);
  $('totalUsd').textContent=Number.isFinite(amount)?'$'+amount.toFixed(6):'—';
  $('totalCalls').textContent=data.total_calls;
  $('unpricedCalls').textContent=data.unpriced_calls;
  $('expenseRows').replaceChildren();
  Object.keys(data.by_role).forEach(function(role){
   var info=data.by_role[role],row=element('div','expense-row');
   row.appendChild(element('strong','',role));
   row.appendChild(element('span','',info.calls+' выз. · '+info.input_tokens+
    ' вход · '+info.output_tokens+' выход · '+info.unpriced_calls+' без цены'));
   row.appendChild(element('strong','','$'+Number(info.estimated_usd).toFixed(6)));
   $('expenseRows').appendChild(row);
  });
  if(!Object.keys(data.by_role).length){
   $('expenseRows').appendChild(element('p','empty','Пока нет зарегистрированных расходов.'));
  }
 }catch(error){errorMessage(error);}
});
$('retryButton').addEventListener('click',reload);
reload();
})();