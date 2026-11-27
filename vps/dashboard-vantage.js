/* Shares the dashboard's existing snapshots and interaction handlers. */
function initHubPanelVisibility() {
  const settings = {map:['hubMapBody','hubMapHidden'], availability:['hubAvailabilityBody','hubAvailabilityHidden'], disk:['hubDiskBody','hubDiskHidden']};
  const restore = () => {
    Object.entries(settings).forEach(([key,[id,attribute]]) => {
      let visible = document.documentElement.dataset[attribute] !== '1';
      try { visible = localStorage.getItem('owrtRemote:panelVisible:'+key) !== '0'; } catch (_) {}
      document.documentElement.dataset[attribute] = visible ? '0' : '1';
      const body = document.getElementById(id);
      if (body) body.hidden = !visible;
      document.querySelectorAll('[data-hub-panel-toggle="'+key+'"]').forEach(button => {
        button.textContent = visible ? 'Скрыть' : 'Открыть';
        button.setAttribute('aria-expanded', String(visible));
        button.setAttribute('aria-label', (visible ? 'Скрыть' : 'Открыть')+' — '+({map:'Карта подключений',availability:'Доступность роутеров',disk:'Диск VPS'})[key]);
      });
      if (key === 'map') window.hubDashboardNetwork?.setCollapsed(!visible);
    });
  };
  document.querySelectorAll('[data-hub-panel-toggle]').forEach(button => button.addEventListener('click', () => {
    const key = button.dataset.hubPanelToggle, visible = button.getAttribute('aria-expanded') !== 'true';
    document.documentElement.dataset[settings[key][1]] = visible ? '0' : '1';
    try { localStorage.setItem('owrtRemote:panelVisible:'+key, visible ? '1' : '0'); } catch (_) {}
    restore();
  }));
  restore();
  window.addEventListener('pageshow', restore);
  window.addEventListener('storage', event => { if (!event.key || event.key.startsWith('owrtRemote:panelVisible:')) restore(); });
}

function setHubStatisticsVisible(visible, options = {}) {
  const shown = Boolean(visible);
  document.documentElement.dataset.hubStatsHidden = shown ? '0' : '1';
  const panel = document.getElementById('routerStats');
  if (panel) panel.hidden = !shown;
  const label = shown ? 'Скрыть статистику' : 'Открыть статистику';
  const legacy = document.getElementById('routerStatsToggle');
  if (legacy) { legacy.setAttribute('aria-expanded', String(shown)); legacy.textContent = label; }
  document.querySelectorAll('[data-hub-action="stats"]').forEach(button => {
    button.setAttribute('aria-expanded', String(shown));
    button.querySelector('span').textContent = label;
  });
  if (options.persist !== false) {
    try { localStorage.setItem('owrtRemote:routerStatsVisible', shown ? '1' : '0'); } catch (_) {}
  }
}

function initHubStatisticsVisibility() {
  const restore = () => {
    let visible = document.documentElement.dataset.hubStatsHidden !== '1';
    try { visible = localStorage.getItem('owrtRemote:routerStatsVisible') !== '0'; } catch (_) {}
    setHubStatisticsVisible(visible, {persist:false});
  };
  restore();
  document.getElementById('routerStatsToggle')?.addEventListener('click', () => {
    setHubStatisticsVisible(document.getElementById('routerStats').hidden);
  });
  window.addEventListener('pageshow', restore);
  window.addEventListener('storage', event => {
    if (event.key === 'owrtRemote:routerStatsVisible' || event.key === null) restore();
  });
}

function updateHubDiskBanner(widget, metric, failed, formatBytes) {
  const data = metric && !failed ? metric : {};
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const values = {used:data.used_bytes, total:data.total_bytes, free:data.free_bytes};
  if (!number(values.free) && number(values.used) && number(values.total)) {
    values.free = Math.max(0, values.total-values.used);
  }
  widget.querySelectorAll('[data-vps-disk-bytes]').forEach(node => {
    const value = values[node.dataset.vpsDiskBytes];
    node.textContent = number(value) ? formatBytes(value) : '—';
  });
  widget.dataset.diskLevel = !number(data.percent) ? 'unknown'
    : data.percent >= 90 ? 'critical' : data.percent >= 80 ? 'warning' : 'normal';
}

function sortHubRouterList(list) {
  return (Array.isArray(list) ? list : []).slice().sort((a,b) =>
    (routerStatusFilterValue === 'all' ? groupName(a.group_id).localeCompare(groupName(b.group_id), 'ru') : 0)
    || (Number.isInteger(a.display_order) ? a.display_order : Number.MAX_SAFE_INTEGER) - (Number.isInteger(b.display_order) ? b.display_order : Number.MAX_SAFE_INTEGER)
    || String(a.name || a.id).localeCompare(String(b.name || b.id), 'ru'));
}

function initHubVantageDashboard() {
  const host = document.getElementById('hubNetworkMap');
  if (!host || window.hubDashboardNetwork) return;
  window.hubDashboardNetwork = window.createHubNetworkMap(host, {
    onOpenRouter(id) {
      const findCard = () => Array.from(document.querySelectorAll('article[data-router-id]'))
        .find(card => card.dataset.routerId === id);
      let card = findCard();
      if (!card) return;
      const group = card.closest('.routerGroupCards');
      if (group?.hidden) {
        group.closest('.routerGroupSection')?.querySelector('[data-router-group-toggle]')?.click();
        card = findCard();
      }
      if (!card) return;
      card.scrollIntoView({block:'center', behavior:'instant'});
      const body = card.querySelector('.cardBody');
      if (body) setCardDetailsExpanded(body, true);
      card.querySelector('[data-card-toggle]')?.focus({preventScroll:true});
    }
  });
  initHubPanelVisibility();
  const button = document.getElementById('hubMapMotion');
  let motion = true;
  const savedKey = 'owrtRemote:hubMapMotion';
  try { motion = localStorage.getItem(savedKey) !== 'off'; } catch (_) {}
  const syncMotion = () => {
    window.hubDashboardNetwork.setMotion(motion);
    button.setAttribute('aria-pressed', String(!motion));
    button.title = motion ? 'Приостановить анимацию карты' : 'Включить анимацию карты';
    button.setAttribute('aria-label', button.title);
    button.querySelector('b').textContent = motion ? 'Ⅱ' : '▷';
    button.querySelector('span').textContent = motion ? 'Анимация' : 'На паузе';
  };
  button.addEventListener('click', () => {
    motion = !motion;
    try { localStorage.setItem(savedKey, motion ? 'on' : 'off'); } catch (_) {}
    syncMotion();
  });
  syncMotion();
  updateHubVantageDashboard(window.ROUTERS || [], filteredRouters(window.ROUTERS || []));
  window.addEventListener('pagehide', () => window.hubDashboardNetwork.setVisible(false));
  window.addEventListener('pageshow', () => window.hubDashboardNetwork.setVisible(true));
}

function updateHubVantageDashboard(allRouters, visibleRouters) {
  const rows = Array.isArray(allRouters) ? allRouters : [];
  const online = rows.filter(row => Boolean(row.online)).length;
  const offline = rows.length - online;
  const intro = document.getElementById('hubVantageIntro');
  if (intro) {
    intro.dataset.attention = String(offline > 0);
    document.getElementById('hubVantageTitle').textContent = !rows.length ? 'Добавьте первый роутер'
      : offline ? 'Есть роутеры без связи' : 'Все роутеры на связи';
    document.getElementById('hubVantageSummary').textContent = !rows.length
      ? 'Подключённые устройства и их состояние появятся здесь.'
      : `${online} из ${rows.length} на связи · данные от агентов Heartbeat`;
  }
  window.hubDashboardNetwork?.update(sortHubRouterList(visibleRouters));
}

function renderHubRouterQuickMetrics(router) {
  const status = router.status || {};
  const online = Boolean(router.online);
  const memory = String(status.memory || '');
  const kb = memory.match(/(\d+)\s*\/\s*(\d+)\s*kB/i);
  const mb = memory.match(/([\d.]+)\s*MB\s*free\s*\/\s*([\d.]+)\s*MB/i);
  const match = kb || mb;
  const used = online && match && Number(match[2]) > 0
    ? Math.max(0, Math.min(100, Math.round((1 - Number(match[1]) / Number(match[2])) * 100))) + '%' : '—';
  const xray = online ? String(status.xray || 'unknown') : 'unknown';
  const temperature = online ? String(temperatureValueForRouter(router)).trim() : '';
  const temp = /^-?\d+(?:[.,]\d+)?(?:\s*°?\s*C)?$/i.test(temperature)
    ? temperature.replace(/\s*°?\s*C$/i, '') + ' °C' : '—';
  return `<div class="routerQuickMetrics"><div><span>Xray</span><b data-xray="${escapeAttr(xray)}">${escapeHtml(online ? statusRu(xray) : 'Нет связи')}</b></div><div><span>ОЗУ</span><b>${used}</b></div><div><span>Температура</span><b>${escapeHtml(temp)}</b></div></div>`;
}

/* Five minutes of actual observations; a bounded tab cache survives reload. */
window.HubKpiSparks = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const WINDOW = 5 * 60 * 1000, BUCKET = 5000, GAP = 15000;
  const labels = {total:'Всего роутеров',online:'В сети',offline:'Не в сети',xray:'Xray запущен',cpu:'ЦП VPS',memory:'ОЗУ VPS'};
  const history = new Map(Object.keys(labels).map(key => [key, []]));
  let storageKey = '', savedBucket = -1;
  const validValue = (key, value) => typeof value === 'number' && Number.isFinite(value) && value >= 0
    && (!(key === 'cpu' || key === 'memory') || value <= 100);
  function restore(scope) {
    storageKey = 'owrtRemote:kpiHistory:v1:' + encodeURIComponent(String(scope));
    const now = Date.now();
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw || raw.length > 60000) return;
      const cached = JSON.parse(raw);
      if (cached.version !== 1 || !Number.isFinite(cached.savedAt) || cached.savedAt > now || now-cached.savedAt > WINDOW) return;
      for (const key of Object.keys(labels)) {
        const rows = cached.points?.[key];
        if (!Array.isArray(rows) || rows.length > 120) continue;
        let previous = -1;
        const points = rows.filter(point => {
          if (!point || !Number.isFinite(point.time) || point.time <= previous || point.time > now || point.time < now-WINDOW
              || !(point.value === null || validValue(key,point.value))) return false;
          previous = point.time;return true;
        }).map(point => ({time:point.time,value:point.value}));
        history.set(key,points);
      }
    } catch (_) { /* Storage is optional, including private browsing. */ }
  }
  function persist(force = false) {
    if (!storageKey) return;
    const now = Date.now(), bucket = Math.floor(now/BUCKET);
    if (!force && savedBucket === bucket) return;
    savedBucket = bucket;
    const points = Object.fromEntries(Array.from(history, ([key, rows]) => [key,rows.filter(point => point.time >= now-WINDOW)]));
    try { sessionStorage.setItem(storageKey, JSON.stringify({version:1,savedAt:now,points})); } catch (_) {}
  }
  const format = (number, key) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(number) + (key === 'cpu' || key === 'memory' ? '%' : '');
  function svgNode(tag, className) {
    const node = document.createElementNS(NS, tag);
    if (className) node.setAttribute('class', className);
    return node;
  }
  function chart(card, key) {
    let svg = card.querySelector('.hubKpiSpark');
    if (svg) return svg;
    svg = svgNode('svg','hubKpiSpark');
    svg.dataset.metric = key;
    svg.setAttribute('viewBox','0 0 200 28');
    svg.setAttribute('preserveAspectRatio','none');
    svg.setAttribute('role','img');
    svg.setAttribute('focusable','false');
    const defs = svgNode('defs'), gradient = svgNode('linearGradient');
    gradient.id = 'hub-kpi-fill-' + key;
    gradient.setAttribute('x1','0');gradient.setAttribute('y1','0');gradient.setAttribute('x2','0');gradient.setAttribute('y2','1');
    for (const [offset, opacity] of [['0','0.2'],['1','0']]) {
      const stop = svgNode('stop');stop.setAttribute('offset',offset);stop.setAttribute('stop-color','currentColor');stop.setAttribute('stop-opacity',opacity);gradient.append(stop);
    }
    defs.append(gradient);
    const area = svgNode('path','hubKpiSparkArea');area.setAttribute('fill','url(#'+gradient.id+')');
    const line = svgNode('path','hubKpiSparkLine');
    const dot = svgNode('circle','hubKpiSparkDot');dot.setAttribute('r','1.3');
    svg.append(defs,area,line,dot);card.append(svg);card.classList.add('has-kpi-spark');
    return svg;
  }
  function record(key, value, now) {
    const points = history.get(key);
    const valid = validValue(key,value);
    const point = {time:now, value:valid ? value : null};
    const last = points[points.length-1];
    // Replace readings inside a bucket, while preserving an explicit missing-data boundary.
    if (last && Math.floor(last.time/BUCKET) === Math.floor(now/BUCKET) && (last.value === null) === (point.value === null)) points[points.length-1] = point;
    else points.push(point);
    while (points.length && (points[0].time < now-WINDOW || points.length > 120)) points.shift();
  }
  function draw(card, key) {
    if (!card) return;
    const svg = chart(card,key), now = Date.now();
    const points = history.get(key).filter(point => point.time >= now-WINDOW);
    const valid = points.filter(point => point.value !== null);
    svg.dataset.samples = String(valid.length);
    svg.dataset.history = valid.length > 1 ? 'ready' : 'pending';
    svg.dataset.empty = String(!valid.length);
    const fresh = points.length && points[points.length-1].value !== null && now-points[points.length-1].time <= GAP;
    svg.dataset.stale = String(!fresh);
    const line = svg.querySelector('.hubKpiSparkLine'), area = svg.querySelector('.hubKpiSparkArea'), dot = svg.querySelector('circle');
    let outline = '', fill = '', segments = 0;
    if (valid.length) {
      let low = Infinity, high = -Infinity;
      for (const point of valid) {low=Math.min(low,point.value);high=Math.max(high,point.value);}
      const spread = Math.max(key === 'cpu' || key === 'memory' ? 5 : 1, high-low);
      const bottom = Math.max(0, (low+high-spread)/2), top = Math.max(bottom+spread,high);
      const start = points[0].time, span = Math.max(1000,now-start);
      const x = point => (2+196*(point.time-start)/span).toFixed(2);
      const y = point => (25-22*(point.value-bottom)/(top-bottom)).toFixed(2);
      let run = [], previous = null;
      const flush = () => {
        if (!run.length) return;
        const first = run[0], last = run[run.length-1];
        let path = 'M'+x(first)+' '+y(first);
        for (const point of run.slice(1)) path += key === 'cpu' || key === 'memory' ? ' L'+x(point)+' '+y(point) : ' H'+x(point)+' V'+y(point);
        outline += path;
        if (run.length > 1) fill += path+' L'+x(last)+' 28 L'+x(first)+' 28 Z';
        segments++;run=[];
      };
      for (const point of points) {
        if (point.value === null || (previous && point.time-previous.time > GAP)) flush();
        if (point.value !== null) run.push(point);
        previous=point;
      }
      flush();
      const last = valid[valid.length-1];
      // Show the current reading immediately, without fabricating past samples.
      if (valid.length === 1 && fresh) {
        outline = 'M2 '+y(last)+' H198';
        fill = outline+' L198 28 L2 28 Z';
      }
      dot.setAttribute('cx',valid.length === 1 && fresh ? '198' : x(last));dot.setAttribute('cy',y(last));
      const detail = labels[key]+': '+format(last.value,key)+(valid.length === 1
        ? '. Линия текущего значения; история накапливается.'
        : '. История за последние 5 минут в этой вкладке, замеров: '+valid.length+'. Диапазон '+format(low,key)+' — '+format(high,key)+'.')+(fresh ? '' : ' Нет свежего замера.');
      svg.setAttribute('aria-label',detail);svg.setAttribute('title',detail);
    } else {
      outline = 'M2 25 H198';
      const detail = labels[key]+': ожидаем реальные замеры. История накапливается в открытой вкладке.';
      svg.setAttribute('aria-label',detail);svg.setAttribute('title',detail);
    }
    svg.dataset.segments=String(segments);
    line.setAttribute('d',outline);area.setAttribute('d',fill);dot.hidden=!valid.length || !fresh;
    dot.style.display = !valid.length || !fresh ? 'none' : '';
  }
  function renderRouterCards() {
    for (const key of ['total','online','offline','xray']) draw(document.querySelector('#routerStats .statCard.'+key),key);
  }
  function recordRouters(rows) {
    if (!Array.isArray(rows)) return;
    const now=Date.now(),online=rows.filter(row => Boolean(row.online)).length;
    record('total',rows.length,now);record('online',online,now);record('offline',rows.length-online,now);
    record('xray',rows.filter(row => row.online && row.status?.xray === 'running').length,now);
    persist(true);
    renderRouterCards();
  }
  function recordResources(data, failed) {
    const now=Date.now();
    for (const key of ['cpu','memory']) {
      record(key,failed ? null : data?.[key]?.percent,now);
      draw(document.querySelector('.hubExtraStats [data-vps-resource="'+key+'"]')?.closest('.statCard'),key);
    }
    persist();
  }
  window.addEventListener('pagehide', () => persist(true));
  return {restore,recordRouters,recordResources,renderRouterCards};
})();
