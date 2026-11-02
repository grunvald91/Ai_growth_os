/* Create is a second entry to the existing dispatcher, not a second STT implementation. */
(function(){
  'use strict';
  const screen=document.querySelector('.sg-create-screen');
  if(!screen)return;
  const root=screen.querySelector('.sg-create-dispatch');
  const composer=document.getElementById('dp-input');
  const platform=document.getElementById('sg-create-platform');
  const count=document.getElementById('sg-create-count');
  const platformRow=document.getElementById('sg-create-platform-row');
  const countRow=document.getElementById('sg-create-count-row');
  const modelRow=document.getElementById('sg-create-model-row');
  const aspectRow=document.getElementById('sg-create-aspect-row');
  const storyRatio=document.getElementById('sg-create-story-ratio');
  const aspect=document.getElementById('sg-create-aspect');
  const channels={
    post:[['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['vc_ru','vc.ru'],['tenchat','TenChat']],
    story:[['telegram','Telegram Stories'],['vk','Истории ВК'],['instagram_reels','Instagram Stories']],
    short_video:[['youtube_shorts','YouTube Shorts'],['instagram_reels','Instagram Reels'],['vk_clips','VK Клипы']],
    static_image:[['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['vc_ru','vc.ru'],['tenchat','TenChat'],['instagram_reels','Instagram']],
    package:[]
  };
  const defaults={post:'telegram',story:'telegram',short_video:'youtube_shorts',
    static_image:'telegram',package:''};
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
    platformRow.hidden=!channels[key] || channels[key].length===0;
    countRow.hidden=key!=='story';
    modelRow.hidden=key!=='story' && key!=='static_image';
    aspectRow.hidden=key!=='static_image';
    storyRatio.hidden=key!=='story';
    if(key!=='static_image') aspect.value='';
    const previous=platform.value;
    platform.replaceChildren();
    (channels[key]||[]).forEach(([value,label])=>{
      const option=document.createElement('option');option.value=value;option.textContent=label;
      platform.append(option);
    });
    if(channels[key] && channels[key].some(([value])=>value===previous)) platform.value=previous;
    else platform.value=defaults[key]||'';
    const names={post:'Пост',story:'Stories',static_image:'Изображение',
      short_video:'Видеосценарий',whiteboard:'Анимированное видео',package:'Полный пакет'};
    const context=document.getElementById('sg-create-context');
    context.textContent='Выбрано: '+names[key]+'. Опишите тему в нижней строке.';
    const advanced=document.getElementById('sg-create-advanced');
    if(advanced) advanced.hidden=key==='whiteboard';
    if(key==='whiteboard'){
      const link=document.createElement('a');link.href=base+'/whiteboard';
      link.textContent='Подробные настройки видео →';
      context.append(document.createTextNode(' '),link);
    }
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
  // Only the lower dispatcher composer can submit a request.
  // Settings are read at send time, including immediately after voice transcription.
})();
