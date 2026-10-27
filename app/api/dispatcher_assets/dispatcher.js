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

  // Every request is a POST of JSON; the Origin header is added by the browser.
  function post(path, body) {
    return fetch(api + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error((data && data.detail) || 'Не получилось. Попробуйте ещё раз.');
        return data;
      });
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

  function renderLinks(items) {
    var list = el('div', 'dp-links');
    items.forEach(function (item) {
      var link = el('a', 'dp-link', item.label);
      link.href = item.href;
      list.appendChild(link);
    });
    return list;
  }

  function renderTable(block) {
    var wrap = el('div', 'dp-tablewrap');
    var table = el('table', 'dp-table');
    var head = el('tr');
    block.columns.forEach(function (name) { head.appendChild(el('th', '', name)); });
    var thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
    var body = el('tbody');
    block.rows.forEach(function (row) {
      var tr = el('tr');
      row.forEach(function (cell, index) {
        var td = el('td', '', cell);
        td.setAttribute('data-label', block.columns[index]);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body); wrap.appendChild(table);
    return wrap;
  }

  function renderTask(block) {
    var card = el('a', 'dp-task');
    card.href = block.href;
    card.appendChild(el('span', 'dp-task-title', block.title));
    card.appendChild(el('span', 'dp-task-status', statusWord(block.status)));
    return card;
  }

  // A ready material: kind and status on top, a short text, a link to the full view. Its buttons come as chips.
  function renderMaterial(block) {
    var card = el('div', 'dp-material');
    var top = el('div', 'dp-material-top');
    top.appendChild(el('span', 'dp-material-kind', block.kind));
    top.appendChild(el('span', 'dp-material-status', block.status));
    card.appendChild(top);
    card.appendChild(el('strong', 'dp-material-title', block.title));
    if (block.excerpt) card.appendChild(el('p', 'dp-material-text', block.excerpt));
    var open = el('a', 'dp-link', 'Открыть полностью');
    open.href = block.href;
    card.appendChild(open);
    return card;
  }

  function statusWord(status) {
    return ({
      queued: 'В очереди', running: 'Готовится', waiting_approval: 'Ждёт подтверждения',
      completed: 'Готово', failed: 'Ошибка', cancelled: 'Отменено'
    })[status] || status;
  }

  function renderConfirm(block) {
    var card = el('div', 'dp-confirm' + (block.paid ? ' paid' : ''));
    card.appendChild(el('strong', 'dp-confirm-title', block.title));
    if (block.paid) card.appendChild(el('span', 'dp-badge', 'Платно'));
    var list = el('ul', 'dp-confirm-list');
    block.details.forEach(function (line) { list.appendChild(el('li', '', line)); });
    card.appendChild(list);
    if (block.state === 'pending') {
      var actions = el('div', 'dp-confirm-actions');
      var no = el('button', 'dp-btn ghost', block.cancel_label); no.type = 'button';
      var yes = el('button', 'dp-btn primary', block.confirm_label); yes.type = 'button';
      no.addEventListener('click', function () { answer(block, false, no, yes); });
      yes.addEventListener('click', function () { answer(block, true, no, yes); });
      actions.appendChild(no); actions.appendChild(yes);
      card.appendChild(actions);
    } else {
      card.appendChild(el('span', 'dp-confirm-state', block.state === 'confirmed' ? 'Подтверждено' : 'Отменено'));
    }
    return card;
  }

  function answer(block, accept, no, yes) {
    no.disabled = yes.disabled = true;
    post('/confirm', { confirmation_id: block.confirmation_id, accept: accept, via: 'button' })
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
    (message.blocks || []).forEach(function (block) {
      if (block.type === 'text') bubble.appendChild(el('p', 'dp-text', block.text));
      else if (block.type === 'chips') bubble.appendChild(renderChips(block.items, sendChip));
      else if (block.type === 'links') bubble.appendChild(renderLinks(block.items));
      else if (block.type === 'table') bubble.appendChild(renderTable(block));
      else if (block.type === 'task') bubble.appendChild(renderTask(block));
      else if (block.type === 'material') bubble.appendChild(renderMaterial(block));
      else if (block.type === 'confirm') bubble.appendChild(renderConfirm(block));
    });
    return bubble;
  }

  function upsert(message) {
    var node = renderMessage(message);
    var existing = feed.querySelector('[data-id="' + message.id + '"]');
    if (existing) feed.replaceChild(node, existing); else feed.appendChild(node);
  }

  function renderHints() {
    chipsBox.textContent = '';
    if (state.hints && state.hints.length) chipsBox.appendChild(renderChips(state.hints, sendChip));
  }

  function toBottom() { feed.scrollTop = feed.scrollHeight; }

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
    toBottom();
  }

  function notify(text) {
    var note = el('div', 'dp-msg bot dp-error', text);
    feed.appendChild(note);
    toBottom();
  }

  function applyResult(data) {
    (data.messages || []).forEach(upsert);
    if (data.hints) { state.hints = data.hints; renderHints(); }
    root.classList.toggle('has-feed', feed.children.length > 0);
    toBottom();
  }

  // ---- sending ----------------------------------------------------------------------------

  function send(body, echo) {
    if (busy) return;
    var key = newKey();
    body.client_key = key;
    var echoNode = null;
    if (echo) {
      echoNode = renderMessage({ id: 'local-' + key, role: 'user', text: echo });
      feed.appendChild(echoNode);
      root.classList.add('has-feed');
    }
    setBusy(true);
    post('/messages', body).then(function (data) {
      if (echoNode) echoNode.remove();
      applyResult(data);
    }).catch(function (error) {
      notify(error.message);
    }).then(function () { setBusy(false); });
  }

  function sendChip(chip) {
    send({ mode: 'chip', chip: { label: chip.label, capability: chip.capability, params: chip.params || {} } }, chip.label);
  }

  function sendText() {
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = '';
    grow();
    send({ mode: 'text', text: text }, text);
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
    post('/new', {}).then(function (data) {
      feed.textContent = ''; root.classList.remove('has-feed');
      state.hints = data.hints || []; renderHints(); input.focus();
    }).catch(function (error) { notify(error.message); });
  });

  // ---- voice input: speak, the text lands in the field, the person sends it themselves -----

  var micButton = document.getElementById('dp-mic');
  var voiceNote = document.getElementById('dp-voice-note');
  var voiceUrl = root.getAttribute('data-voice-url');
  var previewUrl = root.getAttribute('data-voice-preview-url');
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
    micButton.classList.toggle('recording', recording);
    micButton.setAttribute('aria-pressed', recording ? 'true' : 'false');
    micButton.setAttribute('aria-label', recording ? 'Закончить запись' : 'Надиктовать голосом');
    micButton.disabled = mode === 'processing';
    input.setAttribute('placeholder', mode === 'processing' ? 'Распознаю…' : defaultPlaceholder);
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
    if (voiceWorking || recorder || micOpening) return;
    showNote('');
    micOpening = true;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      micOpening = false;
      recordStream = stream;
      var type = mimeCandidate();
      try { recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream); }
      catch (error) { releaseStream(); showNote('Этот браузер не умеет записывать звук.', true); return; }
      recordChunks = [];
      recorder.addEventListener('dataavailable', function (event) { if (event.data && event.data.size) recordChunks.push(event.data); });
      recorder.addEventListener('stop', onRecorded);
      try { recorder.start(1000); }
      catch (error) { recorder = null; releaseStream(); showNote('Не получилось включить запись.', true); return; }
      recordStarted = Date.now();
      recordingId += 1; lastLive = '';
      showLive('Слушаю…', '', false);
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
      var denied = error && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
      showNote(denied ? 'Нет доступа к микрофону. Разрешите его для этого сайта в настройках браузера.'
                      : 'Микрофон не найден или занят другим приложением.', true);
    });
  }

  function stopRecording() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }

  function onRecorded() {
    var type = (recorder && recorder.mimeType) || 'audio/webm';
    var millis = Date.now() - recordStarted;
    stopPreview();
    recordingId += 1;
    recorder = null;
    showLive('Правлю текст…', lastLive, true);
    releaseStream();
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
      .then(function (data) { hideLive(); applyDictation(data); })
      .catch(function (error) {
        // The words already seen on screen are not lost: they go into the field as recognised.
        var kept = lastLive;
        hideLive();
        if (kept) insertText(kept);
        showNote(error.message + (kept ? ' Вставил распознанное без правки.' : ''), true);
      })
      .then(function () { voiceWorking = false; setVoiceState('idle'); input.focus(); });
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

  if (micButton && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder && voiceUrl) {
    micButton.hidden = false;
    micButton.addEventListener('click', function () { if (recorder) stopRecording(); else startRecording(); });
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
  root.classList.toggle('has-feed', feed.children.length > 0);
  renderHints();
  toBottom();
})();
