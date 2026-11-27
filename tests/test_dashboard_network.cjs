/* Browser contract for the network map using only permitted, supplied snapshots. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/dashboard-network');
fs.mkdirSync(output, {recursive:true});
const css = fs.readFileSync(path.resolve('vps/dashboard-network.css'),'utf8');
const js = fs.readFileSync(path.resolve('vps/dashboard-network.js'),'utf8');
const html = '<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>' +
  ':root{color-scheme:dark;--panel:#15171a;--panel2:#1a1d21;--line:#2a2e34;--text:#e8eaed;--muted:#8b929d;--cyan:#c5ed34;--green:#49cbb1;--red:#ec8f9c}' +
  '*{box-sizing:border-box}body{margin:0;background:#0d0e10;color:var(--text);font:13px system-ui,sans-serif}#host{max-width:1180px;margin:16px auto;padding:0 10px}button{font:inherit;cursor:pointer}' + css +
  '</style></head><body class="hubDashboard"><main id="host"></main><script>' + js + '</script><script>' +
  'window.opens=[];window.fixtureMap=createHubNetworkMap("#host",{onOpenRouter:(id,row)=>opens.push({id,name:row.name,seen:row.last_seen})});' +
  '</script></body></html>';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    for(const width of [320,390,844,1280,1920]) {
      const context=await browser.newContext({viewport:{width,height:900},isMobile:width<700,hasTouch:width<700});
      const page=await context.newPage(),errors=[];
      let requests=0;
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',async route=>{requests++;await route.fulfill({contentType:'text/html; charset=utf-8',body:html});});
      await page.goto('http://127.0.0.1:9999/fixture');
      const navigationRequests=requests;
      await page.waitForFunction(()=>Boolean(window.fixtureMap));
      assert.equal(await page.locator('.hubNetworkEmpty').isVisible(),true);
      assert.equal(await page.locator('.hubNetworkStage').isVisible(),false);
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-router-count'),'0');
      const routers=Array.from({length:18},(_,i)=>({id:i===0?'__proto__':i===1?'node-"<img>':'node-'+i,
        name:i===0?'<img src=x onerror="window.networkInjected=true">':'Площадка '+i,
        online:i%4!==0,status:{xray:i%3===0?'stopped':'running'},last_seen:10+i}));
      await page.evaluate(routers=>fixtureMap.update(routers),routers);
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-router-count'),'18');
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-online-count'),'13');
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-offline-count'),'5');
      assert.equal(await page.locator('.hubNetworkBranches .hubNetworkBranch').count(),7);
      assert.equal(await page.locator('.hubNetworkToggle').textContent(),'Показать все');
      assert.equal(await page.locator('.hubNetworkCount').textContent(),'Показано 7 из 18');
      assert.deepEqual(await page.locator('.hubNetworkBranch').evaluateAll(nodes=>nodes.map(el=>el.dataset.routerId)),routers.slice(0,7).map(r=>r.id));
      assert.equal(await page.locator('.hubNetwork img').count(),0);
      assert.equal(await page.evaluate(()=>window.networkInjected===true),false);
      assert.equal(await page.locator('.hubNetworkFootHelp,.hubNetworkFootXray').count(),0);
      assert(!await page.locator('.hubNetwork').textContent().then(text=>text.includes('Xray онлайн-роутеров')||text.includes('доступность SSH-туннеля')));
      assert.equal(await page.locator('.hubNetworkWire.is-offline').count(),2);
      assert.equal(await page.locator('.hubNetworkPulse').count(),6,'one browser wire plus five online diagram wires');
      const target=page.locator('.hubNetworkBranches .hubNetworkBranch').nth(1);
      await target.focus();
      await page.evaluate(()=>{window.stableMapButton=document.activeElement;});
      const changed=routers.map(r=>({...r,last_seen:r.last_seen+100}));
      await page.evaluate(routers=>fixtureMap.update(routers),changed);
      assert.equal(await page.evaluate(()=>stableMapButton===document.activeElement),true,'polling keeps existing focus/DOM');
      await target.click();
      assert.deepEqual(await page.evaluate(()=>opens.at(-1)),{id:'node-"<img>',name:'Площадка 1',seen:111});
      await page.locator('.hubNetworkToggle').click();
      assert.equal(await page.locator('.hubNetworkBranch').count(),18);
      assert.equal(await page.locator('.hubNetworkToggle').textContent(),'Скрыть все');
      assert.equal(await page.locator('.hubNetworkToggle').getAttribute('aria-expanded'),'true');
      await page.evaluate(routers=>fixtureMap.update(routers),changed);
      assert.equal(await page.locator('.hubNetworkBranch').count(),18,'polling keeps map expanded');
      await page.locator('.hubNetworkBranch').last().click();
      assert.equal((await page.evaluate(()=>opens.at(-1))).id,'node-17');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'expanded map fits viewport');
      await page.screenshot({path:path.join(output,'map-expanded-'+width+'.png'),fullPage:true});
      await page.locator('.hubNetworkToggle').click();
      assert.equal(await page.locator('.hubNetworkBranch').count(),7,'collapse restores first seven');
      assert.equal(await page.locator('.hubNetworkToggle').getAttribute('aria-expanded'),'false');
      await page.locator('.hubNetworkToggle').click();
      const filtered=routers.filter(r=>!r.online);
      await page.evaluate(routers=>fixtureMap.update(routers),filtered);
      assert.equal(await page.locator('.hubNetworkBranch').count(),5,'filter replaces every stale branch including overflow');
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-router-count'),'5');
      assert.equal(await page.locator('.hubNetworkControls').isVisible(),false);
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-expanded'),'false');
      assert.equal(await page.locator('.hubNetworkPulse').count(),1,'offline branches never animate');
      await page.evaluate(()=>fixtureMap.setMotion(false));
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true');
      assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).animationPlayState),'paused');
      await page.evaluate(()=>fixtureMap.setMotion(true));
      await page.emulateMedia({reducedMotion:'reduce'});
      assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).display),'none');
      await page.emulateMedia({reducedMotion:'no-preference'});
      await page.evaluate(()=>document.documentElement.dataset.hubDesign='lite');
      assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).display),'none');
      await page.evaluate(()=>delete document.documentElement.dataset.hubDesign);
      await page.evaluate(()=>fixtureMap.setVisible(false));
      assert.equal(await page.locator('.hubNetwork').isVisible(),false);
      assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true');
      await page.evaluate(()=>fixtureMap.setVisible(true));
      await page.evaluate(routers=>fixtureMap.update(routers),routers.slice(0,6));
      const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
        outside:Array.from(document.querySelectorAll('.hubNetworkBranch,.hubNetworkNode')).filter(el=>{
          const box=el.getBoundingClientRect();return box.width>0&&(box.left<0||box.right>innerWidth+1);
        }).length}));
      assert.ok(layout.scroll<=layout.width+1,JSON.stringify(layout));
      assert.equal(layout.outside,0,JSON.stringify(layout));
      await page.screenshot({path:path.join(output,'map-'+width+'.png'),fullPage:true});
      await page.evaluate(()=>fixtureMap.update([]));
      assert.equal(await page.locator('.hubNetworkBranch').count(),0);
      assert.equal(await page.locator('.hubNetworkEmpty').isVisible(),true);
      for(const n of [1,7,8]){
        await page.evaluate(rows=>fixtureMap.update(rows),routers.slice(0,n));
        assert.equal(await page.locator('.hubNetworkBranch').count(),Math.min(7,n));
        assert.equal(await page.locator('.hubNetworkControls').isVisible(),n>7,'toggle appears only for more than seven');
      }
      assert.equal(requests,navigationRequests,'map performs no additional network requests');
      assert.deepEqual(errors,[]);
      await page.evaluate(()=>fixtureMap.destroy());
      assert.equal(await page.locator('.hubNetwork').count(),0);
      await context.close();
      console.log('HUB_NETWORK_MAP_OK',width);
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
