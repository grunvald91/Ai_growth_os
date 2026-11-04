/* Progressive enhancement for Growth V4: company picker and daily briefing. */
(function () {
  'use strict';
  var match = location.pathname.match(/\/studio\/products\/([0-9a-f-]{36})(?:\/|$)/i);
  if (!match) return;
  var companyId = match[1], base = '/studio/products/' + companyId;
  var companyButton = document.getElementById('bizBtn');
  var companyMenu = document.getElementById('sgOrgMenu');
  var companyWrapper = document.getElementById('sgOrgWrapper');
  var loadedCompanies = false;
  function closeCompany() {
    if (!companyMenu || companyMenu.hidden) return;
    companyMenu.hidden = true;
    companyButton.setAttribute('aria-expanded', 'false');
  }
  function create(tag, cls, value) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  function renderCompanies(data) {
    companyMenu.replaceChildren(create('div', 'sg-org-menu-heading', 'Выберите организацию'));
    (Array.isArray(data.items) ? data.items : []).forEach(function (item) {
      if (!/^[0-9a-f-]{36}$/i.test(item.id || '')) return;
      var link = create('a', 'sg-org-option');
      link.href = '/studio/products/' + item.id;
      link.appendChild(create('span', '', item.name || 'Компания'));
      if (item.id.toLowerCase() === companyId.toLowerCase()) {
        link.setAttribute('aria-current', 'true');
        link.appendChild(create('span', '', '✓'));
      }
      companyMenu.appendChild(link);
    });
    if (data.can_add) {
      var add = create('a', 'sg-org-footer', '+ Управление организациями');
      add.href = '/studio?choose=1';
      companyMenu.appendChild(add);
    }
  }
  if (companyButton && companyMenu) {
    companyButton.addEventListener('click', function (event) {
      event.preventDefault();
      if (!companyMenu.hidden) { closeCompany(); return; }
      companyMenu.hidden = false;
      companyButton.setAttribute('aria-expanded', 'true');
      if (!loadedCompanies) {
        companyMenu.replaceChildren(create('div', 'sg-org-menu-heading', 'Загрузка организаций…'));
        fetch(base + '/organizations.json', {credentials:'same-origin', headers:{'Accept':'application/json'}})
          .then(function (response) { if (!response.ok) throw Error('request failed'); return response.json(); })
          .then(function (data) { loadedCompanies = true; renderCompanies(data); })
          .catch(function () {
            companyMenu.replaceChildren(create('div', 'sg-org-menu-heading', 'Не удалось загрузить организации. Нажмите ещё раз.'));
          });
      }
    });
    document.addEventListener('pointerdown', function (event) {
      if (!companyWrapper.contains(event.target)) closeCompany();
    });
  }
  // The notification and "More" controls are native details. Close on outside tap.
  document.addEventListener('pointerdown', function (event) {
    document.querySelectorAll('[data-sg-notifications][open],.mobile-nav details[open]').forEach(function (details) {
      if (!details.contains(event.target)) details.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeCompany();
  });

  var drawer = document.getElementById('sgDailyDrawer');
  var backdrop = document.getElementById('sgDailyBackdrop');
  var handle = document.getElementById('sgDailyHandle');
  var closeButton = document.getElementById('sgDailyClose');
  var content = document.getElementById('sgDailyContent');
  var title = document.getElementById('sgDailyCompany');
  if (!drawer || !backdrop || !handle || !closeButton || !content) return;
  var previousFocus = null, isOpen = false;
  function closeDrawer() {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove('sg-daily-open');
    drawer.setAttribute('aria-hidden', 'true');
    handle.setAttribute('aria-expanded', 'false');
    (previousFocus && document.contains(previousFocus) ? previousFocus : handle).focus();
  }
  function showDrawer() {
    if (isOpen) return;
    previousFocus = document.activeElement === document.body ? handle : document.activeElement;
    isOpen = true;
    document.body.classList.add('sg-daily-open');
    drawer.setAttribute('aria-hidden', 'false');
    handle.setAttribute('aria-expanded', 'true');
    closeCompany();
    closeButton.focus();
  }
  function showStatus(message) {
    content.replaceChildren(create('p','sg-daily-empty',message));
  }
  function safeLink(url) {
    return typeof url === 'string' && url.startsWith(base + '/') ? url : null;
  }
  function section(name) {
    var box = create('section','sg-daily-section');
    box.appendChild(create('h3','',name));
    content.appendChild(box);
    return box;
  }
  function row(box, text, subtext, url) {
    var href = safeLink(url);
    var item = create(href ? 'a' : 'div','sg-daily-row',text);
    if (href) item.href = href;
    if (subtext) item.appendChild(create('small','',subtext));
    box.appendChild(item);
  }
  function stats(parent, name, value) {
    var box = create('div','sg-daily-stat');
    box.appendChild(create('b','',value));
    box.appendChild(create('span','',name));
    parent.appendChild(box);
  }
  function renderSummary(info) {
    content.replaceChildren();
    if (title) title.textContent = info.company || 'Организация';
    var statBox = create('div','sg-daily-stats');
    stats(statBox,'В работе',info.in_work);
    stats(statBox,'На проверке',info.review);
    stats(statBox,'По плану · 7 дней',info.planned_week);
    content.appendChild(statBox);
    var attention = section('Требует внимания');
    if (!info.attention || !info.attention.length) {
      attention.appendChild(create('p','sg-daily-empty','Событий, требующих внимания, нет.'));
    } else info.attention.forEach(function (notice) {
      row(attention, notice.title || 'Проверить задание',notice.kind === 'error' ? 'Ошибка' : 'Ожидает решения',notice.url);
    });
    var upcoming = section('Ближайшие публикации');
    if (!info.upcoming || !info.upcoming.length) {
      upcoming.appendChild(create('p','sg-daily-empty',info.has_active_plan ?
        'Ближайших публикаций в плане нет.' : 'Утверждённый контент-план отсутствует.'));
    } else info.upcoming.forEach(function (slot) {
      var date = new Date(slot.when);
      var stamp = isNaN(date.getTime()) ? 'Время не указано' :
        new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(date);
      row(upcoming,slot.topic || 'Публикация',stamp + (slot.platform ? ' · ' + slot.platform : ''),base+'/calendar');
    });
    if (info.has_draft_plan) {
      var plan = section('Контент-план');
      row(plan,'Черновик ждёт утверждения','Открыть план',base+'/plan?view=draft');
    }
  }
  function refresh() {
    showStatus('Получаю актуальное состояние компании…');
    return fetch(base + '/home-summary.json',{credentials:'same-origin',cache:'no-store',
      headers:{'Accept':'application/json'}})
      .then(function (response) { if (!response.ok) throw Error('request failed'); return response.json(); })
      .then(function (data) { renderSummary(data); return true; })
      .catch(function () {
        showStatus('Не удалось загрузить сводку. Закройте панель и откройте ещё раз.');
        return false;
      });
  }
  function localDate() {
    var now = new Date();
    return [now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
  }
  var key = 'servgo:growth:briefing:v1:' + companyId;
  function readSeen() { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function saveSeen() { try { localStorage.setItem(key,localDate()); } catch (_) {} }
  handle.addEventListener('click',function () {
    drawer.dataset.first = 'false';
    showDrawer(); refresh();
  });
  closeButton.addEventListener('click',closeDrawer);
  backdrop.addEventListener('click',closeDrawer);
  document.addEventListener('keydown',function (event) {
    if (!isOpen) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); return; }
    if (event.key !== 'Tab') return;
    var focusable = Array.from(drawer.querySelectorAll('button:not([disabled]),a[href]'));
    if (!focusable.length) {event.preventDefault(); closeButton.focus();return;}
    var first=focusable[0], last=focusable[focusable.length-1];
    if (event.shiftKey && document.activeElement === first) {event.preventDefault();last.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first.focus();}
  });
  if (readSeen() !== localDate()) {
    drawer.dataset.first = 'true';
    // Allow initial offscreen style to paint so the very first entrance slides in.
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        showDrawer();
        refresh().then(function (ok) { if (ok) saveSeen(); });
      });
    });
  }
})();