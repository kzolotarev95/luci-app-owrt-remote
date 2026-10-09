// Exercise the actual router picker script: checkbox changes must not close it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('files/www/cgi-bin/owrt-remote', 'utf8');
const begin = source.indexOf("document.querySelectorAll('[data-wan-picker]')");
const end = source.indexOf('</script>', begin);
assert(begin >= 0 && end > begin);
for (const mobile of [false, true]) {
  const inputs = ['wan', 'wwan', 'modem4g'].map(value => ({value, checked: value === 'wan'}));
  const count = {}, picked = {}, handlers = {};
  const picker = {
    open: false,
    closest() { return {querySelector: selector => selector === '[data-wan-count]' ? count : picked}; },
    querySelectorAll() { return inputs; },
    addEventListener(name, handler) { handlers[name] = handler; },
  };
  vm.runInNewContext(source.slice(begin, end), {
    document: {querySelectorAll: () => [picker]},
    window: {matchMedia: () => ({matches: mobile})},
  });
  assert.equal(count.textContent, 1);
  assert.equal(picked.textContent, 'wan');
  picker.open = true;
  inputs[1].checked = true;
  inputs[2].checked = true;
  handlers.change();
  assert.equal(count.textContent, 3);
  assert.equal(picked.textContent, 'wan, wwan, modem4g');
  assert.equal(picker.open, true);
  for (const input of inputs) input.checked = false;
  handlers.change();
  assert.equal(count.textContent, 0);
  assert.equal(picked.textContent, 'Интерфейсы не выбраны');
  assert.equal(picker.open, true);
}
console.log('WAN_PICKER_UI_OK: multiple checks, count, empty selection and open-list persistence');
