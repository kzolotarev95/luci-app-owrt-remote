/* Three per-router shortcuts. Insertion never presses Enter. */
function initRouterTerminalCommands() {
  const host = document.getElementById('routerTerminalCommands');
  if (!host || IS_VPS_TERMINAL) return;
  const defaults = ROUTER_TERMINAL_COMMANDS.commands;
  const buttons = Array.from(host.querySelectorAll('[data-router-command]'));
  const inputs = Array.from(host.querySelectorAll('[data-router-command-input]'));
  const editor = host.querySelector('details');
  const key = 'owrtRemote:terminalCommands:v1:' + encodeURIComponent(ROUTER_ID) + ':' + ROUTER_TERMINAL_COMMANDS.manager;
  const clean = value => String(value || '').replace(/[\r\n\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 500);
  let commands = defaults.slice();
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (Array.isArray(saved) && saved.length === 3 && saved.every(value => typeof value === 'string')) commands = saved.map(clean);
  } catch (_) {}
  const render = () => {
    buttons.forEach((button, index) => {
      button.textContent = commands[index] || 'Команда ' + (index + 1);
      button.title = commands[index] ? 'Вставить: ' + commands[index] : 'Настроить команду ' + (index + 1);
      button.setAttribute('aria-label', button.title);
      inputs[index].value = commands[index];
    });
  };
  buttons.forEach((button, index) => button.addEventListener('click', async () => {
    const command = commands[index];
    if (!command) { editor.open = true; inputs[index].focus(); return; }
    if (!term || passwordPromptActive || (terminalMode === 'http' ? !httpSid : !ws || ws.readyState !== 1)) {
      notice('Сначала подключитесь к роутеру и завершите вход в SSH.', '33');
      return;
    }
    await sendData(command, true);
    term.focus();
  }));
  host.querySelector('[data-router-command-save]').addEventListener('click', () => {
    commands = inputs.map(input => clean(input.value));
    try { localStorage.setItem(key, JSON.stringify(commands)); } catch (_) {}
    render(); editor.open = false;
  });
  host.querySelector('[data-router-command-reset]').addEventListener('click', () => {
    commands = defaults.slice();
    try { localStorage.removeItem(key); } catch (_) {}
    render(); editor.open = false;
  });
  render();
}
