// Run with Playwright available in NODE_PATH. Uses the actual dashboard HTML/JS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/card-details');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python', ['-c', String.raw`
import importlib.util, os, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='card-details-preview-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR'] = state
    spec = importlib.util.spec_from_file_location('card_details_hub', 'vps/owrt-remote-hub.py')
    hub = importlib.util.module_from_spec(spec); sys.modules[spec.name] = hub; spec.loader.exec_module(hub)
    routers = [{
        'id': 'node-' + str(i), 'name': 'Router ' + str(i), 'online': i != 8,
        'role': 'node', 'group_id': '', 'last_seen_iso': '2026-11-22T00:00:00Z',
        'status': {'model': 'Xiaomi Mi Router AX3000T (OpenWrt U-Boot layout)',
                   'release': 'OpenWrt 25.12.5', 'xray': 'running', 'ssh': 'running',
                   'memory': '64 MB free / 234 MB', 'flash': '30 MB free / 60 MB',
                   'uptime': '1000', 'load': '0.1 0.2 0.3', 'temperature': '55'},
        'permissions': {p: True for p in ('owner','manage','admin','ssh','config','delete','logs')},
        'ssh_entry_port': 19180 + i, 'admin_url': '/r/node-' + str(i) + '/',
        'ssh_url': '/ssh/node-' + str(i) + '/', 'config_url': '/config/node-' + str(i),
        'tachyon': {'installed': True, 'online': True, 'version': '2.0.0-beta.15', 'url': '/tachyon/node-' + str(i) + '/'}
    } for i in range(1,10)]
    page = hub.dashboard_html(routers, 'owner', auth_bootstrap={'username':'owner','is_owner':True})
    Path('.test-output/card-details/dashboard.html').write_text(page, encoding='utf-8')
`], {cwd:path.resolve(__dirname, '..')});
const html = fs.readFileSync(path.join(output, 'dashboard.html'), 'utf8');
const routers = JSON.parse(html.match(/window\.ROUTERS = (\[.*\]);/)[1]);
const geometry = page => page.locator('.routerCardSlot').evaluateAll(slots => slots.map(el => {
  const r = el.getBoundingClientRect();
  return {x:r.x,y:r.y,width:r.width,height:r.height};
}));
function sameGrid(before, after, label) {
  assert.equal(after.length, before.length);
  for (let i=0; i<before.length; i++) for (const k of ['x','y','width','height']) {
    assert(Math.abs(before[i][k]-after[i][k])<1, `${label}: slot ${i} ${k} moved`);
  }
}
const settled = page => page.waitForFunction(() => cardDetailsAnimations.size === 0);
async function fitsViewport(page) {
  const r = await page.locator('.isFloating>.card').evaluate(el => {
    const r=el.getBoundingClientRect();
    return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,w:innerWidth,h:innerHeight};
  });
  assert(r.left>=11 && r.right<=r.w-11 && r.top>=11 && r.bottom<=r.h-11, JSON.stringify(r));
  assert(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth));
}
(async () => {
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  let count=0;
  try {
    for (const scenario of [
      {name:'desktop', width:1280,height:900},
      {name:'two-columns',width:900,height:700},
      {name:'winter',width:1280,height:900,design:'winter'},
      {name:'lite',width:1280,height:900,design:'lite',noMotion:true},
      {name:'reduced-motion',width:1280,height:900,reducedMotion:'reduce',noMotion:true},
      {name:'portrait',width:390,height:844,mobile:true},
      {name:'landscape',width:844,height:390,mobile:true},
    ]) {
      const context=await browser.newContext({viewport:{width:scenario.width,height:scenario.height},
        hasTouch:!!scenario.mobile,isMobile:!!scenario.mobile,reducedMotion:scenario.reducedMotion || 'no-preference'});
      try {
        await context.addInitScript(design => {
          localStorage.setItem('owrtRemote:hubDesign',design);
          localStorage.setItem('owrtRemote:seasonEffectMode','off');
        },scenario.design || 'default');
        await context.route('**/*', async route => {
          const u=new URL(route.request().url());
          if (u.pathname === '/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
          if (u.pathname === '/api/routers') return route.fulfill({json:{routers}});
          if (u.pathname === '/api/auth/meta') return route.fulfill({json:{ok:true,auth:{username:'owner',is_owner:true}}});
          if (u.pathname === '/api/notifications/wait') return route.abort();
          if (u.pathname.startsWith('/api/')) return route.fulfill({json:{ok:true,groups:[],notifications:[]}});
          return route.abort();
        });
        const page=await context.newPage();
        const errors=[];page.on('pageerror', e=>errors.push(e.message));
        await page.goto('http://hub.test/',{waitUntil:'domcontentloaded'});
        await page.locator('.routerCardSlot').nth(8).waitFor();
        const toggles=page.locator('[data-card-toggle]');
        assert.equal(await toggles.count(),9,scenario.name);
        assert.equal(await page.locator('.cardBody:visible').count(),0,'compact at entry');
        await toggles.first().scrollIntoViewIfNeeded();
        await page.waitForTimeout(50);
        const before=await geometry(page);
        const docHeight=await page.evaluate(()=>document.documentElement.scrollHeight);
        await toggles.first().click();
        assert.equal(await toggles.first().getAttribute('aria-expanded'),'true');
        if (!scenario.mobile) {
          assert.equal(await page.locator('.isFloating').count(),1);
          assert.equal(await page.evaluate(()=>cardDetailsAnimations.size>0),!scenario.noMotion,scenario.name+' animation policy');
          sameGrid(before,await geometry(page),scenario.name+' during animation');
          await settled(page);
          sameGrid(before,await geometry(page),scenario.name+' open');
          if (scenario.height-before[0].y-12>=400) {
            assert(Math.abs((await page.locator('.isFloating>.card').boundingBox()).y-before[0].y)<1,'expansion stays anchored when there is room');
          }
          assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight),docHeight,'page height stable');
          await fitsViewport(page);
          const body=page.locator('.isFloating .cardBody');
          const scrolled=await body.evaluate(el=>{el.scrollTop=80;return el.scrollTop;});
          await page.evaluate(()=>loadRouters());
          await page.waitForTimeout(60);
          assert.equal(await page.locator('.isFloating .cardBody').evaluate(el=>el.scrollTop),scrolled,'poll preserves scroll');
          sameGrid(before,await geometry(page),scenario.name+' polling');
          await toggles.first().focus();
          await page.evaluate(()=>loadRouters());
          assert.equal(await toggles.first().evaluate(el=>el===document.activeElement),true,'poll preserves keyboard focus');
          await toggles.nth(1).click();
          await settled(page);
          assert.equal(await page.locator('.isFloating').count(),1,'one desktop expansion');
          assert.equal(await toggles.first().getAttribute('aria-expanded'),'false');
          sameGrid(before,await geometry(page),scenario.name+' switch');
          await page.screenshot({path:path.join(output,scenario.name+'.png'),fullPage:false});
          await page.keyboard.press('Escape');
          await settled(page);
          assert.equal(await page.locator('.isFloating').count(),0,'Escape closes');
          sameGrid(before,await geometry(page),scenario.name+' close');
          await toggles.last().scrollIntoViewIfNeeded();
          await toggles.last().click();await settled(page);await fitsViewport(page);
          await page.locator('#routerStats').evaluate(el=>el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true})));
          await settled(page);
          assert.equal(await page.locator('.isFloating').count(),0,'outside pointer closes');
          await page.evaluate(()=>{
            const toggle=document.querySelector('[data-card-toggle]');
            toggle.click();toggle.click();toggle.click();
          });
          await settled(page);
          assert.equal(await toggles.first().getAttribute('aria-expanded'),'true','rapid clicks finish open');
          await toggles.first().click();await settled(page);
          assert.equal(await page.locator('.isFloating').count(),0,'toggle closes');
          await toggles.first().scrollIntoViewIfNeeded();
          await toggles.first().click();await settled(page);
          await page.setViewportSize({width:390,height:844});
          await page.waitForFunction(()=>mobileCardsMq.matches && !document.querySelector('.isFloating'));
          assert.equal(await toggles.first().getAttribute('aria-expanded'),'true','resizing to mobile keeps data open');
          assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'resize has no overflow');
        } else {
          assert.equal(await page.locator('.isFloating').count(),0,'mobile stays inline');
          assert.equal(await page.locator('.cardBody:visible').count(),1);
          await page.evaluate(()=>loadRouters());
          assert.equal(await toggles.first().getAttribute('aria-expanded'),'true','mobile poll preserves state');
          await page.screenshot({path:path.join(output,scenario.name+'.png'),fullPage:false});
          await toggles.first().click();
          assert.equal(await page.locator('.cardBody:visible').count(),0);
          assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        }
        assert.deepEqual(errors,[],scenario.name+' browser errors');
        count++;console.log('CARD_DETAILS_OK',scenario.name);
      } finally {await context.close();}
    }
  } finally {await browser.close();}
  console.log('CARD_DETAILS_BROWSER_OK:',count,'scenarios');
})().catch(e=>{console.error(e);process.exitCode=1;});
