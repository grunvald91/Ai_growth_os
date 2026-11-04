(() => {
  'use strict';
  document.querySelectorAll('[data-ym-company-form]').forEach(form => {
    const name = form.querySelector('[name=name]');
    const site = form.querySelector('[name=website], [name=link]');
    const description = form.querySelector('[name=about], [name=description]');
    const city = form.querySelector('[data-ym-city]');
    const button = form.querySelector('[data-ym-find]');
    const result = form.querySelector('[data-ym-results]');
    const evidence = form.querySelector('[name=yandex_org]');
    if (!name || !city || !button || !result || !evidence) return;
    const productMatch = form.action.match(/\/studio\/products\/([0-9a-f-]{36})\//i);
    const url = productMatch
      ? '/studio/products/' + productMatch[1] + '/organizations/search'
      : '/studio/organizations/search';
    function make(tag, text, parent) {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      parent.append(element);
      return element;
    }
    button.addEventListener('click', async () => {
      if (name.value.trim().length < 2 || city.value.trim().length < 2) {
        result.textContent = 'Укажите название организации и город';
        return;
      }
      result.textContent = 'Ищу в Яндекс Картах…';
      button.disabled = true;
      try {
        const response = await fetch(url + '?' + new URLSearchParams({
          query: name.value.trim(), city: city.value.trim()
        }), {credentials:'same-origin'});
        const data = await response.json();
        if (!response.ok) throw Error(data.detail || 'Сервис поиска недоступен');
        result.replaceChildren();
        if (!(data.organizations || []).length) {
          result.textContent = 'Совпадений не найдено';
          return;
        }
        (data.organizations || []).forEach(org => {
          const row = make('div', undefined, result);
          row.style = 'padding:10px 0;border-bottom:1px solid var(--sg-line,#ddd)';
          make('strong', org.name, row);
          make('div', [org.address, (org.categories || []).join(', ')].filter(Boolean).join(' · '), row);
          const select = make('button', 'Использовать сведения', row);
          select.type = 'button';
          select.className = 'btn small';
          select.addEventListener('click', () => {
            name.value = org.name;
            if (site && org.website && !site.value.trim()) site.value = org.website;
            const summary = [
              'Данные Яндекс Карт по выбранной организации:',
              'Название: ' + org.name,
              org.address ? 'Адрес: ' + org.address : '',
              (org.categories || []).length ? 'Категории: ' + org.categories.join(', ') : ''
            ].filter(Boolean).join('\n');
            if (description && !description.value.trim()) description.value = summary;
            evidence.value = JSON.stringify({
              id: org.id, name: org.name, address: org.address,
              website: org.website, categories: org.categories, maps_url: org.maps_url
            });
            result.textContent = 'Данные «' + org.name + '» подставлены. Проверьте и подтвердите при создании профиля.';
          });
          const link = make('a', ' Открыть в Картах', row);
          link.href = org.maps_url || org.website || '#';
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
        });
      } catch (error) {
        result.textContent = error.message || 'Ошибка поиска';
      } finally {
        button.disabled = false;
      }
    });
  });
})();