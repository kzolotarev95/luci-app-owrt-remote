// Run after: python tests/test_recaptcha.py --export-js .test-output/recaptcha
// node tests/test_host_policy_ui.cjs .test-output/recaptcha/dashboard.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
const begin = source.indexOf('const vpsHostDrafts = new Map();');
const end = source.indexOf('\nfunction renderRouterNotesPanel(', begin);
const handlersBegin = source.indexOf("cards.addEventListener('input', (ev) => {\n  const field = ev.target.closest('[data-vps-field]');");
const handlersEnd = source.indexOf("\ncards.addEventListener('click', async (ev) => {\n  const pickerToggle", handlersBegin);
assert(begin >= 0 && end > begin && handlersBegin > 0 && handlersEnd > handlersBegin);
const handlers = {};
let renders = 0, request;
const router = {id: 'test', online: true, permissions: {owner: true}, status: {vps_host: '193.233.82.38', vps_host_mode: 'manual', vps_host_control: 1}};
const context = {
  crypto: require('node:crypto').webcrypto, AbortController, setTimeout, clearTimeout,
  escapeHtml: value => String(value).replace(/[<>&"']/g, ch => ({'<':'&lt;', '>':'&gt;', '&':'&amp;', '"':'&quot;', "'":'&#39;'}[ch])),
  escapeAttr: value => String(value).replace(/"/g, '&quot;'),
  cards: {addEventListener(kind, handler) { handlers[kind] = handler; }},
  selectedRouter() { return router; },
  requestRouterRender() { renders++; },
  replaceRouterInList(next) { Object.assign(router, next); },
  async fetch(url, options) {
    request = {url, payload: JSON.parse(options.body)};
    return {ok: true, async json() { return {ok: true, router: {...router, vps_host_update:{id:request.payload.request_id, status:'queued', ...request.payload}}}; }};
  },
};
vm.createContext(context);
vm.runInContext(source.slice(begin, end) + '\n' + source.slice(handlersBegin, handlersEnd), context);
const eventFor = (selector, node) => ({target: {closest(wanted) { return wanted === selector ? node : null; }}});
(async () => {
  assert.equal(context.renderVpsHostSettings({...router, permissions: {manage: true}}), '', 'owner permission is required');
  const state = context.getVpsHostDraft(router);
  await handlers.click(eventFor('[data-vps-toggle]', {dataset: {vpsToggle: 'test'}}));
  assert.equal(state.open, true);
  handlers.input(eventFor('[data-vps-field]', {dataset: {vpsRouter: 'test', vpsField: 'host'}, type: 'text', value: '192.0.2.8'}));
  handlers.input(eventFor('[data-vps-field]', {dataset: {vpsRouter: 'test', vpsField: 'auto'}, type: 'checkbox', checked: true}));
  context.renderVpsHostSettings(router); // simulate polling refresh
  assert.equal(state.host, '192.0.2.8');
  assert.equal(state.auto, true);
  assert.equal(state.open, true);
  await handlers.click(eventFor('[data-vps-save]', {dataset: {vpsSave: 'test'}}));
  assert.equal(request.url, '/api/router/test/vps-host');
  assert.equal(request.payload.vps_host_mode, 'auto');
  assert.equal(request.payload.vps_host, '192.0.2.8');
  assert.equal(request.payload.ssh_password, undefined);
  assert.equal(state.dirty, true, 'queueing is not successful application');
  assert(context.renderVpsHostSettings(router).includes('Ожидает Heartbeat'));
  assert(!context.renderVpsHostSettings(router).includes('type="password"'));
  router.vps_host_update.status='succeeded';
  router.status.vps_host='192.0.2.8';router.status.vps_host_mode='auto';
  context.renderVpsHostSettings(router);
  assert.equal(state.dirty,false);
  assert.equal(state.saving, false);
  router.status.xray='running';
  router.vps_host_update.vps_host='192.0.2.8';router.vps_host_update.vps_host_mode='auto';
  assert(context.renderVpsHostSettings(router).includes('Адрес подтверждён агентом. Xray запущен.'));
  router.status.vps_host_control=0;
  const downgraded=context.renderVpsHostSettings(router);
  assert(downgraded.includes('Обнови агент'));
  assert(!downgraded.includes('Адрес подтверждён агентом. Xray запущен.'),'historic success cannot contradict current capabilities');
  router.status.vps_host_control=1;router.status.vps_host='changed-locally.example';
  assert(context.renderVpsHostSettings(router).includes('Сейчас агент сообщает другой адрес или режим.'));
  router.status.vps_host='192.0.2.8';router.status.xray='stopped';
  assert(context.renderVpsHostSettings(router).includes('Сейчас Xray остановлен.'));
  context.fetch = async () => ({ok: false, async json() { return {error: 'Agent unavailable'}; }});
  state.dirty = true;
  router.vps_host_update={};state.requestId='';
  await handlers.click(eventFor('[data-vps-save]', {dataset: {vpsSave: 'test'}}));
  assert.equal(state.message, 'Agent unavailable');
  assert.equal(state.dirty, true);
  assert.equal(state.saving, false);
  assert(renders > 0);
  console.log('HOST_POLICY_UI_OK: owner access, draft persistence, explicit apply, queued delivery, verified acknowledgement and errors');

  // Exercise the active card renderer: owner controls stay available on polling.
  const renderBegin = source.indexOf('render = function(list) {');
  const renderEnd = source.indexOf('\nfunction renderRouterView()', renderBegin);
  assert(renderBegin >= 0 && renderEnd > renderBegin);
  const identity = value => String(value ?? '');
  const empty = () => '';
  Object.assign(context, {
    syncRouterNotesState: empty, syncExpandedActionPanels: empty,
    groupName: () => 'Без группы', metricPlaceholder: identity, duration: identity,
    formatLoad: identity, formatMemory: identity, flashValueForRouter: empty,
    formatFlash: identity, temperatureValueForRouter: empty, routerSupportsWol: () => false,
    getWolState: () => ({open: false}), getTrafficState: () => ({open: false}),
    routerNotesPreview: empty, actionPanelId: id => 'actions-' + id,
    expandedActionPanels: new Set(), expandedCardDetails: new Set(),
    expandedGroupPickers: new Set(), openRouterGroups: new Set(['__ungrouped__']),
    routerStatusFilterValue:'all', mobileCardsMq: {matches: false}, authIsOwner: () => true, groupPickerButtonsHtml: empty,
    renderModelMetric: empty, modelGetsLegendBadge: () => false, metric: empty, renderTachyon: empty,
    statusRu: identity, ago: empty, metricHtml: empty, formatMemoryHtml: empty,
    memoryClass: empty, formatFlashHtml: empty, flashClass: empty, syncCardDetails: empty,
    formatTemperatureHtml: empty, tempClass: empty, syncActionToggleStates: empty,
  });
  vm.runInContext(source.slice(renderBegin, renderEnd), context);
  for (const mobile of [false, true]) {
    context.mobileCardsMq.matches = mobile;
    for (const expanded of [false, true]) {
      context.expandedCardDetails.clear();
      if (expanded) context.expandedCardDetails.add('router-details-test');
      for (let refresh = 0; refresh < 3; refresh++) {
        context.render([{...router, name: 'Test router', online: true}]);
        assert(/data-vps-toggle/.test(context.cards.innerHTML));
        assert(/(?:Настроить|Скрыть) VPS host/.test(context.cards.innerHTML));
        assert(context.cards.innerHTML.includes('Test router'));
      }
    }
  }
  console.log('VPS_HOST_CONTROLS_RESTORED_OK: desktop, mobile, expanded cards and polling');
})().catch(error => { console.error(error); process.exitCode = 1; });
