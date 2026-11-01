/* ServGo Air theme protocol v1 — mirrors booking_bot/src/utils/servgoTheme.ts.
 * Preferences are per-origin until the unified servgo.ru gateway is deployed.
 */
(function () {
  'use strict';
  var KEY = 'servgo-theme';
  var LEGACY = ['schedTheme', 'servgo-desktop-theme'];
  var ready = false;

  function read(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }
  function valid(value) { return value === 'light' || value === 'dark' || value === 'system'; }
  function preference() {
    var selected = read(KEY);
    if (valid(selected)) return selected;
    for (var i = 0; i < LEGACY.length; i++) {
      var old = read(LEGACY[i]);
      if (old === 'light' || old === 'dark') return old;
    }
    return 'system';
  }
  function resolve(mode) {
    if (mode === 'dark' || mode === 'light') return mode;
    var tg = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.colorScheme;
    if (tg === 'dark' || tg === 'light') return tg;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function apply() {
    var mode = preference(), resolved = resolve(mode);
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = mode;
    document.documentElement.style.colorScheme = resolved;
    if (document.body) document.body.classList.toggle('dark', resolved === 'dark');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', resolved === 'dark' ? '#111a29' : '#ffffff');
    try { window.dispatchEvent(new CustomEvent('servgo-themechange', {detail: {preference: mode, resolved: resolved}})); } catch (_) {}
    return resolved;
  }
  function set(mode) {
    if (!valid(mode)) return;
    try { localStorage.setItem(KEY, mode); } catch (_) {}
    apply();
  }
  window.ServGoTheme = { getPreference: preference, resolve: resolve, setPreference: set, apply: apply };
  apply();

  document.addEventListener('DOMContentLoaded', function () { apply(); }, {once: true});
  window.addEventListener('storage', function (event) {
    if (event.key === KEY || LEGACY.indexOf(event.key) !== -1) apply();
  });
  var media = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  if (media && media.addEventListener) media.addEventListener('change', function () {
    if (preference() === 'system') apply();
  });
  var webApp = window.Telegram && window.Telegram.WebApp;
  if (webApp && webApp.onEvent) webApp.onEvent('themeChanged', function () {
    if (preference() === 'system') apply();
  });
})();
