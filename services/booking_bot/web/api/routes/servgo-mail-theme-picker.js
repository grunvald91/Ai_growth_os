/* Applies the same Light/Dark/System setting to both ServGo Mail dashboards. */
document.addEventListener('DOMContentLoaded', function () {
  var picker = document.getElementById('servgo-mail-theme');
  var theme = window.ServGoTheme;
  if (!picker || !theme) return;
  function sync() { picker.value = theme.getPreference(); }
  sync();
  picker.addEventListener('change', function () { theme.setPreference(picker.value); });
  window.addEventListener('servgo-themechange', sync);
});
