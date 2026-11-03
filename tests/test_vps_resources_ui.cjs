// node tests/test_vps_resources_ui.cjs <exported-dashboard.js>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
const begin = source.indexOf('function initVpsResourceWidgets() {');
const end = source.indexOf('\ninitVpsResourceWidgets();', begin);
assert(begin >= 0 && end > begin);
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function fixture(initialPending = false) {
  const timers = new Map(), documentEvents = {}, windowEvents = {}, calls = [];
  let nextId = 1;
  const widgets = ['cpu', 'memory', 'disk', 'cpu', 'memory', 'disk'].map(key => ({
    dataset: {vpsResource: key}, label: {textContent: {cpu: 'ЦП', memory: 'ОЗУ', disk: 'ДИСК'}[key]},
    value: {textContent: '12,5%'}, title: '',
    querySelector(selector) { return selector === 'span' ? this.label : this.value; },
    setAttribute(key, value) { this[key] = value; },
  }));
  const metrics = {ok: true, cpu: {percent: 25, cores: 2}, memory: {percent: 40, used_bytes: 1024, total_bytes: 2048}, disk: {percent: 60, used_bytes: 2048, total_bytes: 4096}};
  const context = {
    Intl, AbortController,
    document: {hidden: false, documentElement:{dataset:{hubDesign:'default'}}, querySelectorAll() { return widgets; }, addEventListener(key, fn) { documentEvents[key] = fn; }},
    window: {addEventListener(key, fn) { windowEvents[key] = fn; }},
    setInterval(fn, ms) { const id = nextId++; timers.set(id, {fn, ms, interval: true}); return id; },
    setTimeout(fn, ms) { const id = nextId++; timers.set(id, {fn, ms}); return id; },
    clearInterval(id) { timers.delete(id); }, clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { calls.push({url, options}); return context.respond(url, options); },
    respond: initialPending ? () => new Promise(() => {}) : async () => ({ok: true, status: 200, json: async () => metrics}),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(begin, end) + '\ninitVpsResourceWidgets();', context);
  return {context, timers, calls, widgets, documentEvents, windowEvents,
    tick() { const timer = [...timers.values()].find(t => t.interval); assert(timer); return timer.fn(); },
    intervals() { return [...timers.values()].filter(t => t.interval); },
  };
}

(async () => {
  const initial = fixture(true);
  assert.equal(initial.calls.length, 1);
  initial.windowEvents.pageshow();
  assert.equal(initial.calls.length, 1, 'initial pageshow reuses the pending fetch');
  assert.equal(initial.calls[0].options.signal.aborted, false, 'opening page must not cancel initial fetch');
  assert(initial.widgets.every(widget => widget.value.textContent === '12,5%'), 'keep server-rendered values while first fetch is pending');
  initial.windowEvents.pagehide();
  const f = fixture();
  await flush();
  assert.equal(f.calls.length, 1, 'first update starts immediately');
  assert.equal(f.intervals().length, 1);
  assert.equal(f.intervals()[0].ms, 1000);
  assert.equal(f.calls[0].url, '/api/vps/resources');
  assert.equal(f.calls[0].options.cache, 'no-store');
  assert.equal(f.widgets[0].value.textContent, '25%');
  assert.equal(f.widgets[3].value.textContent, '25%');
  assert(f.widgets[1].title.includes('1 КиБ из 2 КиБ'));
  assert(f.widgets[2].title.includes('раздел /'));

  let finish;
  f.context.respond = () => new Promise(resolve => { finish = resolve; });
  f.tick(); f.tick();
  assert.equal(f.calls.length, 2, 'slow requests must not overlap');
  finish({ok: true, status: 200, json: async () => ({ok: true, cpu: {percent: 50, cores: 2}, memory: null, disk: null})});
  await flush();
  assert.equal(f.widgets[0].value.textContent, '50%');
  assert.equal(f.widgets[1].value.textContent, '—');

  f.context.respond = async () => { throw new Error('offline'); };
  f.tick(); await flush();
  assert.equal(f.widgets[0].value.textContent, '—', 'failed fetch must not pretend old numbers are live');
  assert.equal(f.widgets[0].dataset.stale, 'true');
  f.context.respond = async () => ({ok: true, status: 200, json: async () => ({ok: true, cpu: {percent: 0, cores: 1}})});
  f.tick(); await flush();
  assert.equal(f.widgets[0].value.textContent, '0%');
  assert.equal(f.widgets[0].dataset.stale, 'false');

  f.context.document.hidden = true;
  f.documentEvents.visibilitychange();
  assert.equal(f.intervals().length, 0);
  f.context.document.hidden = false;
  f.documentEvents.visibilitychange(); await flush();
  assert.equal(f.intervals().length, 1);
  f.windowEvents.pagehide();
  assert.equal(f.intervals().length, 0);
  f.windowEvents.pageshow(); await flush();
  assert.equal(f.intervals().length, 1, 'BFCache restore must resume once');

  f.context.respond = async () => ({ok: false, status: 401});
  f.tick(); await flush();
  assert.equal(f.intervals().length, 0, 'expired sessions stop polling');
  const count = f.calls.length;
  f.documentEvents.visibilitychange(); await flush();
  assert.equal(f.calls.length, count);

  const lite = fixture(); await flush();
  lite.context.document.documentElement.dataset.hubDesign = 'lite';
  lite.windowEvents.hubdesignchange(); await flush();
  assert.equal(lite.intervals().length, 1, 'changing design must not duplicate widget timers');
  assert.equal(lite.intervals()[0].ms, 5000, 'lite mode polls resources every 5 seconds');
  lite.context.document.documentElement.dataset.hubDesign = 'winter';
  lite.windowEvents.hubdesignchange(); await flush();
  assert.equal(lite.intervals().length, 1);
  assert.equal(lite.intervals()[0].ms, 1000, 'winter restores the regular update interval');
  lite.windowEvents.pagehide();

  const g = fixture(); await flush();
  g.context.respond = (url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timeout'))));
  g.tick();
  const timeout = [...g.timers.values()].find(timer => !timer.interval);
  assert.equal(timeout.ms, 2500);
  timeout.fn(); await flush();
  assert(g.calls.at(-1).options.signal.aborted);
  assert.equal(g.widgets[0].dataset.stale, 'true');
  g.windowEvents.pagehide();
  console.log('VPS_WIDGETS_UI_OK: immediate/1s updates, no overlaps, desktop/mobile, errors, timeout, pause/resume and session expiry');
})().catch(error => { console.error(error); process.exitCode = 1; });
