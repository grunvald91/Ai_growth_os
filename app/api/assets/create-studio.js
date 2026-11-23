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
  const vkImageSlot=document.getElementById('sg-create-vk-image-slot');
  const vkRatioRow=document.getElementById('sg-create-vk-ratio-row');
  const vkReviewTip=document.getElementById('sg-create-vk-review-tip');
  const vkImageRadios=screen.querySelectorAll('[name="vk_image_enabled"]');
  const vkModelChoice=document.getElementById('sg-create-vk-model-choice');
  const vkModelChoiceRow=document.getElementById('sg-create-vk-model-choice-row');
  const summary=document.getElementById('sg-create-review-summary');
  const formatOptions=document.getElementById('sg-create-format-options');
  const formatOptionsSlot=document.getElementById('sg-create-format-options-slot');
  const extrasSlot=document.getElementById('sg-create-extra-settings-slot');
  const advanced=document.getElementById('sg-create-advanced');
  // The image generation/critic cost note must accompany the visible VK option.
  if(vkImageSlot && vkReviewTip)vkImageSlot.appendChild(vkReviewTip);
  const aspectRow=document.getElementById('sg-create-aspect-row');
  const storyRatio=document.getElementById('sg-create-story-ratio');
  // All format-affecting controls precede the topic and submit button on
  // narrow screens. Optional wishes stay in the same form, before submit.
  if(formatOptionsSlot){
    [countRow,modelRow,aspectRow,vkRatioRow,storyRatio].forEach(row=>{
      if(row)formatOptionsSlot.appendChild(row);
    });
  }
  if(extrasSlot && advanced)extrasSlot.appendChild(advanced);
  // The destination platform belongs next to the selected format, not in advanced settings.
  const platformSlot=document.getElementById('sg-create-platform-slot');
  if(platformSlot && platformRow)platformSlot.appendChild(platformRow);
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
  let topicRequest=null;
  function cancelTopicRequest(){
    if(topicRequest)topicRequest.abort();
    topicRequest=null;
  }
  const suggestRow=screen.querySelector('.sg-create-suggest-row');
  // Both controls and their help now live next to the editor in source order.
  // Suggestions are inserted in the same place so Tab order matches layout.
  if(suggestRow && topics)suggestRow.after(topics);
  const base=root.getAttribute('data-api').replace(/\/dispatch$/, '');
  const platformStatus=document.getElementById('sg-create-platform-status');
  let savedConnections=null;
  function refreshPlatformStatus(){
    if(!platformStatus)return;
    const state=savedConnections && savedConnections[platform.value] &&
      savedConnections[platform.value].state;
    platformStatus.textContent=state==='configured'
      ? 'Подключение сохранено · доступ проверим при публикации.'
      : state==='not_configured'
        ? 'Публикация не подключена · черновик доступен.'
        : 'Статус публикации неизвестен · черновик доступен.';
  }
  fetch(base+'/create/platforms.json',{credentials:'same-origin',
    headers:{Accept:'application/json'}}).then(response=>{
    if(!response.ok)throw new Error('not available');
    return response.json();
  }).then(data=>{savedConnections=data;refreshPlatformStatus();})
    .catch(()=>refreshPlatformStatus());

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
  function imageEnabled(){
    const checked=screen.querySelector('[name="vk_image_enabled"]:checked');
    return !checked || checked.value==='yes';
  }
  function updateSummary(){
    if(!summary)return;
    const names={post:'Пост',story:'Stories',static_image:'Изображение',
      short_video:'Видеосценарий',whiteboard:'Простое видео MP4',package:'Контент-пакет'};
    const key=root.getAttribute('data-create-format');
    const platformLabel=platform.selectedOptions && platform.selectedOptions[0]
      ? platform.selectedOptions[0].textContent : '';
    let line=names[key]||'Материал';
    if(platformLabel && key!=='whiteboard' && key!=='package')line+=' · '+platformLabel;
    if(key==='post' && platform.value==='vk'){
      line+=imageEnabled()?' · с иллюстрацией':' · только текст';
    }
    if(key==='story' && count.value)line+=' · '+count.value+' экранов';
    summary.textContent=line;
  }
  let lastVkModel='auto';
  function syncVkImage(){
    const show=root.getAttribute('data-create-format')==='post' && platform.value==='vk';
    const withImage=show && imageEnabled();
    // One control owns both the UI selection and the dispatcher payload.
    // Preserve the chosen model across image/text toggles.
    if(vkModelChoice){
      if(withImage){
        if(vkModelChoice.value==='none')vkModelChoice.value=lastVkModel;
      }else if(vkModelChoice.value!=='none'){
        lastVkModel=vkModelChoice.value;
        vkModelChoice.value='none';
      }
    }
    const vkImageShell=document.getElementById('sg-create-vk-image-primary');
    if(vkImageShell)vkImageShell.hidden=!show;
    if(vkModelChoiceRow)vkModelChoiceRow.hidden=!withImage;
    if(vkRatioRow)vkRatioRow.hidden=!withImage;
    if(vkReviewTip)vkReviewTip.hidden=!withImage;
    updateFormatOptionVisibility();
    updateSummary();
  }
  function updateFormatOptionVisibility(){
    const key=root.getAttribute('data-create-format');
    const show=key==='story' || key==='static_image' ||
      (key==='post' && platform.value==='vk' && imageEnabled());
    if(formatOptions)formatOptions.hidden=!show;
  }
  vkImageRadios.forEach(radio=>radio.addEventListener('change',syncVkImage));
  if(vkModelChoice)vkModelChoice.addEventListener('change',syncVkImage);
  if(count)count.addEventListener('change',updateSummary);
  platform.addEventListener('change',()=>{syncVkImage();refreshPlatformStatus();});

  function chooseFormat(key){
    const selected=screen.querySelector('[data-sg-format="'+key+'"]');
    if(!selected)return;
    if(root.getAttribute('data-create-format')!==key){
      // Invalidate suggestions from the previous material format immediately.
      cancelTopicRequest();
      topics.hidden=true;
      topics.replaceChildren();
    }
    root.setAttribute('data-create-format',key);
    screen.querySelectorAll('[data-sg-format]').forEach(button=>{
      button.setAttribute('aria-pressed',button===selected?'true':'false');
    });
    platformRow.hidden=!channels[key] || channels[key].length===0;
    const platformShell=document.getElementById('sg-create-primary-platform');
    if(platformShell)platformShell.hidden=platformRow.hidden;
    const topicLabel=document.getElementById('sg-create-topic-label');
    if(topicLabel)topicLabel.textContent=platformRow.hidden ? '2. О чём материал?' : '3. О чём материал?';
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
    syncVkImage();
    refreshPlatformStatus();
    const names={post:'Пост',story:'Stories',static_image:'Изображение',
      short_video:'Видеосценарий',whiteboard:'Анимированное видео',package:'Полный пакет'};
    const context=document.getElementById('sg-create-context');
    const formatHelp=document.getElementById('sg-create-format-help');
    if(formatHelp)formatHelp.textContent=selected.dataset.sgDescription || '';
    // Format and platform already appear immediately above the editor;
    // repeat only special information that changes the decision to proceed.
    context.textContent=key==='whiteboard'
      ? 'Готовое видео MP4. Платный запуск — только после подтверждения.'
      : '';
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
  // Make the normal path immediately understandable without a preliminary click.
  chooseFormat('post');
  screen.querySelector('[data-sg-suggest]').addEventListener('click',async()=>{
    cancelTopicRequest();
    const controller=new AbortController();
    topicRequest=controller;
    const requestedFormat=root.getAttribute('data-create-format') || 'post';
    topics.hidden=false;topics.replaceChildren();
    const status=document.createElement('p');status.textContent='Подбираем темы из профиля компании…';topics.append(status);
    try{
      const response=await fetch(base+'/create/topics.json?format='+encodeURIComponent(requestedFormat),
        {credentials:'same-origin',headers:{Accept:'application/json'},signal:controller.signal});
      if(!response.ok)throw new Error('Не удалось получить темы');
      const data=await response.json();
      // An older response must never overwrite a newer request or another format.
      if(controller.signal.aborted || topicRequest!==controller ||
        root.getAttribute('data-create-format')!==requestedFormat)return;
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
      if(!controller.signal.aborted && topicRequest===controller &&
          root.getAttribute('data-create-format')===requestedFormat)
        status.textContent='Не удалось получить предложения. Вы можете задать тему самостоятельно.';
    }finally{
      if(topicRequest===controller)topicRequest=null;
    }
  });

  // Inline assistance panels are dismissible as well. Ignore their own
  // launcher buttons so a tap on an info icon can switch descriptions.
  document.addEventListener('pointerdown',event=>{
    if(!(event.target instanceof Element))return;
    if(!panel.hidden && !panel.contains(event.target) &&
       !event.target.closest('[data-sg-info],[data-sg-about]'))panel.hidden=true;
    if(!topics.hidden && !topics.contains(event.target) &&
       !event.target.closest('[data-sg-suggest]')){
      topics.hidden=true;
      cancelTopicRequest();
    }
  });

  // The Create overlay can be collapsed without hiding the input or losing messages.
  // Pointer gestures are scoped to the handles, so they do not compete with
  // feed scrolling or the hold-to-record voice gesture in the composer.
  const history=document.getElementById('sg-create-history');
  const historyClose=document.getElementById('sg-create-history-close');
  const historyOpen=document.getElementById('sg-create-history-open');
  if(history && historyClose && historyOpen){
    function showHistory(open,focusHandle=true){
      root.classList.toggle('sg-history-collapsed',!open);
      history.setAttribute('aria-hidden',String(!open));
      historyClose.setAttribute('aria-expanded',String(open));
      historyOpen.setAttribute('aria-expanded',String(open));
      if(focusHandle)(open ? historyClose : historyOpen).focus({preventScroll:true});
    }
    // Inline history is not a popover: clicking a form field must not collapse it.
    function attachGrip(button,open,swipeDirection){
      let gesture=null;
      let suppressClickUntil=0;
      function resetPosition(){
        const reduceMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        button.style.transition=reduceMotion?'none':'transform 160ms ease-out, opacity 160ms ease-out';
        button.style.transform='';
        button.style.opacity='';
      }
      button.addEventListener('pointerdown',event=>{
        if((event.pointerType!=='touch' && event.pointerType!=='pen') || event.button!==0)return;
        gesture={id:event.pointerId,startY:event.clientY,lastY:event.clientY,
          lastTime:event.timeStamp,velocity:0,moved:false};
        button.style.transition='none';
        // Track only the handle, never intercept scrolling in the result or editor.
        try{button.setPointerCapture(event.pointerId);}catch(_){/* synthetic event */}
      });
      button.addEventListener('pointermove',event=>{
        if(!gesture || event.pointerId!==gesture.id)return;
        const elapsed=Math.max(1,event.timeStamp-gesture.lastTime);
        gesture.velocity=(event.clientY-gesture.lastY)/elapsed;
        gesture.lastY=event.clientY;
        gesture.lastTime=event.timeStamp;
        const delta=event.clientY-gesture.startY;
        if(Math.abs(delta)>6)gesture.moved=true;
        // The handle follows the pointer directly; opposing movement resists.
        const tracked=swipeDirection*delta<0?delta*.2:delta;
        button.style.transform='translate3d(0,'+Math.max(-120,Math.min(120,tracked))+'px,0)';
      });
      button.addEventListener('pointerup',event=>{
        if(!gesture || event.pointerId!==gesture.id)return;
        const displacement=event.clientY-gesture.startY;
        const velocity=gesture.velocity;
        const dragged=gesture.moved || Math.abs(displacement)>6;
        const shouldToggle=swipeDirection*displacement>24 ||
          (swipeDirection*displacement>8 && swipeDirection*velocity>.65);
        gesture=null;
        resetPosition();
        if(dragged)suppressClickUntil=performance.now()+350;
        if(shouldToggle)showHistory(open);
      });
      button.addEventListener('pointercancel',()=>{gesture=null;resetPosition();});
      button.addEventListener('click',()=>{
        if(performance.now()<suppressClickUntil)return;
        showHistory(open);
      });
    }
    attachGrip(historyClose,false,1); // pull down
    attachGrip(historyOpen,true,-1);  // pull up
    // Voice and typed requests reopen the conversation where the reply appears.
    // Keep focus on the composer to avoid Android keyboard/focus jumps.
    root.addEventListener('sg:dispatcher:outgoing',()=>showHistory(true,false));
    root.addEventListener('sg:dispatcher:response',event=>{
      // Wait until the server response is rendered, not the send click. This
      // also reveals a confirmation that arrives after the mobile keyboard hides.
      showHistory(true,false);
      requestAnimationFrame(()=>{
        const latest=[...history.querySelectorAll('.dp-msg')].pop();
        // Never jump to an older unrelated confirmation card.
        const pending=latest && latest.querySelector('.dp-confirm-actions');
        const target=(pending && pending.closest('.dp-confirm')) || latest || history;
        target.setAttribute('tabindex','-1');
        target.scrollIntoView({block:'center',inline:'nearest',behavior:'auto'});
        if(pending)target.focus({preventScroll:true});
      });
    });
  }
  // Only the lower dispatcher composer can submit a request.
  // Settings are read at send time, including immediately after voice transcription.
})();
