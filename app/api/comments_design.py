"""Use the existing Growth Studio shell, tokens and controls."""
COMMENTS_CSS = """
#comments-app{font-size:16px;line-height:1.55;color:var(--sg-text);min-width:0}
#comments-app h2{font-size:20px;margin:0 0 12px;color:var(--sg-navy)}
#comments-app h3{font-size:17px;margin:22px 0 8px}
#comments-app p{font-size:14px;color:var(--sg-muted)}
#comments-app .btn{font-size:14px;min-height:42px;white-space:normal}
#comments-app .chip{font-size:13px;padding:6px 10px}
.c-tabs,.c-actions,.c-filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:20px}
.c-tabs{border-bottom:1px solid var(--sg-line);padding-bottom:16px}
.c-notice{background:#e9f4ff;border:1px solid #d1e7ff;padding:14px 18px;border-radius:14px;font-size:14px;margin-bottom:22px}
.c-inbox-grid,.c-agent-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,1fr);gap:22px;align-items:start}
.c-platform-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.c-list{display:grid;gap:18px;min-width:0}
.c-card,.c-platform{padding:24px;min-width:0}
.c-card-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.c-card-head h2{margin:0!important}
#comments-app label{display:grid;gap:7px;font-size:14px;font-weight:650;margin:14px 0}
#comments-app input:not([type=checkbox]),#comments-app textarea,#comments-app select{display:block;width:100%;border:1px solid var(--sg-line);background:#f9fbfe;border-radius:11px;padding:11px 13px;font:inherit;font-size:16px;color:var(--sg-text);box-sizing:border-box}
#comments-app textarea{resize:vertical;min-height:92px}
#comments-app input:focus,#comments-app textarea:focus,#comments-app select:focus{outline:2px solid var(--sg-blue);outline-offset:2px}
#comments-app .c-check{display:flex;align-items:center;font-weight:500}
.c-check input{width:18px;height:18px;accent-color:var(--sg-blue)}
.c-fields{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:center}
.c-comment-text{white-space:pre-wrap;overflow-wrap:anywhere;font-size:16px!important;color:var(--sg-text)!important}
.c-reason,.c-meta{font-size:13px!important}
.c-warning{color:#945411!important;background:#fff5e4;padding:12px;border-radius:10px}
.c-empty{padding:44px 28px;text-align:center}
.c-test{max-width:820px}
.c-platform ol{padding-left:22px;font-size:14px;color:var(--sg-text)}
.c-platform li{margin-bottom:10px}
.c-platform a:not(.btn){color:var(--sg-blue);font-size:14px}
.c-knowledge{padding-left:18px;font-size:14px}
.c-knowledge pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;max-height:220px;overflow:auto;background:#f4f8fd;padding:12px;border-radius:10px}
#comments-app details summary{font-size:14px;cursor:pointer;color:var(--sg-blue)}
#c-feedback:empty{display:none}
#c-feedback{padding:12px 16px;border-radius:12px;background:#e9f4ff;color:var(--sg-text)!important}
#c-feedback.error{background:#fff1ef;color:#a53227!important}
#c-test-result:not(:empty){border-top:1px solid var(--sg-line);margin-top:24px;padding-top:20px}

@media(max-width:1000px){.c-inbox-grid,.c-agent-grid{grid-template-columns:1fr}.c-platform-grid{grid-template-columns:1fr}}
@media(max-width:600px){.c-card,.c-platform{padding:18px}.c-tabs{gap:8px}.c-tabs .btn{flex:1 1 42%;text-align:center}.c-fields{grid-template-columns:1fr;gap:0}.c-filters label{flex:1 1 42%}.c-card .btn{margin:4px 0}.c-inbox-grid{gap:16px}}
#comments-app p{line-height:1.7;margin:12px 0 20px}
#comments-app .btn{display:inline-flex;align-items:center;justify-content:center;text-align:center;padding:12px 18px;line-height:1.4;min-height:46px}
#comments-app .c-tabs{gap:6px;padding:6px;background:#eaf0f7;border:0;border-radius:14px;margin-bottom:24px}
#comments-app .c-tabs .btn{box-shadow:none;border:0;background:transparent;color:var(--sg-muted);flex:1}
#comments-app .c-tabs .btn.primary{background:#fff;color:var(--sg-blue-600);box-shadow:0 2px 6px rgba(20,40,70,.06)}
.c-connect{padding-top:20px;margin-top:24px;border-top:1px solid var(--sg-line)}
#comments-app .c-connect label{margin:0 0 18px}
.c-connect .btn{width:100%;margin-top:6px}
.c-platform .c-actions{margin:16px 0 0;gap:12px}
.c-platform ol{margin:16px 0 24px;padding-left:24px}
.c-platform li{padding-left:6px;margin-bottom:18px;line-height:1.7}
.c-step-links{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.c-step-links a{display:inline-flex;align-items:center;min-height:36px;text-decoration:underline;text-underline-offset:3px;overflow-wrap:anywhere}
.c-platform .c-docs{display:inline-flex;min-height:40px;align-items:center;text-decoration:underline;text-underline-offset:4px}
.c-platform .c-meta{margin-bottom:16px!important}
.c-guide{border:1px solid var(--sg-line);border-radius:12px;padding:14px 16px;margin:18px 0}
#comments-app .c-guide summary{font-weight:650;line-height:1.6;min-height:28px}
.c-platform-intro{padding:24px;margin-bottom:24px}
.c-platform-intro h2{margin-bottom:8px!important}
.c-platform-intro p:last-child{margin-bottom:0!important}
.c-help{display:block;font-size:12px;font-weight:450;color:var(--sg-muted);line-height:1.6}
#comments-app .c-fields{gap:18px}
@media(max-width:600px){#comments-app .c-tabs .btn{flex:1 1 42%;font-size:13px}.c-platform-intro{padding:18px}.c-connect .btn{width:100%}.c-platform .chip{font-size:11px}}

"""

COMMENTS_JS = r"""
(() => {
  const root = document.getElementById('comments-app'); if (!root) return;
  const feedback = document.getElementById('c-feedback');
  const say = (text, error=false) => {feedback.textContent=text; feedback.classList.toggle('error',error);};
  async function api(path, data={}) {
    const response = await fetch(root.dataset.api + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    let result; try {result=await response.json();} catch {throw Error('Сервер не подтвердил действие. Повторите позже.');}
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Проверьте заполнение полей');
    return result;
  }
  async function run(control, work) {
    if (control.disabled) return; control.disabled=true; say('Выполняется…');
    try {await work();} catch (error) {say(error.message, true);} finally {control.disabled=false;}
  }
  root.addEventListener('submit', event => {
    const form=event.target; if (!form.matches('#c-settings,#c-manual,#c-test,.c-connect')) return;
    event.preventDefault(); const values=Object.fromEntries(new FormData(form)); const button=form.querySelector('button[type=submit]');
    run(button, async () => {
      if (form.id==='c-settings') {
        values.max_length=Number(values.max_length); values.emoji=form.elements.emoji.checked; values.enabled=form.elements.enabled.checked;
        values.facts=values.facts.split('\n').map(x=>x.trim()).filter(Boolean).map((text,i)=>({id:'owner.'+(i+1),text}));
        const data=await api('/settings',values); say('Настройки сохранены. Версия '+data.version+'.');
      } else if (form.classList.contains('c-connect')) {
        values.platform=form.dataset.platform;
        try {await api('/connect',values);} finally {form.elements.token.value=''; values.token='';}
        location.reload();
      } else if (form.id==='c-manual') {
        await api('/manual',values); location.reload();
      } else {
        const data=await api('/test',values), result=document.getElementById('c-test-result'); result.replaceChildren();
        const heading=document.createElement('h3'); heading.textContent=data.action==='human_review'?'Ответ для проверки человеком':'Черновик ответа'; result.append(heading);
        const reply=document.createElement('p'); reply.className='c-comment-text'; reply.textContent=data.reply || 'Комментарий предлагается пропустить.'; result.append(reply);
        const reason=document.createElement('p'); reason.textContent=data.reason; result.append(reason);
        if(data.missing_information.length){const missing=document.createElement('p'); missing.textContent='Нужно уточнить: '+data.missing_information.join('; '); result.append(missing);}
        const source=document.createElement('p'); source.textContent='Источники: '+(data.used_fact_ids.join(', ')||'Нет подтверждённых фактов'); result.append(source); say('Пробный ответ готов. Ничего не опубликовано.');
      }
    });
  });
  root.addEventListener('click', event => {
    const button=event.target.closest('button'); if(!button) return;
    if(button.dataset.copyReply){run(button,async()=>{await navigator.clipboard.writeText(document.getElementById(button.dataset.copyReply).value);say('Ответ скопирован.');});return;}
    if(button.dataset.sync){run(button,async()=>{const data=await api('/channels/'+button.dataset.sync+'/sync');say('Загружено новых комментариев: '+data.imported+'.'); if(data.imported)location.href=location.pathname+'?tab=inbox';});return;}
    if(button.dataset.disconnect){if(!confirm('Отключить площадку? Сохранённый токен будет удалён.'))return;run(button,async()=>{await api('/channels/'+button.dataset.disconnect+'/disconnect');location.reload();});return;}
    if(button.dataset.draft){run(button,async()=>{await api('/items/'+button.dataset.draft+'/draft');location.reload();});return;}
    if(button.dataset.closeComment){run(button,async()=>{await api('/items/'+button.dataset.closeComment+'/'+button.dataset.state);location.reload();});return;}
    if(button.dataset.send){
      const reply=document.getElementById(button.dataset.textId).value;
      if(!confirm('Опубликовать этот ответ на площадке?\n\n'+reply)) return;
      run(button,async()=>{await api('/items/'+button.dataset.send+'/send',{reply});location.reload();});
    }
  });
})();
"""
