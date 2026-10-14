(function(){
'use strict';
var token='', state=null, preview=null, dirty=false, available={};
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
 rows.forEach(function(row){
  var option=element('option','',row[1]);option.value=row[0];
  option.selected=row[0]===value;select.appendChild(option);
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
function enabledModels(){return state.config.profiles.filter(function(item){return item.enabled;});}
function possibleModels(current,defaultLabel){
 var values=[['',defaultLabel]];
 enabledModels().forEach(function(model){values.push([model.model,model.model]);});
 if(current&&!values.some(function(row){return row[0]===current;})){
  values.push([current,current+' (отключена)']);
 }
 return values;
}
function setOverride(role,phase,main,fallback){
 var key=phase==='critic'?role+':critic':role;
 var values=main?[main]:[];
 if(phase==='main'&&main&&fallback&&fallback!==main)values.push(fallback);
 if(values.length)state.config.overrides[key]=values;
 else delete state.config.overrides[key];
 markDirty();
}
function roleCard(role){
 var bad=role.main.status!=='ready'||role.critic.status!=='ready';
 var card=element('article','card'+(bad?' unavailable':''));
 var head=element('div','card-title'),title=element('div');
 title.appendChild(element('h3','',role.name));
 title.appendChild(element('small','',role.description));
 head.appendChild(title);
 var labels={economy:'Эконом',balanced:'Баланс',premium:'Премиум'};
 head.appendChild(element('span','badge '+role.main.tier,labels[role.main.tier]));
 card.appendChild(head);
 var own=state.config.overrides[role.id]||[],review=state.config.overrides[role.id+':critic']||[];
 var main=own[0]||'',fallback=own[1]||'',critic=review[0]||'';
 var grid=element('div','card-grid');
 grid.appendChild(field('Основная модель',dropdown(main,possibleModels(main,'Автоматически'),function(value){
  main=value;if(!main)fallback='';
  setOverride(role.id,'main',main,fallback);renderRoles();
 })));
 var back=dropdown(fallback,possibleModels(fallback,'Без резервной'),function(value){
  fallback=value;setOverride(role.id,'main',main,fallback);
 });
 back.disabled=!main;
 grid.appendChild(field('Резервная модель',back));
 grid.appendChild(field('Модель критика',dropdown(critic,possibleModels(critic,'Автоматически'),function(value){
  critic=value;setOverride(role.id,'critic',critic,'');
 }),'critic'));
 card.appendChild(grid);
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
  ],function(v){model.tier=v;markDirty();})));
  ['input_usd_per_million','output_usd_per_million'].forEach(function(key,index){
   var input=element('input');input.type='text';input.inputMode='decimal';
   input.placeholder='Неизвестно';input.value=model[key]===undefined||model[key]===null?'':String(model[key]);
   input.addEventListener('change',function(){model[key]=input.value.trim()||null;markDirty();});
   row.appendChild(field(index?'Выход / 1 млн токенов, USD':'Вход / 1 млн токенов, USD',input));
  });
  var checkbox=element('input');checkbox.type='checkbox';checkbox.checked=!!model.enabled;
  checkbox.addEventListener('change',function(){model.enabled=checkbox.checked;markDirty();renderModels();});
  var label=element('label','model-enable');
  label.appendChild(checkbox);label.appendChild(element('span','','Разрешена'));
  row.appendChild(label);$('modelRows').appendChild(row);
 });
 $('statModels').textContent=enabledModels().length;
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
async function load(){
 var result=await api('/api/admin/team');
 state={revision:result.revision,config:result.config,products:result.products};
 preview=result.preview;dirty=false;available={};renderAll();
 $('authOverlay').hidden=true;$('error').hidden=true;
}
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
  input_usd_per_million:null,output_usd_per_million:null,web_search_verified:false});
 $('newModel').value='';markDirty();renderModels();
 alertMessage('Модель добавлена выключенной; проверьте доступность и тариф.',false);
});
async function previewChanges(){
 try{
  var result=await api('/api/admin/team/preview','POST',state.config);
  preview=result;renderRoles();renderOverview();
  alertMessage(result.ready?'Основные маршруты настроены. Это не проверка генерации.':
   'Не готовы маршруты: '+result.issues.length+'. Детали показаны в карточках.',!result.ready);
  return result;
 }catch(error){errorMessage(error);return null;}
}
$('previewButton').addEventListener('click',previewChanges);
$('saveButton').addEventListener('click',async function(){
 var result=await previewChanges();if(!result)return;
 if(state.config.enabled&&!result.ready){
  alertMessage('Нельзя включить роутер с незавершёнными маршрутами.',true);return;
 }
 try{
  var saved=await api('/api/admin/team','PUT',{
   expected_revision:state.revision,config:state.config
  });
  state.revision=saved.revision;state.config=saved.config;preview=saved.preview;
  dirty=false;renderAll();
  alertMessage('Сохранено: будущие вызовы получат новую конфигурацию.',false);
 }catch(error){errorMessage(error);}
});
$('catalogButton').addEventListener('click',async function(){
 $('catalogButton').disabled=true;
 try{
  var result=await api('/api/admin/team/catalog');
  available={};result.models.forEach(function(m){available[m.model]=m.listed_by_gateway;});
  renderModels();$('catalogResult').textContent=
   'Моделей в ответе API: '+result.listed_count+
   '. Новые ID (до 100): '+(result.unconfigured_ids.join(', ')||'нет')+
   '. Живой поиск не проверен, платная генерация не запускалась.';
 }catch(error){errorMessage(error);}
 finally{$('catalogButton').disabled=false;}
});
$('usageButton').addEventListener('click',async function(){
 var id=$('productSelect').value;
 if(!id){alertMessage('Нет выбранного проекта.',true);return;}
 try{
  var data=await api('/api/admin/team/usage?product_id='+encodeURIComponent(id));
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
load().catch(errorMessage);
})();