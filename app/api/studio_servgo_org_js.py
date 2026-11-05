"""Client-side company chooser for the canonical ServGo SSO Studio.

The browser's existing ServGo JWT authorizes listing and SSO-ticket issuance.
Growth never uses browser-supplied company IDs as permissions: both API calls
validate the JWT/ownership and the landing verifies the current browser identity.
The legacy Growth host does not load this script.
"""

JS = r"""
(() => {
  'use strict';
  function init(mount, metadata = null) {
  if (!mount) return;
  const compact = metadata !== null;

  const legacy = compact ? null : document.getElementById('sg-growth-connected');
  const expectedUser = String(compact ? metadata.unified_user_id : mount.dataset.userId || '');
  const linked = new Set(compact ? (metadata.linked_business_ids || []).map(String) : (mount.dataset.linkedBusinesses || '').split(',').filter(Boolean));
  let state = mount.querySelector('[role="status"]') || document.createElement('p');
  if (compact) { state.className = 'sg-org-menu-heading'; state.setAttribute('role', 'status'); mount.prepend(state); }

  // The canonical picker is served as Growth HTML, not by the React SPA.
  // Explicit ServGo theme preference therefore needs applying here as well.
  try {
    const preference = localStorage.getItem('servgo-theme') || 'system';
    const dark = preference === 'dark' ||
      (preference === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (_) { /* CSS still honors the system theme */ }

  const showMessage = (message, allowLogin = false) => {
    if (!state) return;
    state.textContent = message;
    if (allowLogin) {
      const a = document.createElement('a');
      a.href = '/login?next=%2Fworkspace%2Fstudio-entry';
      a.textContent = 'Войти через ServGo';
      a.className = 'sg-company-back';
      state.appendChild(document.createTextNode(' '));
      state.appendChild(a);
    }
  };

  function jwtUserId(token) {
    try {
      const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const payload = JSON.parse(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '=')));
      return String(payload.sub || '');
    } catch (_) {
      return '';
    }
  }

  const bearer = (() => {
    try { return localStorage.getItem('token') || ''; }
    catch (_) { return ''; }
  })();

  // A stale JWT from another signed-in tab must not list that user's businesses
  // inside the current Growth SSO session. Auth still happens on the server.
  if (!bearer || !expectedUser || jwtUserId(bearer) !== expectedUser) {
    showMessage('Для просмотра всех организаций подтвердите текущий вход ServGo.', true);
    return;
  }

  const headers = { Authorization: 'Bearer ' + bearer };
  const text = (tag, className, value) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = String(value);
    return node;
  };

  const show = (companies) => {
    if (!Array.isArray(companies)) throw new Error('Invalid company list');
    const items = document.createElement('div');
    items.className = compact ? 'sg-org-options' : 'sg-company-items';
    const unique = new Set();
    let connectedCount = 0;
    // Existing platform-wide Growth project is a separate, protected record,
    // not a newly created BookingBot business. The server emits its product
    // UUID only when this SSO user has a VERIFIED linked operator account.
    const platformId = compact ? metadata.platform_product?.id || '' : mount.dataset.platformProduct || '';
    const platformName = compact ? metadata.platform_product?.name || '' : mount.dataset.platformName || '';
    if (platformId && platformName) {
      const platformButton = text('a', compact ? 'sg-org-option' : 'sg-company-choice', '');
      platformButton.href = '/studio/products/' + encodeURIComponent(platformId);
      if (compact && metadata.current_product_id === platformId) platformButton.setAttribute('aria-current', 'true');
      if (!compact) platformButton.appendChild(text('span', 'sg-company-avatar', 'S'));
      const platformCopy = text('span', compact ? 'sg-org-copy' : 'sg-company-choice-copy', '');
      platformCopy.appendChild(text('strong', '', platformName));
      if (!compact) platformCopy.appendChild(text('small', '', 'Платформа · существующий проект администратора'));
      platformButton.appendChild(platformCopy);
      if (!compact) platformButton.appendChild(text('span', 'sg-company-arrow', '›'));
      items.appendChild(platformButton);
      connectedCount += 1;
    }
    for (const company of companies) {
      if (!company || !Number.isSafeInteger(company.id) || company.id <= 0 ||
          typeof company.name !== 'string' || !company.name.trim() ||
          unique.has(company.id)) throw new Error('Invalid company');
      unique.add(company.id);
      const connected = linked.has(String(company.id));
      if (connected) connectedCount++;
      const button = text('button', compact ? 'sg-org-option' : 'sg-company-choice sg-company-action', '');
      button.type = 'button';
      if (compact && (metadata.current_business_ids || []).includes(company.id)) button.setAttribute('aria-current', 'true');
      if (!compact) button.appendChild(text('span', 'sg-company-avatar', company.name.trim().slice(0, 1).toUpperCase()));
      const copy = text('span', compact ? 'sg-org-copy' : 'sg-company-choice-copy', '');
      copy.appendChild(text('strong', '', company.name));
      if (!compact || !connected) copy.appendChild(text('small', '', compact ? 'Подключить маркетинг' : connected ? 'Открыть маркетинг' : 'Подключить маркетинг для организации'));
      button.appendChild(copy);
      if (!compact) button.appendChild(text('span', 'sg-company-arrow', '›'));
      button.addEventListener('click', async () => {
        button.disabled = true;
        showMessage('Проверяем доступ к выбранной организации…');
        try {
          const resp = await fetch('/api/studio/ticket', {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify({ business_id: company.id })
          });
          if (!resp.ok) throw new Error('Permission denied');
          const data = await resp.json();
          const url = new URL(data.url);
          if (url.origin !== window.location.origin || url.pathname !== '/studio/sso' ||
              !url.searchParams.get('t')) throw new Error('Invalid Studio SSO URL');
          window.location.assign(url.href);
        } catch (_) {
          button.disabled = false;
          showMessage('Не удалось открыть маркетинг. Проверьте вход и повторите.', true);
        }
      });
      items.appendChild(button);
    }
    if (compact) {
      if (!companies.length && !platformId) items.appendChild(text('p', 'sg-org-menu-heading', 'Нет доступных организаций.'));
      state = text('div', 'sg-org-menu-heading', 'Выберите организацию');
      state.setAttribute('role', 'status');
      mount.replaceChildren(state, items);
      return;
    }
    mount.replaceChildren(
      text('p', 'sg-company-caption',
           'Доступных организаций и проектов: ' + (companies.length + (platformId ? 1 : 0)) +
           ' · Подключено к маркетингу: ' + connectedCount),
      items
    );
    if (!companies.length && !platformId) {
      mount.appendChild(text('p', 'sg-company-empty', 'У этого аккаунта пока нет доступных организаций.'));
    }
    if (legacy) legacy.hidden = true;
  };

  fetch('/api/studio/businesses', {
    method: 'GET', credentials: 'same-origin', cache: 'no-store', headers
  }).then(async (resp) => {
    if (!resp.ok) throw new Error('Unable to list ServGo companies');
    return await resp.json();
  }).then(show).catch(() => {
    // Do not misreport the two linked Growth products as the full ServGo list.
    showMessage('Не удалось получить все организации ServGo. Ниже показаны только уже подключённые.', true);
  });
  }
  window.ServGoOrganizations = init;
  init(document.getElementById('sg-servgo-organisations'));
})();
"""
