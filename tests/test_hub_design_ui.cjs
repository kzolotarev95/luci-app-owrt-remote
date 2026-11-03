// Local browser checks; all Hub API calls are fixtures, no VPS is contacted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/hub-design');
execFileSync(process.env.TEST_PYTHON || 'python', ['tests/test_vps_resources.py', '--export-preview', output],
  {env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const template = fs.readFileSync(path.join(output, 'dashboard.html'), 'utf8');
const routers = Array.from({length:18}, (_, i) => ({id:'node-'+i,name:'Роутер '+(i+1),role:'node',
  online:i!==2,entry_port:18000+i,ssh_entry_port:19000+i,last_seen:1700000000,
  permissions:{manage:true,admin:true,ssh:true},status:{model:'Xiaomi Mi Router AX3000T',
    release:'OpenWrt 24.10.3',xray:'running',ssh:'running',mem_total_kb:239720,mem_available_kb:72000}}));
const html = template.replace('window.ROUTERS = [];', 'window.ROUTERS = '+JSON.stringify(routers)+';');

(async () => {
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    for (const [width,height,mobile] of [[1366,960,false],[390,844,true],[844,390,true]]) {
      const context = await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile});
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:html});
        if (url.pathname === '/api/routers') return route.fulfill({json:{routers}});
        if (url.pathname === '/api/router-groups') return route.fulfill({json:{groups:[]}});
        if (url.pathname === '/api/notifications/wait') return route.fulfill({status:429,json:{busy:true},headers:{'Retry-After':'5'}});
        if (url.pathname === '/api/vps/resources') return route.fulfill({json:{ok:true,cpu:{percent:15},memory:{percent:35},disk:{percent:40}}});
        if (url.pathname === '/api/hub-update') return route.fulfill({json:{ok:true,checked:true,available:false,current_version:'v120'}});
        if (url.pathname.startsWith('/api/')) return route.fulfill({json:{ok:true,auth:{username:'root',is_owner:true,can_add_router:true}}});
        return route.abort();
      });
      await page.goto('http://127.0.0.1:9999/');
      await page.waitForFunction(() => typeof setHubDesign === 'function' && document.querySelectorAll('.card').length === 18);
      const mobileLayout = width<=680 || mobile;
      const toggle = page.locator(mobileLayout?'#mobileSeasonToggle':'#seasonToggle');
      const panel = page.locator(mobileLayout?'#mobileSeasonPanel':'#seasonPanel');
      await toggle.click();
      await panel.locator('[data-hub-design="winter"]').click();
      assert.equal(await page.evaluate(() => document.documentElement.dataset.hubDesign), 'winter');
      assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(6, 20, 38)');
      assert.equal(await page.locator('.card').first().evaluate(el => getComputedStyle(el).backdropFilter), 'none');
      assert.equal(await panel.locator('[data-hub-design="winter"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.evaluate(() => localStorage.getItem('owrtRemote:hubDesign')), 'winter');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(output, 'winter-'+width+'.png'),fullPage:false});
      await panel.locator('[data-season-mode="snow"]').click();
      await page.waitForFunction(() => seasonalFxFrame>0 && seasonalFxParticles.length>0);
      // Pausing a hidden tab cancels the animation, returning resumes one loop.
      await page.evaluate(() => {Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
      assert.equal(await page.evaluate(() => seasonalFxFrame),0);
      await page.evaluate(() => {Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
      await page.waitForFunction(() => seasonalFxFrame>0);
      await panel.locator('[data-hub-design="lite"]').click();
      assert.deepEqual(await page.evaluate(() => [seasonalFxFrame,seasonalFxParticles.length,seasonalFxCanvas.width,seasonalFxCanvas.height]), [0,0,1,1]);
      assert.equal(await panel.locator('[data-season-mode="snow"]').isDisabled(),true);
      assert.equal(await page.locator('.card').first().evaluate(el => getComputedStyle(el).boxShadow),'none');
      assert.equal(await page.locator('.card').first().evaluate(el => getComputedStyle(el).backdropFilter),'none');
      assert.equal(await page.evaluate(() => document.getAnimations().filter(a=>a.playState==='running').length),0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(output, 'lite-'+width+'.png'),fullPage:false});
      // The palette is restored on reload before canvas allocation.
      await page.reload();
      await page.waitForFunction(() => hubDesignMode==='lite');
      assert.deepEqual(await page.evaluate(() => [seasonalFxFrame,seasonalFxParticles.length,seasonalFxCanvas.width]), [0,0,1]);
      await toggle.click();
      assert.equal(await panel.locator('[data-hub-design="lite"]').getAttribute('aria-pressed'),'true');
      await panel.locator('[data-hub-design="default"]').click();
      await page.waitForFunction(() => seasonalFxFrame>0);
      assert.equal(await page.evaluate(() => seasonEffectMode),'snow','restore the saved effect after leaving lite mode');
      await page.evaluate(() => setSeasonEffectMode('off'));
      await page.evaluate(() => {localStorage.setItem('owrtRemote:hubDesign','lite');});
      await page.reload();
      await page.waitForFunction(() => hubDesignMode==='lite');
      // Storage unavailable: changing designs still works in the current tab.
      await page.evaluate(() => {Storage.prototype.setItem=()=>{throw new Error('disabled');};setHubDesign('winter');});
      assert.equal(await page.evaluate(() => hubDesignMode),'winter');
      assert.deepEqual(errors,[]);
      await context.close();
    }
    console.log('HUB_DESIGN_UI_OK: desktop/mobile/landscape, persistence, no overflow, lite has no animations or canvas, hidden tab pause/resume');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
