const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const prefix = fs.readFileSync(path.join(__dirname, 'test_router_sidebar_ui.cjs'), 'utf8').split('const html = renderPage(false)')[0];
const html = new Function('require', '__dirname', prefix + '\nreturn renderPage(false);')(require, __dirname);
const script = fs.readFileSync(path.join(root, 'files/www/luci-static/resources/owrt_remote_update.js'), 'utf8');
const effects = fs.readFileSync(path.join(root, 'files/www/luci-static/resources/owrt_remote_effects.js'), 'utf8');
const output = path.join(root, '.test-output/router-panel-update');
fs.mkdirSync(output, {recursive:true});
const revision = 'a'.repeat(40);
const confirmation = require('node:crypto').createHash('sha256').update('owrt-remote:panel-update:v1:fixture-key').digest('hex');

(async function () {
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    for (const width of [1920, 1366, 390, 320]) {
      const context = await browser.newContext({viewport:{width, height:900}, isMobile:width<500, hasTouch:width<500});
      const page = await context.newPage();
      let state = 'idle', active = 0, max = 0, checks = 0, installs = 0;
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const base = width === 1366 ? '/cgi-bin/owrt-remote' : '/access/node-12/cgi-bin/owrt-remote';
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname.endsWith('/owrt_remote_update.js')) return route.fulfill({contentType:'text/javascript',body:script});
        if (url.pathname.endsWith('/owrt_remote_effects.js')) return route.fulfill({contentType:'text/javascript',body:effects});
        if (url.searchParams.get('action') === 'panel_update') {
          assert.equal(url.pathname, base, 'proxy prefix preserved');
          assert.equal(url.searchParams.get('key'), 'fixture-key');
          const op = url.searchParams.get('operation');
          if (op !== 'status') {
            assert.equal(request.method(), 'POST');
            assert.equal(request.headers()['x-owrt-panel-update'], '1');
            assert.equal(new URLSearchParams(request.postData()).get('panel_update_token'), confirmation, 'uhttpd proof is in the POST body');
          }
          assert(!url.searchParams.has('panel_update_token'), 'proof is never placed in the URL');
          active++; max = Math.max(max, active);
          await new Promise(resolve => setTimeout(resolve, 80));
          if (op === 'check') { checks++; state = 'available'; }
          if (op === 'install') {
            installs++; assert.equal(new URLSearchParams(request.postData()).get('revision'), revision); state = 'installing';
          }
          const messages = {idle:'Проверить обновление панели',available:'Доступно обновление панели: v126',
            installing:'Установка обновления и проверка службы…',current:'Установлена последняя версия',
            failed:'Ошибка <script>window.bad=true</script>'};
          active--;
          // The POST can start the router job but lose its response when the
          // reverse tunnel/network drops. The UI must recover via GET status.
          if (op === 'install' && width === 320) return route.abort('failed');
          return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,id:'fixture',status:state,
            message:messages[state],installed_version:state==='current'?'v126':'v125',version:'v126',revision,updated_at:Date.now()/1000})});
        }
        if (url.pathname === base) return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
        return route.abort();
      });
      await page.goto('http://router.test' + base + '?key=fixture-key');
      await page.getByRole('button',{name:'Доступно обновление',exact:true}).waitFor();
      assert.equal(checks, 1, 'one automatic check');
      await page.locator('.routerUpdateButton').click();
      const dialog = page.locator('#routerUpdateDialog');
      await dialog.waitFor({state:'visible'});
      for(const design of ['default','winter','lite']) {
        await page.evaluate(design=>document.documentElement.dataset.hubDesign=design,design);
        const colours=await page.evaluate(()=>{
          const probe=document.createElement('span');probe.style.color='var(--amber)';document.body.append(probe);
          const expected=getComputedStyle(probe).color;probe.remove();
          return {expected,button:getComputedStyle(document.querySelector('.routerUpdateButton')).color,
            message:getComputedStyle(document.querySelector('[data-router-update-message]')).color};
        });
        assert.equal(colours.button,colours.expected,'available update uses the VPS amber colour: '+design);
        assert.equal(colours.message,colours.expected,'dialog uses the same amber colour: '+design);
      }
      await page.evaluate(()=>document.documentElement.dataset.hubDesign='default');
      const box = await dialog.boundingBox();
      assert(Math.abs(box.x + box.width/2 - width/2) < 2, 'dialog centered');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), 'no mobile overflow');
      await dialog.getByRole('button',{name:'Обновить панель',exact:true}).click();
      await page.getByText('Установка обновления и проверка службы…',{exact:true}).waitFor();
      assert(await dialog.getByRole('button',{name:'Проверить обновление',exact:true}).isDisabled());
      await page.reload();
      await page.getByRole('button',{name:'Обновление панели…',exact:true}).waitFor();
      assert.equal(installs, 1, 'reload resumes status without submitting again');
      await page.locator('.routerUpdateButton').click();
      state = 'current';
      await page.getByRole('button',{name:'Установлена последняя версия',exact:true}).waitFor();
      await page.screenshot({path:path.join(output, width + '.png')});
      state = 'failed';
      await dialog.getByRole('button',{name:'Закрыть',exact:true}).click();
      await page.locator('.routerUpdateButton').click();
      await page.getByText('Доступно обновление панели: v126',{exact:true}).waitFor();
      assert(!await page.evaluate(()=>window.bad));
      assert.equal(max, 1, 'never parallel API requests');
      assert.deepEqual(errors, []);
      await context.close();
      console.log('Router update UI: OK ' + width);
    }
  } finally { await browser.close(); }
})();
