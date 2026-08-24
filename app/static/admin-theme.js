(function () {
  var storageKey = 'wmata-admin-theme';
  var root = document.documentElement;
  var buttons = document.querySelectorAll('.theme-toggle');

  function currentTheme() {
    return root.classList.contains('admin-theme-dark') ? 'dark' : 'light';
  }

  function applyTheme(theme, persist) {
    var dark = theme === 'dark';
    root.classList.toggle('admin-theme-dark', dark);
    for (var index = 0; index < buttons.length; index += 1) {
      buttons[index].classList.toggle('is-dark', dark);
      buttons[index].setAttribute('aria-checked', dark ? 'true' : 'false');
    }
    if (persist) {
      try { localStorage.setItem(storageKey, dark ? 'dark' : 'light'); } catch (error) { /* unavailable */ }
    }
  }

  for (var index = 0; index < buttons.length; index += 1) {
    buttons[index].addEventListener('click', function () {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark', true);
    });
  }
  applyTheme(currentTheme(), false);
}());
