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

  (state.messages || []).forEach(upsert);
  root.classList.toggle('has-feed', feed.children.length > 0);
  renderHints();
  toBottom();
})();
