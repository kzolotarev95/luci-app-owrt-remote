/* One request at a time; the router owns the job across reloads/tunnel restarts. */
(function () {
  'use strict';
  var dialog = document.getElementById('routerUpdateDialog');
  if (!dialog) return;
  var openers = Array.from(document.querySelectorAll('[data-router-update-open]'));
  var topButton = document.querySelector('.routerUpdateButton');
  var check = dialog.querySelector('[data-router-update-check]');
  var install = dialog.querySelector('[data-router-update-install]');
  var message = dialog.querySelector('[data-router-update-message]');
  var version = dialog.querySelector('[data-router-update-version]');
  var job = null, request = null, timer = null, disposed = false, failures = 0, reloadTimer = null, pendingInstall = false;
  var busyStates = ['checking', 'downloading', 'installing'];
  var endpoint = new URL(window.location.href);
  endpoint.searchParams.set('action', 'panel_update');
  // Keep both the access prefix and web key when opened through Hub's proxy.
  endpoint.hash = '';
  function busy() { return job && busyStates.indexOf(job.status) >= 0; }
  function schedule(delay) {
    clearTimeout(timer);
    if (!disposed) timer = setTimeout(function () { poll(); }, delay);
  }
  function paint(data) {
    job = data;
    message.textContent = data.message || 'Проверить обновление панели';
    message.dataset.state = data.status;
    version.textContent = data.installed_version ? 'Установлено: ' + data.installed_version +
      (data.version ? ' · В GitHub: ' + data.version : '') : '';
    check.disabled = !!busy();
    install.hidden = data.status !== 'available';
    install.disabled = !!busy();
    topButton.textContent = busy() ? (data.status === 'checking' ? 'Проверка обновления…' : 'Обновление панели…') :
      data.status === 'current' ? 'Установлена последняя версия' :
      data.status === 'available' ? 'Доступно обновление' :
      data.status === 'local_newer' ? 'Установлена локальная версия' :
      data.status === 'failed' ? 'Ошибка обновления' : 'Проверить обновление панели';
    topButton.dataset.state = data.status;
    if (busy()) schedule(1500);
    if (data.status === 'succeeded' && !reloadTimer) {
      // A completed job survives F5; reload each result only once per tab.
      var key = 'owrt-router-update:' + endpoint.pathname + ':' + data.id;
      var seen = false;
      try { seen = sessionStorage.getItem(key) === '1'; sessionStorage.setItem(key, '1'); } catch (_) { seen = true; }
      if (!seen) reloadTimer = setTimeout(function () { window.location.reload(); }, 1800);
    }
  }
  function api(operation, revision) {
    if (request) return request;
    // A lost POST response must still lead to status polling: the router may
    // already be installing even though this browser never received its ID.
    if (operation === 'install' && job) {
      paint(Object.assign({}, job, {status:'installing', message:'Запуск обновления панели…'}));
    }
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    var url = new URL(endpoint.href);
    url.searchParams.set('operation', operation);
    var options = { cache: 'no-store', credentials: 'same-origin', signal: controller.signal };
    if (operation !== 'status') {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/x-www-form-urlencoded', 'X-OWRT-Panel-Update': '1'};
      options.body = new URLSearchParams({revision: revision || ''}).toString();
    }
    request = fetch(url.href, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) throw new Error(data.error || 'HTTP ' + response.status);
        failures = 0;
        if (!disposed) paint(data);
        return data;
      });
    }).catch(function (error) {
      if (disposed) return null;
      failures++;
      message.textContent = busy() ? 'Связь временно прервана. Ожидаем результат обновления…' :
        'Не удалось проверить обновление: ' + error.message;
      if (busy() && failures < 90) schedule(2000);
      else {
        check.disabled = false;
        topButton.textContent = 'Проверить обновление панели';
        if (busy()) message.textContent = 'Результат пока недоступен. Нажмите «Проверить обновление», когда соединение восстановится.';
      }
      return null;
    }).finally(function () { clearTimeout(timeout); request = null; });
    return request;
  }
  function poll() { return api('status'); }
  function checkNow() {
    if (busy()) return poll();
    if (request) return request.then(function () { if (!busy()) return api('check'); });
    clearTimeout(timer);
    return api('check');
  }
  openers.forEach(function (button) {
    button.addEventListener('click', function () {
      if (!dialog.open) dialog.showModal();
      poll().then(function (data) {
        if (data && (data.status === 'idle' || data.status === 'failed' || data.status === 'succeeded')) checkNow();
      });
    });
  });
  dialog.querySelector('[data-router-update-close]').addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) {
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }});
  check.addEventListener('click', checkNow);
  install.addEventListener('click', function () {
    if (pendingInstall || !job || job.status !== 'available') return;
    var revision = job.revision;
    pendingInstall = true;
    install.disabled = true; check.disabled = true;
    (request || Promise.resolve()).then(function () {
      pendingInstall = false;
      if (job && job.status === 'available' && job.revision === revision) api('install', revision);
      else if (job) paint(job);
    });
  });
  window.addEventListener('pagehide', function () { disposed = true; clearTimeout(timer); clearTimeout(reloadTimer); });
  window.addEventListener('pageshow', function (event) { if (event.persisted) { disposed = false; poll(); } });
  poll().then(function (data) {
    if (data && (data.status === 'idle' || data.status === 'succeeded' || (!busy() && Date.now() / 1000 - (data.updated_at || 0) > 600))) checkNow();
  });
})();
