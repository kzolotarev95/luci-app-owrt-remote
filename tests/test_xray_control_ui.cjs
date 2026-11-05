const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/xray-control');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python', ['-c', String.raw`
import importlib.util, os, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='xray-control-preview-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR'] = state
    spec = importlib.util.spec_from_file_location('preview_hub', 'vps/owrt-remote-hub.py')
    hub = importlib.util.module_from_spec(spec); sys.modules[spec.name] = hub; spec.loader.exec_module(hub)
    page = hub.router_xray_control_html({'id':'main','name':'Роутер тест'}, 'csrf-test')
    Path('.test-output/xray-control/page.html').write_text(page, encoding='utf-8')
`], {env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const html = fs.readFileSync(path.join(output, 'page.html'), 'utf8');
const BASE = 1793890000;
const url = 'http://127.0.0.1:9999/router/main/xray-control';
(async()=>{
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    for (const [width,height] of [[1280,900],[320,740],[390,844],[844,390]]) {
      const page = await browser.newPage({viewport:{width,height}});
      await page.clock.install({time:new Date(BASE*1000)});
      const fixture = {now:BASE,history:[],reads:0,posts:[],inflight:0,maxInflight:0};
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*', async route=>{
        const req=route.request();
        if(new URL(req.url()).pathname.startsWith('/api/')) {
          fixture.inflight++;fixture.maxInflight=Math.max(fixture.maxInflight,fixture.inflight);
          try {
            if(req.method()==='POST') {
              assert.equal(req.headers()['x-xray-control-csrf'],'csrf-test');
              const body=req.postDataJSON();fixture.posts.push(body);
              if(body.action==='clear_history')fixture.history=fixture.history.filter(item=>['queued','delivered','running'].includes(item.status));
              else fixture.history.unshift({id:body.request_id,action:body.action,status:'queued',result:'',created_at:fixture.now,delivered_at:0,completed_at:0});
            } else fixture.reads++;
            await route.fulfill({json:{ok:true,name:'Роутер тест',online:true,xray:'running',supported:true,
              last_seen:fixture.now,server_time:fixture.now,heartbeat_interval:30,
              command_timeout_seconds:180,execution_timeout_seconds:45,history:fixture.history}});
          } finally {fixture.inflight--;}
        } else await route.fulfill({contentType:'text/html',body:html});
      });
      await page.goto(url);
      await page.waitForFunction(()=>document.getElementById('resultTitle').textContent==='Журнал действий пуст.');
      assert(await page.locator('#clearHistory').isDisabled());
      for(const action of ['start','stop','restart']) {
        await page.locator(`[data-action="${action}"]`).click();
        await page.waitForFunction(()=>document.getElementById('resultTitle').textContent.startsWith('Ожидает Heartbeat'));
        const before=fixture.reads;
        await page.clock.runFor(1100);
        assert((await page.locator('#operationTimer').textContent()).includes('Прошло: 00:01'));
        assert((await page.locator('#operationTimer').textContent()).includes('Ориентир до итогового ответа: ~'));
        assert.equal(fixture.reads,before,'seconds timer must not add network requests');
        assert.equal(await page.locator('[data-action]:disabled').count(),3);
        assert(await page.locator('#clearHistory').isDisabled(),'active-only journal cannot be erased');
        fixture.now+=12;
        fixture.history[0].status='succeeded';fixture.history[0].completed_at=fixture.now;
        fixture.history[0].result='Configuration OK.\nXray запущен; процесс проверен.\n'+'длинная_строка_'.repeat(25);
        await page.clock.runFor(3000);
        await page.waitForFunction(()=>document.getElementById('resultTitle').textContent.startsWith('Успешно'));
        assert((await page.locator('#operationTimer').textContent()).includes('00:12'));
        const completed=await page.locator('#operationTimer').textContent();
        await page.clock.runFor(1500);
        assert.equal(await page.locator('#operationTimer').textContent(),completed,'final duration stays fixed');
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal overflow at '+width);
        if(action==='restart')await page.screenshot({path:path.join(output,'completed-'+width+'.png'),fullPage:true});
        await page.locator('#clearHistory').click();
        await page.waitForFunction(()=>document.getElementById('resultTitle').textContent==='Журнал действий пуст.');
        assert.equal(await page.locator('.entry').count(),0);
        assert.equal(await page.locator('#resultOutput').textContent(),'');
        assert(await page.locator('#operationTimer').isHidden());
      }
      fixture.history=[{id:'a'.repeat(32),action:'restart',status:'running',created_at:fixture.now-20,delivered_at:fixture.now-15,completed_at:0,result:'Команда принята агентом.'}];
      await page.reload();
      await page.waitForFunction(()=>document.getElementById('operationTimer').textContent.includes('Прошло: 00:20'));
      const text=await page.locator('#operationTimer').textContent();
      await page.clock.setSystemTime(new Date((BASE+43200)*1000));
      assert.equal(await page.locator('#operationTimer').textContent(),text,'local clock changes must not shift elapsed time');
      await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
      const paused=fixture.reads;
      await page.clock.runFor(10000);
      assert.equal(fixture.reads,paused,'pagehide stops polls');
      fixture.now+=10;
      await page.evaluate(()=>{window.dispatchEvent(new Event('pageshow'));window.dispatchEvent(new Event('pageshow'));});
      await page.waitForFunction(()=>document.getElementById('operationTimer').textContent.includes('Прошло: 00:30'));
      await page.clock.runFor(6000);
      assert(fixture.reads<=paused+3,'pageshow must not create parallel poll loops');
      assert.equal(fixture.maxInflight,1);
      assert.deepEqual(errors,[]);
      console.log('XRAY_CONTROL_UI_OK',width,'all actions, timer, clear, reload and lifecycle');
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
