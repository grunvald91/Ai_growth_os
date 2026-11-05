(function () {
  'use strict';
  var root = document.querySelector('.dp-home');
  if (!root) return;
  var api = root.getAttribute('data-api');
  var feed = document.getElementById('dp-feed');
  var chipsBox = document.getElementById('dp-chips');
  var form = document.getElementById('dp-form');
  var input = document.getElementById('dp-input');
  var sendButton = document.getElementById('dp-send');
  var newButton = document.getElementById('dp-new');
  var state = JSON.parse(document.getElementById('dp-state').textContent);
  var busy = false;
  var hydrated = false;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  // Bound only dispatcher messages: other confirmation requests have different budgets.
  // Keep the idempotency key in the caller so a mobile retry cannot duplicate an action.
  function post(path, body, timeoutMs) {
    var controller = timeoutMs && window.AbortController ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    var request = fetch(api + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error((data && data.detail) || 'Не получилось. Попробуйте ещё раз.');
        return data;
      });
    }).catch(function (error) {
      if (error && error.name === 'AbortError') {
        throw new Error('Диспетчер отвечает слишком долго. Попробуйте ещё раз — команда не будет выполнена повторно.');
      }
      throw error;
    });
    return request.then(function (value) {
      if (timer) clearTimeout(timer);
      return value;
    }, function (error) {
      if (timer) clearTimeout(timer);
      throw error;
    });
  }

  // ---- blocks: text is always set with textContent, never as markup -----------------------

  function renderChips(items, onPick) {
    var row = el('div', 'dp-chiprow');
    items.forEach(function (chip) {
      var button = el('button', 'dp-chip', chip.label);
      button.type = 'button';
      button.title = chip.label;
      button.addEventListener('click', function () { onPick(chip); });
      row.appendChild(button);
    });
    return row;
  }

  // Older conversations store review videos as ordinary link blocks.
  // Keep those messages readable after deployment without changing history.
  function legacyVideoTitle(label) {
    var title = String(label || '')
      .replace(/^(?:(?:Рисованное|Простое) видео готово:\s*)+/i, '')
      .replace(/^(?:(?:Рисованное|Простое) видео:\s*)+/i, '')
      .trim();
    return title || 'Видео без названия';
  }

  function renderLinks(items) {
    var list = el('div', 'dp-links');
    var videoCards = [];
    function flushVideos() {
      if (!videoCards.length) return;
      list.appendChild(renderResourceList({ items: videoCards }));
      videoCards = [];
    }
    // Chronology is decided by the executor. Do not invent missing previews.
    items.forEach(function (item) {
      var raw = String(item.label || '');
      if (/^(?:(?:Рисованное|Простое) видео готово:|Готов MP4:)/i.test(raw)) {
        var topic = /^Готов MP4:/i.test(raw)
          ? raw.replace(/^Готов MP4:\s*/i, '').trim() : legacyVideoTitle(raw);
        videoCards.push({ kind: 'video', title: topic || 'Видео без названия',
                          status: 'Готово · проверить', href: item.href });
        return;
      }
      flushVideos();
      var caption = voiceHoldEnabled && raw.indexOf('Открыть: ') === 0
        ? 'Открыть: ' + friendlyTitle(raw.slice(9)) : raw;
      var link = el('a', 'dp-link', caption);
      link.href = item.href;
      list.appendChild(link);
    });
    flushVideos();
    return list;
  }

  function friendlyKind(text) {
    return ({
      whiteboard_renderer:'Простое видео',company_intake:'Профиль компании',
      distribution:'Публикация',seo_factory:'SEO-завод',content_factory:'Создание контента',
      article:'Статья',whiteboard:'Простое видео','Рисованное видео':'Простое видео'
    })[text] || (/^[a-z_]+$/.test(String(text || '')) ? 'Материал' : text);
  }

  function friendlyTitle(title) {
    var value = String(title || '');
    if (/^WHITEBOARD-JOB:\s*[a-f0-9-]{6,}$/i.test(value)) return 'Создание простого видео';
    if (/^Request approval to publish own-site SEO article/i.test(value)) return 'Подтвердить публикацию статьи';
    return value.replace(/^Рисованное видео:/i, 'Простое видео:')
      .replace(/рисованное видео/gi, 'простое видео');
  }

  function renderTable(block) {
    var wrap = el('div', 'dp-tablewrap');
    var table = el('table', 'dp-table');
    var head = el('tr');
    block.columns.forEach(function (name) { head.appendChild(el('th', '', name)); });
    var thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
    var body = el('tbody');
    // Executors send oldest-to-newest rows for the bottom-anchored dispatcher.
    var isWorkList = voiceHoldEnabled && block.columns.indexOf('Задание') >= 0 &&
      block.columns.indexOf('Статус') >= 0;
    block.rows.forEach(function (row) {
      var tr = el('tr');
      row.forEach(function (cell, index) {
        var td = el('td', '', index === 0 && isWorkList ? friendlyTitle(cell) : cell);
        td.setAttribute('data-label', block.columns[index]);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body); wrap.appendChild(table);
    return wrap;
  }

  // One visual item = one accessible link. No duplicate table/link sections.
  function renderResourceList(block) {
    var list = el('div', 'dp-resource-list');
    var kinds = {
      video: { label: 'Простое видео', glyph: '▶' },
      post: { label: 'Пост', glyph: '✎' },
      plan: { label: 'Контент-план', glyph: '▦' },
      task: { label: 'Задание', glyph: '◷' },
      material: { label: 'Материал', glyph: '▤' }
    };
    block.items.forEach(function (item) {
      var kind = kinds[item.kind] ? item.kind
        : /\/whiteboard\//.test(String(item.href || '')) ? 'video' : 'material';
      var meta = kinds[kind];
      var card = el('a', 'dp-resource dp-resource--' + kind);
      card.href = item.href;
      var visual = el('span', 'dp-resource-visual', meta.glyph);
      visual.setAttribute('aria-hidden', 'true');
      card.appendChild(visual);
      var content = el('span', 'dp-resource-copy');
      content.appendChild(el('span', 'dp-resource-kind', meta.label));
      content.appendChild(el('strong', 'dp-resource-title',
        kind === 'video' ? legacyVideoTitle(item.title)
          : voiceHoldEnabled ? friendlyTitle(item.title) : item.title));
      if (item.status) content.appendChild(el('span', 'dp-resource-status', item.status));
      card.appendChild(content);
      var arrow = el('span', 'dp-resource-arrow', '→');
      arrow.setAttribute('aria-hidden', 'true');
      card.appendChild(arrow);
      list.appendChild(card);
    });
    return list;
  }

  function renderTask(block) {
    var card = el('a', 'dp-task');
    card.href = block.href;
    card.appendChild(el('span', 'dp-task-title', voiceHoldEnabled ? friendlyTitle(block.title) : block.title));
    card.appendChild(el('span', 'dp-task-status', statusWord(block.status)));
    return card;
  }

  // A ready material: kind and status on top, a short text, a link to the full view. Its buttons come as chips.
  function renderMaterial(block) {
    var productBase = api.replace(/\/dispatch\/?$/, '');
    var videoPath = String(block.href || '').slice(productBase.length);
    var isVideo = voiceHoldEnabled && String(block.href || '').indexOf(productBase) === 0 &&
      /^\/whiteboard\/[0-9a-f-]{36}$/i.test(videoPath);
    var card = el(isVideo ? 'a' : 'div', 'dp-material' + (isVideo ? ' dp-material--video' : ''));
    if (isVideo) card.href = block.href;
    var top = el('div', 'dp-material-top');
    top.appendChild(el('span', 'dp-material-kind', voiceHoldEnabled ? friendlyKind(block.kind) : block.kind));
    var status = el('span', 'dp-material-status',
      voiceHoldEnabled && block.status === 'Ждёт вашего решения' ? 'На проверке' : block.status);
    status.dataset.state = ({'Утверждён':'approved','Опубликован':'published',
      'Ждёт вашего решения':'review','Отклонён':'rejected'})[block.status] || 'draft';
    top.appendChild(status);
    card.appendChild(top);
    var title = voiceHoldEnabled ? friendlyTitle(block.title) : block.title;
    if (voiceHoldEnabled && /^(Простое видео|Видео)$/.test(friendlyKind(block.kind)))
      title = legacyVideoTitle(title).replace(/^Видео:\s*/i, '');
    card.appendChild(el('strong', 'dp-material-title', title));
    if (block.excerpt && !(isVideo && block.excerpt === 'Видео готово к просмотру.'))
      card.appendChild(el('p', 'dp-material-text', block.excerpt));
    // Use the existing protected file route only for a completed video's scoped
    // job URL. Never infer a thumbnail for an arbitrary material or another company.
    if (isVideo) {
      card.setAttribute('aria-label', 'Посмотреть видео: ' + title);
      var preview = el('span', 'dp-video-preview');
      var image = el('img', '');
      image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
      image.addEventListener('error', function () {
        preview.remove(); card.classList.add('dp-material--no-preview');
      });
      image.src = block.href + '/files/video.png';
      var play = el('span', 'dp-video-play');
      play.setAttribute('aria-hidden','true'); play.appendChild(el('span', '', '▶'));
      preview.appendChild(image); preview.appendChild(play); card.appendChild(preview);
    }
    var open = el(isVideo ? 'span' : 'a', 'dp-link', isVideo ? 'Посмотреть видео →' : 'Открыть полностью');
    if (!isVideo) open.href = block.href;
    card.appendChild(open);
    return card;
  }

  function statusWord(status) {
    return ({
      queued: 'В очереди', running: 'Готовится', waiting_approval: 'Ждёт подтверждения',
      completed: 'Готово', failed: 'Ошибка', cancelled: 'Отменено'
    })[status] || status;
  }

  var createFormatNames = {
    post: 'Пост', story: 'Stories', short_video: 'Видеосценарий',
    static_image: 'Изображение', package: 'Полный пакет'
  };
  var createChannels = {
    post: [['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['vc_ru','vc.ru'],['tenchat','TenChat']],
    story: [['telegram','Telegram Stories'],['vk','Истории ВК'],['instagram_reels','Instagram Stories']],
    short_video: [['youtube_shorts','YouTube Shorts'],['instagram_reels','Instagram Reels'],['vk_clips','VK Клипы']],
    static_image: [['telegram','Telegram'],['vk','ВКонтакте'],['dzen','Дзен'],['vc_ru','vc.ru'],['tenchat','TenChat'],['instagram_reels','Instagram']],
    package: []
  };
  var createDefaultChannels = {post:'telegram',story:'telegram',short_video:'youtube_shorts',
    static_image:'telegram',package:''};

  function editableRow(label, value) {
    var row = el('div', 'dp-create-detail');
    row.appendChild(el('span', 'dp-create-detail-label', label));
    row.appendChild(el('strong', 'dp-create-detail-value', value));
    return row;
  }

  function renderCreationEditor(block, card, details, actions) {
    var data = block.editable;
    if (!data) return;
    var editButton = el('button', 'dp-btn ghost dp-edit-create', 'Изменить');
    editButton.type = 'button';
    actions.insertBefore(editButton, actions.firstChild);
    editButton.addEventListener('click', function () {
      var form = el('form', 'dp-create-editor');
      var status = el('p', 'dp-create-editor-error');
      status.setAttribute('role', 'alert'); status.hidden = true;
      function field(label, control) {
        var wrapper = el('label', 'dp-create-edit-field');
        wrapper.appendChild(el('span', '', label));
        wrapper.appendChild(control);
        return wrapper;
      }
      var kind = el('select', '');
      kind.setAttribute('aria-label', 'Тип материала');
      (data.formats || []).forEach(function (code) {
        var item = el('option', '', createFormatNames[code] || code);
        item.value = code; kind.appendChild(item);
      });
      kind.value = data.format;
      var topic = el('textarea', '');
      topic.value = data.topic || '';
      topic.rows = 2; topic.maxLength = 300; topic.required = true;
      topic.minLength = 3; topic.setAttribute('aria-label','Тема');
      var audience = el('input', '');
      audience.value = data.audience || ''; audience.maxLength = 300;
      var brief = el('textarea', '');
      brief.value = data.editorial_brief || ''; brief.maxLength = 3500; brief.rows = 2;
      var model = el('select','');
      [['auto','Автоматически · одна модель'],['gemini','Gemini'],['openai','ChatGPT Image']]
        .forEach(function (entry) {
          var opt=el('option','',entry[1]);opt.value=entry[0];model.appendChild(opt);
        });
      model.value=data.image_model && data.image_model!=='none' ? data.image_model : 'auto';
      var modelField=field('Модель изображения',model);
      var aspect=el('select','');
      [['','По площадке'],['1:1','1:1 · квадрат'],['4:5','4:5 · вертикальный пост'],
        ['9:16','9:16 · сторис'],['16:9','16:9 · горизонтальный']]
        .forEach(function(entry){var item=el('option','',entry[1]);item.value=entry[0];aspect.appendChild(item);});
      aspect.value=data.aspect_ratio || '';
      var aspectField=field('Пропорции изображения',aspect);
      var count = el('select','');
      var auto = el('option','','Автоматически (3–10)'); auto.value = ''; count.appendChild(auto);
      for (var i=3; i<=10; i++){
        var opt = el('option','',String(i)); opt.value=String(i); count.appendChild(opt);
      }
      count.value = data.frame_count == null ? '' : String(data.frame_count);
      var platform = el('select','');
      var countField = field('Количество экранов',count);
      var platformField = field('Площадка', platform);
      function refreshOptions() {
        var fmt = kind.value;
        countField.hidden = fmt !== 'story';
        modelField.hidden = fmt !== 'story' && fmt !== 'static_image';
        aspectField.hidden = fmt !== 'static_image';
        platformField.hidden = fmt === 'package';
        var previous = platform.value || data.platform || '';
        platform.replaceChildren();
        (createChannels[fmt] || []).forEach(function (entry) {
          var opt=el('option','',entry[1]);opt.value=entry[0];platform.appendChild(opt);
        });
        var choices=(createChannels[fmt] || []).map(function (x) {return x[0];});
        platform.value = choices.indexOf(previous) >= 0 ? previous : createDefaultChannels[fmt];
      }
      kind.addEventListener('change',function(){aspect.value='';refreshOptions();});
      form.appendChild(field('Тип материала',kind));
      form.appendChild(field('Тема',topic));
      form.appendChild(countField);
      form.appendChild(platformField);
      form.appendChild(modelField);
      form.appendChild(aspectField);
      form.appendChild(field('Аудитория (необязательно)', audience));
      form.appendChild(field('Дополнительные требования', brief));
      refreshOptions();
      form.appendChild(status);
      var buttons = el('div','dp-create-edit-actions');
      var discard = el('button','dp-btn ghost','Отменить правку');
      discard.type='button';
      var save = el('button','dp-btn primary','Сохранить');
      save.type='submit';
      discard.addEventListener('click',function(){
        form.remove();details.hidden=false;actions.hidden=false;
      });
      buttons.append(discard,save);form.appendChild(buttons);
      details.hidden=true; actions.hidden=true; card.appendChild(form);
      form.addEventListener('submit',function (event) {
        event.preventDefault();
        if (!topic.value.trim() || topic.value.trim().length < 3) {
          status.textContent='Введите тему не короче трёх символов.';
          status.hidden=false;return;
        }
        save.disabled=true; discard.disabled=true; status.hidden=true;
        post('/edit', {
          confirmation_id:block.confirmation_id, revision:block.revision || 0,
          format:kind.value, topic:topic.value.trim(),
          frame_count:kind.value === 'story' && count.value ? Number(count.value) : null,
          platform:kind.value === 'package' ? null : platform.value,
          audience:audience.value.trim(), editorial_brief:brief.value.trim(),
          image_model:kind.value==='story'||kind.value==='static_image' ? model.value : 'none',
          aspect_ratio:kind.value==='story' ? '9:16'
            : kind.value==='static_image' ? (aspect.value || null) : null
        }).then(applyResult).catch(function (error) {
          status.textContent=error.message;status.hidden=false;
          save.disabled=false;discard.disabled=false;
        });
      });
      kind.focus();
    });
  }

  function renderConfirm(block) {
    var card = el('div', 'dp-confirm' + (block.paid ? ' paid' : ''));
    card.appendChild(el('strong', 'dp-confirm-title', voiceHoldEnabled ? friendlyTitle(block.title) : block.title));
    if (block.paid) card.appendChild(el('span', 'dp-badge', 'Платно'));
    var details = el('div', block.editable ? 'dp-create-details' : 'dp-confirm-list');
    if (block.editable) {
      var data = block.editable;
      details.appendChild(editableRow('Тип',createFormatNames[data.format] || data.format));
      if (data.format === 'story')
        details.appendChild(editableRow('Экранов', data.frame_count == null ? 'Автоматически' : String(data.frame_count)));
      details.appendChild(editableRow('Тема',data.topic));
      if (data.format==='story'||data.format==='static_image')
        details.appendChild(editableRow('Пропорции',data.aspect_ratio ||
          (data.format==='story'?'9:16':(data.platform==='instagram_reels'?'4:5':'1:1'))));
      if ((data.format==='story'||data.format==='static_image') && data.image_model!=='none')
        details.appendChild(editableRow('Изображения', {'auto':'Автоматически',
          'gemini':'Gemini','openai':'ChatGPT Image'}[data.image_model]||'Автоматически'));
      if (data.audience) details.appendChild(editableRow('Аудитория', data.audience));
      if (data.editorial_brief) details.appendChild(editableRow('Требования',
        data.editorial_brief.slice(0,170) + (data.editorial_brief.length>170 ? '…' : '')));
      if (data.platform)
        details.appendChild(editableRow('Площадка',
          ((createChannels[data.format] || []).find(function (row) { return row[0]===data.platform; }) || [null,data.platform])[1]));
      (block.details || []).filter(function (line) {
        return !/^(Тема|Формат|Экранов|Площадка|Аудитория|Требования|Пропорции):/.test(line);
      }).forEach(function (line) {details.appendChild(el('p','dp-create-note',line));});
    } else {
      var list = el('ul', 'dp-confirm-list');
      (block.details || []).forEach(function (line) {list.appendChild(el('li','',line));});
      details.appendChild(list);
    }
    card.appendChild(details);
    if (block.state === 'pending') {
      var actions = el('div', 'dp-confirm-actions');
      var no = el('button', 'dp-btn ghost', block.editable ? 'Отменить задание' : block.cancel_label); no.type='button';
      var yes = el('button', 'dp-btn primary', block.confirm_label); yes.type='button';
      no.addEventListener('click',function(){answer(block,false,no,yes);});
      yes.addEventListener('click',function(){answer(block,true,no,yes);});
      actions.appendChild(yes);
      if (block.editable) renderCreationEditor(block,card,details,actions);
      actions.appendChild(no);
      card.appendChild(actions);
    } else {
      card.appendChild(el('span','dp-confirm-state',block.state === 'confirmed' ? 'Подтверждено' : 'Отменено'));
    }
    return card;
  }

  function answer(block, accept, no, yes) {
    no.disabled = yes.disabled = true;
    post('/confirm', { confirmation_id: block.confirmation_id, accept: accept,
      via: 'button', revision: block.revision || 0 })
      .then(applyResult)
      .catch(function (error) { no.disabled = yes.disabled = false; notify(error.message); });
  }

  function renderMessage(message) {
    var bubble = el('div', 'dp-msg ' + (message.role === 'user' ? 'user' : 'bot'));
    bubble.setAttribute('data-id', message.id);
    if (message.role === 'user') {
      bubble.appendChild(el('p', 'dp-text', message.text));
      return bubble;
    }
    var blocks = message.blocks || [];
    var shortProgress = voiceHoldEnabled && blocks.some(function (block) {
      return block.type === 'text' && /^Запустил (?:рисованное|простое) видео\. Оно появится в ленте, когда будет готово\.$/.test(block.text);
    }) && blocks.some(function (block) {
      return block.type === 'links' && block.items.some(function (item) { return item.label === 'Ход создания'; });
    });
    if (shortProgress) bubble.classList.add('dp-msg--progress');
    var materials = blocks.filter(function (block) { return block.type === 'material'; });
    function topic(text) {
      return friendlyTitle(text).replace(/^Готово:\s*/i, '')
        .replace(/^(?:Простое видео|Видео|Пост|Статья):\s*/i, '')
        .replace(/[.!\s]+$/, '').trim();
    }
    blocks.forEach(function (block) {
      if (block.type === 'text') {
        // Suppress only an exact redundant completion caption. Keep instructions,
        // warnings and any other text, including messages from saved conversations.
        var duplicate = voiceHoldEnabled && /^Готово:\s*/i.test(block.text) &&
          materials.some(function (item) { return topic(block.text) === topic(item.title); });
        var started = /^Запустил (?:рисованное|простое) видео\. Оно появится в ленте, когда будет готово\.$/.test(block.text);
        if (!duplicate) bubble.appendChild(el('p', 'dp-text', shortProgress && started ? 'Видео создаётся' : voiceHoldEnabled ?
          String(block.text).replace(/^Запустил рисованное видео\./, 'Запустил простое видео.') : block.text));
      }
      else if (block.type === 'chips') bubble.appendChild(renderChips(block.items, sendChip));
      else if (block.type === 'links') {
        var items = voiceHoldEnabled ? block.items.filter(function (item) {
          return !materials.some(function (material) {
            return material.href === item.href && /^(Открыть|Открыть полностью)$/.test(item.label);
          });
        }) : block.items;
        if (items.length) bubble.appendChild(renderLinks(items));
      }
      else if (block.type === 'table') bubble.appendChild(renderTable(block));
      else if (block.type === 'resource_list') bubble.appendChild(renderResourceList(block));
      else if (block.type === 'task') bubble.appendChild(renderTask(block));
      else if (block.type === 'material') bubble.appendChild(renderMaterial(block));
      else if (block.type === 'confirm') bubble.appendChild(renderConfirm(block));
    });
    return bubble;
  }

  function upsert(message) {
    var node = renderMessage(message);
    var existing = feed.querySelector('[data-id="' + message.id + '"]');
    if (Number.isInteger(message.seq)) node.setAttribute('data-seq', String(message.seq));
    if (existing) {
      feed.replaceChild(node, existing);
      return;
    }
    if (hydrated && voiceHoldEnabled) node.classList.add('dp-enter');
    // Requests and update polling can finish out of order. Keep the
    // conversation oldest -> newest, with the newest next to the composer.
    var following = Array.from(feed.children).find(function (child) {
      var seq = Number(child.getAttribute('data-seq'));
      return child.hasAttribute('data-seq') && Number.isInteger(message.seq) && seq > message.seq;
    });
    if (following) feed.insertBefore(node, following);
    else feed.appendChild(node);
  }

  function renderHints() {
    chipsBox.textContent = '';
    if (state.hints && state.hints.length) chipsBox.appendChild(renderChips(state.hints, sendChip));
  }

  function toBottom() { feed.scrollTop = feed.scrollHeight; }
  function syncConversationLayout() {
    var hasMessages = feed.children.length > 0;
    root.classList.toggle('has-feed', hasMessages);
    // v4's empty-home CSS centers the welcome hero. It must be disabled as
    // soon as a conversation begins, otherwise an empty stripe persists.
    if (document.body.getAttribute('data-growth-design') === 'v4') {
      document.body.classList.toggle('home-empty', !hasMessages);
    }
  }

  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    root.classList.toggle('busy', value);
    var pending = feed.querySelector('.dp-typing');
    if (value && !pending) {
      var dots = el('div', 'dp-msg bot dp-typing', 'Думаю…');
      dots.setAttribute('role', 'status');
      feed.appendChild(dots);
    }
    if (!value && pending) pending.remove();
    if (!value && voiceDispatchPending) {
      voiceDispatchPending = false;
      hideLive();
      setVoiceState('idle');
    }
    toBottom();
  }

  function notify(text) {
    var note = el('div', 'dp-msg bot dp-error', text);
    feed.appendChild(note);
    syncConversationLayout();
    toBottom();
  }

  function applyResult(data) {
    (data.messages || []).forEach(upsert);
    if (data.hints) { state.hints = data.hints; renderHints(); }
    syncConversationLayout();
    toBottom();
  }

  // ---- sending ----------------------------------------------------------------------------

  function send(body, echo, voiceRequest) {
    if (busy) return false;
    // Preserve the same idempotency key when retrying an ambiguous network failure.
    var key = body.client_key || newKey();
    body.client_key = key;
    var echoNode = null;
    if (echo) {
      echoNode = renderMessage({ id: 'local-' + key, role: 'user', text: echo });
      feed.appendChild(echoNode);
      syncConversationLayout();
    }
    setBusy(true);
    post('/messages', body, 55000).then(function (data) {
      if (echoNode) echoNode.remove();
      applyResult(data);
      if (voiceRequest) showNote('');
    }).catch(function (error) {
      if (echoNode) echoNode.remove();
      notify(error.message);
      if (voiceRequest) {
        var retry = el('button', 'dp-voice-undo', 'Отправить ещё раз');
        retry.type = 'button';
        retry.addEventListener('click', function () {
          if (busy) return;
          showNote('');
          voiceDispatchPending = true;
          setVoiceState('sending');
          showLive('Повторно передаю команду…', '', true);
          send(body, echo, true);
        });
        showNote('Голосовая команда сохранена, отправка не удалась.', true, retry);
      } else if (body.mode === 'text') {
        // A Create hint is transport context, not part of the user's draft.
        var restored = root.classList.contains('sg-create-dispatch') && echo ? echo : body.text;
        input.value = restored + (input.value.trim() ? '\n' + input.value : '');
        grow();
      }
    }).then(function () { setBusy(false); });
    return true;
  }

  function sendChip(chip) {
    send({ mode: 'chip', chip: { label: chip.label, capability: chip.capability, params: chip.params || {} } }, chip.label);
  }

  function createCommand(text) {
    if (!root.classList.contains('sg-create-dispatch')) return text;
    // Explicitly spoken/written format always wins over an older format chip.
    // A selected chip is only a fallback for a bare subject like "про волосы".
    if (/(?:^|[^\p{L}\p{N}])(?:сторис|истори(?:и|й|ю|я|ями|ях)|пост(?:ы|а|у|е|ом|ов|ам|ами|ах)?|видеосценари\p{L}*|сценари\p{L}*|видео|видеоролик\p{L}*|ролик\p{L}*|анимаци\p{L}*|изображени\p{L}*|картин\p{L}*|стать(?:я|ю|и|е|ей|ёй|ями|ях)|пакет)(?=$|[^\p{L}\p{N}])/iu.test(text)) return text;
    var format = root.getAttribute('data-create-format');
    var lead = ({
      post: 'Подготовь пост по следующей задаче: ',
      story: 'Создай Stories по следующей задаче: ',
      static_image: 'Подготовь изображение по следующей задаче: ',
      short_video: 'Подготовь видеосценарий по следующей задаче: ',
      whiteboard: 'Создай простое рисованное анимированное видео по следующей задаче: ',
      package: 'Подготовь полный контент-пакет по следующей задаче: '
    })[format];
    if (!lead) return text;
    if (format === 'story') {
      var count = document.getElementById('sg-create-count');
      if (count && count.value) {
        lead = 'Создай ' + count.value + ' сторис по следующей задаче: ';
      }
    }
    return lead + text;
  }

  function creationHints() {
    if (!root.classList.contains('sg-create-dispatch')) return null;
    var chosen=root.getAttribute('data-create-format');
    var selected=/^(post|story|short_video|static_image|package)$/.test(chosen||'') ? chosen : null;
    var platform=document.getElementById('sg-create-platform');
    var count=document.getElementById('sg-create-count');
    var audience=document.getElementById('sg-create-audience');
    var brief=document.getElementById('sg-create-brief');
    var imageModel=document.getElementById('sg-create-image-model');
    var aspect=document.getElementById('sg-create-aspect');
    var a=audience ? audience.value.trim() : '';
    var b=brief ? brief.value.trim() : '';
    if (!selected && !a && !b) return null;
    return {
      format:selected,
      platform:selected && selected!=='package' && platform ? platform.value : null,
      frame_count:selected==='story' && count && count.value ? Number(count.value) : null,
      audience:a || null,
      editorial_brief:b || null,
      image_model:selected==='story'||selected==='static_image'
        ? (imageModel && imageModel.value || 'auto') : null,
      aspect_ratio:selected==='story' ? '9:16'
        : selected==='static_image' && aspect && aspect.value ? aspect.value : null
    };
  }

  function sendText() {
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = '';
    grow();
    send({ mode: 'text', text: createCommand(text), creation: creationHints() }, text);
  }

  function grow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }

  form.addEventListener('submit', function (event) { event.preventDefault(); sendText(); });
  input.addEventListener('input', grow);
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendText(); }
  });
  newButton.addEventListener('click', function () {
    if (busy) return;
    // Start a new conversation without opening the Android keyboard.
    if (voiceHoldEnabled) input.blur();
    post('/new', {}).then(function (data) {
      feed.textContent = ''; syncConversationLayout();
      state.hints = data.hints || []; renderHints();
      if (voiceHoldEnabled) {
        input.value = '';
        grow();
        input.blur();
        showNote('');
      } else input.focus();
    }).catch(function (error) { notify(error.message); });
  });

  // The classic Studio mic remains click-to-record; v4 uses the entire composer as hold-to-talk.

  var micButton = document.getElementById('dp-mic');
  var voiceNote = document.getElementById('dp-voice-note');
  var voiceUrl = root.getAttribute('data-voice-url');
  var voiceHoldEnabled = form.classList.contains('command-bar');
  var voiceDispatchPending = false, discardRecording = false, activeHold = null, holdTimer = null;
  var previewUrl = root.getAttribute('data-voice-preview-url');
  var voiceHitbox = document.getElementById('dp-voice-hitbox');
  var modeMicButton = document.getElementById('dp-mode-mic');
  var typingPlaceholder = 'Введите команду…';
  var voiceOverlay = document.getElementById('dp-voice-overlay');
  // A fixed layer inside the animated home section is clipped to its
  // transformed ancestor on Android. Portal it to <body> instead.
  if (voiceHoldEnabled && voiceOverlay) document.body.appendChild(voiceOverlay);
  var voiceOverlayText = document.getElementById('dp-voice-overlay-text');
  var voiceStatus = document.getElementById('dp-voice-status');
  var overlayExitTimer = null;
  var liveBox = document.getElementById('dp-live');
  var liveLabel = document.getElementById('dp-live-label');
  var liveText = document.getElementById('dp-live-text');
  var previewTimer = null, previewAbort = null, previewOff = false, lastLive = '', recordingId = 0;
  var voiceProject = root.getAttribute('data-voice-project') || '';
  var voiceMax = parseInt(root.getAttribute('data-voice-max'), 10) || 300;
  var defaultPlaceholder = input.getAttribute('placeholder');
  var recorder = null, recordStream = null, recordChunks = [], recordStarted = 0, recordTimer = null;
  var voiceWorking = false, micOpening = false;
  var lastInsert = null;

  function mimeCandidate() {
    var list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
    for (var i = 0; i < list.length; i++) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(list[i])) return list[i];
    }
    return '';
  }

  function extensionFor(type) {
    if (type.indexOf('mp4') >= 0) return 'm4a';
    if (type.indexOf('ogg') >= 0) return 'ogg';
    return 'webm';
  }

  function showNote(text, bad, undo) {
    voiceNote.textContent = '';
    voiceNote.classList.toggle('bad', !!bad);
    if (!text) { voiceNote.hidden = true; return; }
    voiceNote.appendChild(el('span', '', text));
    if (undo) voiceNote.appendChild(undo);
    voiceNote.hidden = false;
  }

  function clock(seconds) {
    var m = Math.floor(seconds / 60), s = seconds % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function recentLines() {
    var lines = [];
    var nodes = feed.querySelectorAll('.dp-msg:not(.dp-typing):not(.dp-error)');
    for (var i = Math.max(0, nodes.length - 3); i < nodes.length; i++) {
      var parts = nodes[i].querySelectorAll('.dp-text');
      var text = Array.prototype.map.call(parts, function (p) { return p.textContent; }).join(' ').trim();
      if (text) lines.push(text);
    }
    return lines;
  }

  function setVoiceState(mode) {
    var recording = mode === 'recording';
    if (micButton) {
      micButton.classList.toggle('recording', recording);
      micButton.setAttribute('aria-pressed', recording ? 'true' : 'false');
      micButton.setAttribute('aria-label', recording ? 'Закончить запись' : 'Надиктовать голосом');
      micButton.disabled = mode === 'processing' || mode === 'sending';
    }
    if (voiceHoldEnabled) {
      var entering = mode === 'connecting' || recording;
      form.classList.toggle('voice-recording', recording);
      form.classList.toggle('voice-processing', mode === 'processing');
      form.classList.toggle('voice-sending', mode === 'sending');
      root.classList.toggle('voice-connecting', mode === 'connecting');
      root.classList.toggle('voice-recording', recording);
      root.classList.toggle('voice-processing', mode === 'processing' || mode === 'sending');
      // VoiceGlow owns transitions and uses the same stream as MediaRecorder.
      if (window.VoiceGlow) {
        if (mode === 'connecting') window.VoiceGlow.connecting();
        else if (mode === 'processing') window.VoiceGlow.recognize();
        else if (mode === 'sending' || mode === 'idle') window.VoiceGlow.hide();
      }
      if (voiceStatus) {
        voiceStatus.hidden = mode !== 'processing' && mode !== 'sending';
        voiceStatus.textContent = mode === 'processing' ? 'Распознаю и редактирую запись…'
          : mode === 'sending' ? 'Обрабатываю команду…' : '';
      }
      input.readOnly = mode !== 'idle';
      form.setAttribute('aria-busy', mode === 'processing' || mode === 'sending' ? 'true' : 'false');
      sendButton.disabled = busy || mode !== 'idle';
      input.setAttribute('placeholder',
        form.classList.contains('voice-text-editing') ? typingPlaceholder : defaultPlaceholder);
    } else {
      var message = mode === 'processing' ? 'Распознаю…'
        : mode === 'recording' ? 'Говорите…' : defaultPlaceholder;
      input.setAttribute('placeholder', message);
    }
  }

  function releaseStream() {
    if (recordTimer) { clearInterval(recordTimer); recordTimer = null; }
    if (recordStream) { recordStream.getTracks().forEach(function (t) { t.stop(); }); recordStream = null; }
  }

  // ---- live view: while the person speaks, the words recognised so far appear in a box above the line ----

  function showLive(label, text, working) {
    liveBox.hidden = false;
    liveBox.classList.toggle('working', !!working);
    liveLabel.textContent = label;
    liveText.textContent = text || '';
    liveBox.scrollTop = liveBox.scrollHeight;
  }

  function hideLive() { liveBox.hidden = true; liveText.textContent = ''; lastLive = ''; }

  function stopPreview() {
    if (previewTimer) { clearTimeout(previewTimer); previewTimer = null; }
    if (previewAbort) { previewAbort.abort(); previewAbort = null; }
  }

  function schedulePreview(delay, id) {
    if (previewOff || !previewUrl) return;
    previewTimer = setTimeout(function () { runPreview(id); }, delay);
  }

  // Each request carries the whole recording so far (the next one starts only after the last answered),
  // so every answer replaces the previous text and the words never jump around between pieces.
  function runPreview(id) {
    previewTimer = null;
    if (id !== recordingId || !recorder || recorder.state === 'inactive') return;
    if (!recordChunks.length) { schedulePreview(1000, id); return; }
    var type = recorder.mimeType || 'audio/webm';
    var form = new FormData();
    form.append('audio', new Blob(recordChunks, { type: type }), 'preview.' + extensionFor(type));
    form.append('duration_ms', String(Date.now() - recordStarted));
    if (voiceProject) form.append('project', voiceProject);
    var controller = new AbortController();
    previewAbort = controller;
    fetch(previewUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json' },
                        body: form, signal: controller.signal })
      .then(function (response) {
        if (response.status === 503) previewOff = true;
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (id !== recordingId || !recorder) return;
        if (data && data.text) { lastLive = data.text; showLive('Слушаю…', data.text, false); }
      })
      .catch(function () {})
      .then(function () {
        if (previewAbort === controller) previewAbort = null;
        if (id === recordingId && recorder && recorder.state !== 'inactive') schedulePreview(1500, id);
      });
  }

  function startRecording() {
    if (voiceWorking || recorder || micOpening || busy || (voiceHoldEnabled && !activeHold)) return;
    var intent = activeHold;
    discardRecording = false;
    showNote('');
    if (voiceHoldEnabled) input.blur(); // close the keyboard before showing the recording glow
    micOpening = true;
    if (voiceHoldEnabled) {
      setVoiceState('connecting');
      showLive('Подключаю микрофон…', '', true);
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      micOpening = false;
      if (voiceHoldEnabled && (!intent || intent !== activeHold || intent.cancelled)) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        hideLive();
        setVoiceState('idle');
        return;
      }
      recordStream = stream;
      var type = mimeCandidate();
      try { recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream); }
      catch (error) { releaseStream(); hideLive(); setVoiceState('idle'); showNote('Этот браузер не умеет записывать звук.', true); return; }
      recordChunks = [];
      recorder.addEventListener('dataavailable', function (event) { if (event.data && event.data.size) recordChunks.push(event.data); });
      recorder.addEventListener('stop', onRecorded);
      try { recorder.start(1000); }
      catch (error) { recorder = null; releaseStream(); hideLive(); setVoiceState('idle'); showNote('Не получилось включить запись.', true); return; }
      recordStarted = Date.now();
      if (voiceHoldEnabled && window.VoiceGlow) window.VoiceGlow.start(stream);
      recordingId += 1; lastLive = '';
      showLive(voiceHoldEnabled ? 'Говорите · отпустите, чтобы отправить' : 'Слушаю…', '', false);
      schedulePreview(2500, recordingId);
      setVoiceState('recording');
      var tick = function () {
        var seconds = Math.floor((Date.now() - recordStarted) / 1000);
        input.setAttribute('placeholder', 'Идёт запись ' + clock(seconds));
        if (seconds >= voiceMax) stopRecording();
      };
      tick();
      recordTimer = setInterval(tick, 250);
    }).catch(function (error) {
      micOpening = false;
      releaseStream();
      hideLive();
      setVoiceState('idle');
      var denied = error && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
      showNote(denied ? 'Нет доступа к микрофону. Разрешите его для этого сайта в настройках браузера.'
                      : 'Микрофон не найден или занят другим приложением.', true);
    });
  }

  function stopRecording() {
    if (recorder && recorder.state !== 'inactive') {
      if (voiceHoldEnabled && window.VoiceGlow) {
        if (discardRecording) window.VoiceGlow.hide();
        else window.VoiceGlow.recognize();
      }
      recorder.stop();
    }
  }

  function onRecorded() {
    var type = (recorder && recorder.mimeType) || 'audio/webm';
    var millis = Date.now() - recordStarted;
    stopPreview();
    recordingId += 1;
    recorder = null;
    releaseStream();
    if (discardRecording) {
      discardRecording = false;
      recordChunks = [];
      hideLive();
      setVoiceState('idle');
      return;
    }
    showLive('Распознаю и редактирую запись…', lastLive, true);
    var blob = new Blob(recordChunks, { type: type });
    recordChunks = [];
    voiceWorking = true;
    setVoiceState('processing');
    var form = new FormData();
    form.append('audio', blob, 'dictation.' + extensionFor(type));
    form.append('duration_ms', String(millis));
    form.append('context', JSON.stringify(recentLines()));
    if (voiceProject) form.append('project', voiceProject);
    fetch(voiceUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json' }, body: form })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) throw new Error((data && data.detail) || 'Не получилось распознать. Попробуйте ещё раз.');
          return data;
        });
      })
      .then(function (data) {
        hideLive();
        if (voiceHoldEnabled) {
          if (data.empty || !data.text) {
            showNote(emptyReasons[data.empty_reason] || emptyReasons.silence, true);
            return;
          }
          // Voice requests are independent of a typed draft already in the field.
          var transcript = String(data.text).trim();
          if (data.fallback_reason) {
            showNote('Редактор не завершил правку: передаю распознанный текст.', false);
          }
          voiceDispatchPending = true;
          setVoiceState('sending');
          showLive('Обрабатываю запрос…', '', true);
          send({ mode: 'text', text: createCommand(transcript), creation: creationHints() }, transcript, true);
        } else {
          applyDictation(data);
        }
      })
      .catch(function (error) {
        // The words already seen on screen are not lost: they go into the field as recognised.
        var kept = lastLive;
        hideLive();
        if (kept) insertText(kept);
        showNote(error.message + (kept ? ' Вставил распознанное без правки.' : ''), true);
      })
      .then(function () {
        voiceWorking = false;
        if (!voiceDispatchPending) {
          setVoiceState('idle');
          if (!voiceHoldEnabled) input.focus();
        }
      });
  }

  var emptyReasons = {
    too_short: 'Запись слишком короткая. Попробуйте ещё раз.',
    silence: 'Речи не слышно. Попробуйте ещё раз.',
    too_long: 'Запись длиннее ' + Math.round(voiceMax / 60) + ' минут. Надиктуйте по частям.',
    stt_error: 'Не получилось распознать. Попробуйте ещё раз.'
  };

  function insertText(text) {
    var before = input.value;
    var glue = before && !/\s$/.test(before) ? ' ' : '';
    var inserted = glue + text;
    input.value = before + inserted;
    grow();
    return { before: before, glue: glue, inserted: inserted };
  }

  function applyDictation(data) {
    if (data.empty || !data.text) {
      showNote(emptyReasons[data.empty_reason] || emptyReasons.silence, true);
      return;
    }
    var done = insertText(data.text);
    var glue = done.glue;
    lastInsert = { before: done.before, inserted: done.inserted, raw: glue + data.raw_text };
    var changes = (data.replacements || []).map(function (item) { return item.from + ' → ' + item.to; });
    if (data.cleaned && changes.length && data.raw_text !== data.text) {
      var undo = el('button', 'dp-voice-undo', 'Вернуть как сказано');
      undo.type = 'button';
      undo.addEventListener('click', function () {
        if (lastInsert && input.value === lastInsert.before + lastInsert.inserted) {
          input.value = lastInsert.before + lastInsert.raw; grow();
        }
        showNote('');
        input.focus();
      });
      showNote('Поправил: ' + changes.join(', '), false, undo);
    } else if (data.fallback_reason) {
      showNote('Вставил как распознано: правка не получилась.', false);
    } else {
      showNote('');
    }
  }

  var supportedVoice = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder && voiceUrl);
  if (micButton && supportedVoice) {
    micButton.hidden = false;
    micButton.addEventListener('click', function () { if (recorder) stopRecording(); else startRecording(); });
  }
  if (voiceHoldEnabled && !supportedVoice) {
    defaultPlaceholder = 'Напишите команду';
    input.setAttribute('placeholder', defaultPlaceholder);
    form.classList.add('voice-unavailable');
  }
  if (voiceHoldEnabled && supportedVoice) {
    // Typed editing and hold-to-talk are deliberately distinct on Android.
    // The mic is a keyboard-dismiss / mode-switch action, NOT a record button.
    function enterTypingMode() {
      if (busy || voiceWorking || recorder || micOpening) return;
      form.classList.add('voice-text-editing');
      if (modeMicButton) modeMicButton.hidden = false;
      input.setAttribute('placeholder', typingPlaceholder);
    }
    function leaveTypingMode() {
      input.blur();
      form.classList.remove('voice-text-editing');
      if (modeMicButton) modeMicButton.hidden = true;
      input.setAttribute('placeholder', defaultPlaceholder);
    }
    input.addEventListener('focus', enterTypingMode);
    input.addEventListener('blur', function () {
      form.classList.remove('voice-text-editing');
      if (modeMicButton) modeMicButton.hidden = true;
      input.setAttribute('placeholder', defaultPlaceholder);
    });
    if (modeMicButton) {
      modeMicButton.addEventListener('pointerdown', function (event) {
        // Complete the action before Android's automatic blur can remove
        // the button from the layout and swallow the subsequent click.
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        leaveTypingMode();
      });
      modeMicButton.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        leaveTypingMode(); // keyboard/assistive-technology activation
      });
    }
    if (document.activeElement === input) enterTypingMode();
    if (voiceHitbox) {
      voiceHitbox.addEventListener('touchstart', function (event) {
        // Android otherwise opens selection/callout and emits pointercancel
        // while a finger is held over the placeholder.
        if (event.cancelable) event.preventDefault();
      }, { passive: false });
    }
    form.addEventListener('pointerdown', function (event) {
      if (event.button !== 0 || event.target.closest('button') ||
          form.classList.contains('voice-text-editing') || activeHold || recorder ||
          micOpening || voiceWorking || busy || voiceDispatchPending) return;
      // Defer the Android keyboard/text-selection gesture until a short tap is known.
      if (event.pointerType === 'touch') event.preventDefault();
      activeHold = { id: event.pointerId, x: event.clientX, y: event.clientY, cancelled: false, started: false };
      if (window.VoiceGlow) {
        window.VoiceGlow.setKnobX(event.clientX);
        window.VoiceGlow.arm(); // Safari/iOS needs AudioContext resumed in a real pointer gesture.
      }
      // Keep the pointer stream attached to the non-selectable surface.
      if (event.pointerType === 'touch' && event.target.setPointerCapture) {
        try { event.target.setPointerCapture(event.pointerId); } catch (error) {}
      }
      form.classList.add('voice-arming');
      holdTimer = setTimeout(function () {
        holdTimer = null;
        if (!activeHold || activeHold.cancelled) return;
        activeHold.started = true;
        startRecording();
      }, 220);
    });
    function endHold(event, cancelled) {
      if (!activeHold || (event && event.pointerId !== activeHold.id)) return;
      var intent = activeHold;
      intent.cancelled = true;
      activeHold = null;
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
      form.classList.remove('voice-arming');
      if (recorder) {
        discardRecording = cancelled;
        stopRecording();
      } else if (micOpening) {
        // Releasing during the first permissions prompt must not silently discard intent.
        hideLive();
        setVoiceState('idle');
        if (!cancelled) showNote('После разрешения микрофона удерживайте строку ещё раз.', false);
      } else if (!intent.started && !cancelled) {
        if (window.VoiceGlow) window.VoiceGlow.hide(); // Close context armed by a short tap.
        input.focus();
      } else {
        hideLive();
        setVoiceState('idle');
      }
    }
    document.addEventListener('pointermove', function (event) {
      if (activeHold && event.pointerId === activeHold.id && window.VoiceGlow) {
        window.VoiceGlow.setKnobX(event.clientX);
      }
    });
    document.addEventListener('pointerup', function (event) { endHold(event, false); });
    document.addEventListener('pointercancel', function (event) { endHold(event, true); });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) endHold(null, true);
    });
    form.addEventListener('contextmenu', function (event) {
      if (event.target === voiceHitbox || activeHold || recorder) event.preventDefault();
    });
    form.addEventListener('selectstart', function (event) {
      if (activeHold || event.target === voiceHitbox) event.preventDefault();
    });
  }

  // Finished work comes back into the conversation by itself: ask now and then while the page is open.
  function checkUpdates() {
    if (document.hidden || busy) return;
    fetch(api + '/updates', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { if (data && data.messages && data.messages.length) applyResult(data); })
      .catch(function () {});
  }
  setInterval(checkUpdates, 8000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) checkUpdates(); });

  (state.messages || []).forEach(upsert);
  hydrated = true;
  syncConversationLayout();
  renderHints();
  toBottom();

  if (root.classList.contains('sg-create-dispatch')) {
    // On Create, restore only the last pending creation card. Do not copy the
    // full conversation from Home into the compact composition workspace.
    fetch(api + '/state', { credentials: 'same-origin',
      headers: { Accept: 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data || !Array.isArray(data.messages) ||
            feed.querySelector('.dp-msg:not(.dp-typing)')) return;
        var pending = data.messages.filter(function (message) {
          return message.role === 'assistant' && (message.blocks || []).some(function (b) {
            return b.type === 'confirm' && b.state === 'pending' && !!b.editable;
          });
        });
        if (pending.length) applyResult({ messages: [pending[pending.length - 1]] });
      }).catch(function () { /* No network means no restored card, not a page failure. */ });
  }
})();
