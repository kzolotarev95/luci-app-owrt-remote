// Run after: python tests/test_recaptcha.py --export-js .test-output/recaptcha
// Then: node tests/test_recaptcha_login.cjs .test-output/recaptcha/login.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
const prepareStart = source.indexOf('async function prepareLoginRecaptcha()');
const prepareEnd = source.indexOf('\nfunction setActiveAuthTab', prepareStart);
const submitStart = source.indexOf("if (passwordLoginForm) {\n  passwordLoginForm.addEventListener('submit'");
const submitEnd = source.indexOf('\nasync function loadLoginMeta', submitStart);
assert(prepareStart >= 0 && prepareEnd > prepareStart && submitStart >= 0 && submitEnd > submitStart);

function fixture(kind = 'v3') {
  const tokenField = {value: 'stale-token', dataset: {recaptchaV3SiteKey: 'test-site', recaptchaAction: 'login'}};
  const button = {disabled: false};
  const posts = [], status = [], timers = new Map();
  let submitted, timerId = 0, generation = 0, resetCount = 0;
  const form = {
    action: '/login',
    querySelector() { return kind === 'v3' ? tokenField : null; },
    reportValidity() { return true; },
    addEventListener(event, callback) { submitted = callback; },
    submit() { posts.push({native: true, token: tokenField.value}); },
  };
  const google = {
    ready(callback) { callback(); },
    async execute(site, options) {
      assert.equal(site, 'test-site');
      assert.equal(options.action, 'login');
      return 'fresh-token-' + ++generation;
    },
    reset() { resetCount++; },
  };
  const window = {
    grecaptcha: google, FormData: true, fetch: true, AbortController: true,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    location: {assign() { throw new Error('Unexpected redirect in rejection test'); }},
  };
  const context = {
    window, passwordLoginForm: form, passwordLoginSubmitBtn: button,
    loginSubmitInFlight: false, loginAuthMeta: {}, hubOtp: null,
    updateLoginOtp() {}, loginRequiresTotp() { return false; },
    setLoginRuntimeStatus(text) { status.push(text); },
    FormData: class { constructor() { this.token = tokenField.value; } },
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    extractLoginError() { return 'Incorrect password'; },
    async fetch(url, options) {
      posts.push({url, token: options.body.token});
      return {ok: false, status: 401, async text() { return 'Incorrect password'; }};
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(prepareStart, prepareEnd) + '\n' + source.slice(submitStart, submitEnd), context);
  return {
    context, window, google, tokenField, button, posts, status, timers,
    submit: () => submitted({preventDefault() {}}),
    resetCount: () => resetCount,
  };
}

(async () => {
  const retry = fixture();
  assert.equal(retry.posts.length, 0, 'v3 must run on submission, not page load');
  await retry.submit();
  await retry.submit();
  assert.deepEqual(retry.posts.map(p => p.token), ['fresh-token-1', 'fresh-token-2']);
  assert.equal(retry.tokenField.value, '');
  assert.equal(retry.button.disabled, false);
  assert.equal(retry.context.loginSubmitInFlight, false);
  assert.equal(retry.timers.size, 0);

  const failure = fixture();
  failure.google.execute = async () => { throw new Error('blocked'); };
  await failure.submit();
  assert.equal(failure.posts.length, 0, 'a failed CAPTCHA must not send login');
  assert.equal(failure.button.disabled, false);
  assert.equal(failure.tokenField.value, '');

  const blocked = fixture();
  delete blocked.window.grecaptcha;
  const pending = blocked.submit();
  const timeout = [...blocked.timers.values()].find(timer => timer.delay === 12000);
  assert(timeout, 'missing Google script needs a finite timeout');
  timeout.callback();
  await pending;
  assert.equal(blocked.posts.length, 0);
  assert.equal(blocked.timers.size, 0);
  assert.equal(blocked.button.disabled, false);

  const doubleClick = fixture();
  let release;
  doubleClick.google.execute = () => new Promise(resolve => { release = resolve; });
  const first = doubleClick.submit();
  await doubleClick.submit();
  release('only-once');
  await first;
  assert.equal(doubleClick.posts.length, 1, 'double click must not duplicate login');

  const native = fixture();
  native.window.fetch = false;
  await native.submit();
  assert.deepEqual(native.posts, [{native: true, token: 'fresh-token-1'}]);

  for (const kind of ['v2', 'digits']) {
    const previous = fixture(kind);
    await previous.submit();
    assert.equal(previous.posts.length, 1);
    assert.equal(previous.button.disabled, false);
    assert.equal(previous.resetCount(), 1);
  }
  console.log('RECAPTCHA_LOGIN_JS_OK: fresh tokens, retry, failure, timeout, double click, native POST and old modes');

  const dashboard = fs.readFileSync(path.join(path.dirname(process.argv[2]), 'dashboard.js'), 'utf8');
  const start = dashboard.indexOf('function updateCaptchaSettingsMode(');
  const end = dashboard.indexOf('\nfunction authB64urlToBytes', start);
  const scoreLabel = {hidden: true};
  const settings = {
    authCaptchaModeField: {value: 'recaptcha_v3'}, authCaptchaMinScoreField: {},
    authCaptchaSiteKeyField: {value: 'v3-site'}, authCaptchaSecretKeyField: {value: 'v3-secret'},
    authCaptchaDraftDirty: false,
    authForm: {querySelector() { return scoreLabel; }},
  };
  vm.createContext(settings);
  vm.runInContext(dashboard.slice(start, end), settings);
  settings.updateCaptchaSettingsMode();
  assert.equal(scoreLabel.hidden, false);
  assert.equal(settings.authCaptchaMinScoreField.disabled, false);
  assert.equal(settings.authCaptchaSiteKeyField.placeholder, 'reCAPTCHA v3 Site Key');
  assert.equal(settings.authCaptchaSiteKeyField.value, 'v3-site', 'rendering must preserve keys of the saved mode');
  settings.authCaptchaModeField.value = 'recaptcha';
  settings.updateCaptchaSettingsMode(true);
  assert.equal(scoreLabel.hidden, true);
  assert.equal(settings.authCaptchaMinScoreField.disabled, true);
  assert.equal(settings.authCaptchaSiteKeyField.value, '');
  assert.equal(settings.authCaptchaSecretKeyField.value, '');
  assert.equal(settings.authCaptchaSiteKeyField.required, true);
  assert.equal(settings.authCaptchaDraftDirty, true);
  settings.authCaptchaSiteKeyField.value = 'v2-site';
  settings.authCaptchaSecretKeyField.value = 'v2-secret';
  settings.updateCaptchaSettingsMode();
  assert.equal(settings.authCaptchaSiteKeyField.value, 'v2-site');
  settings.authCaptchaModeField.value = 'digits';
  settings.updateCaptchaSettingsMode(true);
  assert.equal(settings.authCaptchaSiteKeyField.value, '');
  assert.equal(settings.authCaptchaSecretKeyField.value, '');
  assert.equal(settings.authCaptchaSiteKeyField.hidden, true);
  assert.equal(settings.authCaptchaSiteKeyField.disabled, true);
  assert.equal(settings.authCaptchaSiteKeyField.required, false);
  settings.authCaptchaModeField.value = 'recaptcha_v3';
  settings.updateCaptchaSettingsMode(true);
  assert.equal(settings.authCaptchaSiteKeyField.value, '');
  assert.equal(settings.authCaptchaSecretKeyField.value, '');
  assert.equal(settings.authCaptchaSiteKeyField.hidden, false);
  assert.equal(settings.authCaptchaSiteKeyField.disabled, false);
  console.log('RECAPTCHA_SETTINGS_JS_OK: v2/v3/digits switching clears incompatible keys and keeps edited drafts');

  const saveStart = dashboard.indexOf("authForm.addEventListener('submit', async (ev) => {");
  const saveEnd = dashboard.indexOf('\nif (delegatedUserReset)', saveStart);
  assert(saveStart >= 0 && saveEnd > saveStart);
  let saveCallback, posted, reloaded = false;
  const savedForm = {
    password: {value: ''}, password_confirm: {value: ''}, current_password: {value: 'fake-password'},
    addEventListener(type, fn) { saveCallback = fn; },
  };
  const saving = {
    authForm: savedForm, authUsernameDraftDirty: true, authCaptchaDraftDirty: true,
    setAuthMessage() {}, ownerPasswordRememberEnabled() { return false; }, restoreOwnerPasswordInput() {},
    FormData: class {
      constructor(form) { assert.equal(form, savedForm); }
      *[Symbol.iterator]() { yield ['captcha_mode', 'recaptcha_v3']; yield ['captcha_min_score', '0.7']; }
    },
    URLSearchParams,
    async fetch(url, options) { posted = options.body.toString(); return {ok: true, async text() { return 'Saved'; }}; },
    async loadAuthMeta() { reloaded = true; },
  };
  vm.createContext(saving);
  vm.runInContext(dashboard.slice(saveStart, saveEnd), saving);
  const event = {currentTarget: savedForm, preventDefault() {}, stopImmediatePropagation() {}};
  const saved = saveCallback(event);
  event.currentTarget = null; // Browsers clear currentTarget after dispatch.
  await saved;
  assert.equal(posted, 'captcha_mode=recaptcha_v3&captcha_min_score=0.7');
  assert.equal(saving.authCaptchaDraftDirty, false);
  assert.equal(reloaded, true);
  console.log('RECAPTCHA_SETTINGS_SAVE_JS_OK: v3 persisted and currentTarget expiry handled');
})().catch(error => { console.error(error); process.exitCode = 1; });
