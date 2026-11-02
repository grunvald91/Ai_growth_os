/* Create is a second entry to the existing dispatcher, not a second STT implementation. */
(function(){
  'use strict';
  const screen=document.querySelector('.sg-create-screen');
  if(!screen)return;
  const root=screen.querySelector('.sg-create-dispatch');
  const composer=document.getElementById('dp-input');
  const form=document.getElementById('sg-create-form');
  const panel=document.getElementById('sg-create-info-panel');
  const topics=document.getElementById('sg-create-suggestions');
  const base=root.getAttribute('data-api').replace(/\/dispatch$/, '');

  function info(title,detail){
    panel.replaceChildren();
    const row=document.createElement('div');row.className='sg-create-info-heading';
    const heading=document.createElement('strong');heading.textContent=title;
    const close=document.createElement('button');close.type='button';close.textContent='×';
    close.setAttribute('aria-label','Закрыть описание');close.onclick=()=>{panel.hidden=true;};
    row.append(heading,close);
    const text=document.createElement('p');text.textContent=detail;
    panel.append(row,text);panel.hidden=false;
  }
  screen.querySelectorAll('[data-sg-info]').forEach(button=>{
    button.addEventListener('click',()=>info(button.dataset.sgInfo,button.dataset.sgDescription));
  });
  screen.querySelector('[data-sg-about]').addEventListener('click',()=>{
    info('Как работает ИИ-команда',
      'Мы используем подтверждённый профиль вашей компании и доступные исследования аудитории, '+
      'затем стратег формирует подход, специалисты готовят материал, а проверка ищет проблемы качества. '+
      'Готовый результат появляется в заданиях для вашего просмотра. Публикация — отдельное действие.');
  });
  function chooseFormat(key){
    const selected=screen.querySelector('[data-sg-format="'+key+'"]');
    if(!selected)return;
    root.setAttribute('data-create-format',key);
    screen.querySelectorAll('[data-sg-format]').forEach(button=>{
      button.setAttribute('aria-pressed',button===selected?'true':'false');
    });
    const radio=form?.querySelector('input[name="format"][value="'+key+'"]');
    if(radio&&!radio.checked){radio.checked=true;radio.dispatchEvent(new Event('change',{bubbles:true}));}
    const count=form?.querySelector('.sg-story-count');
    if(count){count.hidden=key!=='story';count.querySelector('select').disabled=key!=='story';}
    const names={post:'Пост',story:'Stories',static_image:'Изображение',
      short_video:'Видеосценарий',whiteboard:'Анимированное видео',package:'Полный пакет'};
    document.getElementById('sg-create-context').textContent=
      'Выбрано: '+names[key]+'. Опишите тему в нижней строке или откройте подробности.';
  }
  screen.querySelectorAll('[data-sg-format]').forEach(button=>{
    button.addEventListener('click',()=>chooseFormat(button.dataset.sgFormat));
  });
  screen.querySelector('[data-sg-suggest]').addEventListener('click',async()=>{
    topics.hidden=false;topics.replaceChildren();
    const status=document.createElement('p');status.textContent='Подбираем темы из профиля компании…';topics.append(status);
    try{
      const response=await fetch(base+'/whiteboard/topics.json',{credentials:'same-origin',headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('Не удалось получить темы');
      const data=await response.json();
      topics.replaceChildren();
      const list=Array.isArray(data.topics)?data.topics:[];
      if(!list.length){
        const note=document.createElement('p');note.textContent=data.message||'В профиле пока нет данных для предложений.';
        topics.append(note);
        const link=document.createElement('a');link.href=base+'/profile';link.textContent='Дополнить профиль компании';
        topics.append(link);return;
      }
      const title=document.createElement('strong');title.textContent='Выберите тему';topics.append(title);
      list.forEach(item=>{
        if(typeof item.topic!=='string')return;
        const button=document.createElement('button');button.type='button';button.textContent=item.topic;
        button.addEventListener('click',()=>{
          composer.value=item.topic;
          composer.dispatchEvent(new Event('input',{bubbles:true}));
          topics.hidden=true;composer.focus();
        });
        topics.append(button);
      });
    }catch(error){
      status.textContent='Не удалось получить предложения. Вы можете задать тему самостоятельно.';
    }
  });
  if(form){
    form.classList.add('sg-create-js-ready');
    // The original form still has its own checked default, but it must not override speech.
    // No initial top-level format is forced; a free-form voice request chooses its own.
    const count=form.querySelector('.sg-story-count');
    if(count){count.hidden=true;count.querySelector('select').disabled=true;}
    form.querySelectorAll('input[name="format"]').forEach(radio=>{
      radio.addEventListener('change',()=>{
        if(radio.checked&&radio.value!=='article'&&radio.value!=='whiteboard')
          chooseFormat(radio.value);
      });
    });
  }
})();
