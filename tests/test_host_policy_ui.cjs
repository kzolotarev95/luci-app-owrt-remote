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
const router = {id: 'test', online: true, permissions: {owner: true}, status: {vps_host: '193.233.82.38', vps_host_mode: 'manual'}};
const context = {
  escapeHtml: value => String(value).replace(/[<>&"']/g, ch => ({'<':'&lt;', '>':'&gt;', '&':'&amp;', '"':'&quot;', "'":'&#39;'}[ch])),
  escapeAttr: value => String(value).replace(/"/g, '&quot;'),
  cards: {addEventListener(kind, handler) { handlers[kind] = handler; }},
  selectedRouter() { return router; },
  requestRouterRender() { renders++; },
  replaceRouterInList(next) { Object.assign(router, next); },
  async fetch(url, options) {
    request = {url, payload: JSON.parse(options.body)};
    return {ok: true, async json() { return {ok: true, router: {...router, status: {vps_host: request.payload.vps_host, vps_host_mode: request.payload.vps_host_mode}}}; }};
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
  handlers.input(eventFor('[data-vps-field]', {dataset: {vpsRouter: 'test', vpsField: 'password'}, type: 'password', value: 'test-password'}));
  context.renderVpsHostSettings(router); // simulate polling refresh
  assert.equal(state.host, '192.0.2.8');
  assert.equal(state.auto, true);
  assert.equal(state.open, true);
  await handlers.click(eventFor('[data-vps-save]', {dataset: {vpsSave: 'test'}}));
  assert.equal(request.url, '/api/router/test/vps-host');
  assert.equal(request.payload.vps_host_mode, 'auto');
  assert.equal(request.payload.vps_host, '192.0.2.8');
  assert.equal(state.password, '');
  assert.equal(state.dirty, false);
  assert.equal(state.saving, false);
  context.fetch = async () => ({ok: false, async json() { return {error: 'SSH failed'}; }});
  state.dirty = true;
  state.password = 'test-password';
  await handlers.click(eventFor('[data-vps-save]', {dataset: {vpsSave: 'test'}}));
  assert.equal(state.message, 'SSH failed');
  assert.equal(state.dirty, true);
  assert.equal(state.password, '');
  assert.equal(state.saving, false);
  assert(renders > 0);
  console.log('HOST_POLICY_UI_OK: owner access, draft persistence, explicit apply, remote failure and password clearing');

  // Exercise the active card renderer: hidden controls must not return on polling.
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
    mobileCardsMq: {matches: false}, authIsOwner: () => true, groupPickerButtonsHtml: empty,
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
        assert(!/data-vps-(toggle|field|save)/.test(context.cards.innerHTML));
        assert(!context.cards.innerHTML.includes('Настроить VPS host'));
        assert(context.cards.innerHTML.includes('Test router'));
      }
    }
  }
  console.log('VPS_HOST_CONTROLS_HIDDEN_OK: desktop, mobile, expanded cards and polling');
})().catch(error => { console.error(error); process.exitCode = 1; });
