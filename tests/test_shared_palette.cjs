// Render real Hub pages with isolated APIs. Never connect to a router or VPS.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process'),{chromium,webkit}=require('playwright');
const out=path.resolve('.test-output/shared-palette');fs.mkdirSync(out,{recursive:true});
execFileSync(process.env.TEST_PYTHON||'python',['tests/test_hub_design_global.py','--export-preview',path.join(out,'pages.json')],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const pages=JSON.parse(fs.readFileSync(path.join(out,'pages.json'),'utf8'));
(async()=>{for(const engine of [chromium,webkit]){const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});try{
 for(const width of engine===webkit?[390]:[390,1366]){
  const context=await browser.newContext({viewport:{width,height:900},isMobile:width<681,hasTouch:width<681});
  await context.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{value:true});localStorage.setItem('owrtRemote:hubDesign','default');localStorage.setItem('owrtRemote:seasonEffectMode','off');});
  await context.route('**/*',route=>{const u=new URL(route.request().url());
   if(pages[u.pathname])return route.fulfill({contentType:'text/html; charset=utf-8',body:pages[u.pathname]});
   if(u.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
   if(u.hostname!=='hub.test')return route.abort();
   return route.fulfill({json:{ok:true,routers:[],commands:[],groups:[],notes:[],auth:{is_owner:true},job:null}});
  });
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const route of Object.keys(pages)){
   await page.goto('http://hub.test'+route);
   await page.waitForFunction(()=>hubAppearanceInitialized);
   const palette=await page.evaluate(()=>{
    const root=getComputedStyle(document.documentElement),body=getComputedStyle(document.body);
    return {bg:root.getPropertyValue('--bg').trim(),panel:root.getPropertyValue('--panel').trim(),panel2:root.getPropertyValue('--panel2').trim(),code:root.getPropertyValue('--code').trim(),body:body.backgroundColor==='rgba(0, 0, 0, 0)'?root.backgroundColor:body.backgroundColor};
   });
   const expected={bg:'#0d0e10',panel:'#15171a',panel2:'#1a1d21',code:'#1a1d21'};
   if(engine===chromium)expected.body='rgb(13, 14, 16)';else delete palette.body;
   assert.deepEqual(palette,expected,route+' uses dashboard greys');
   for(const design of ['default','winter','lite']){
    const red=await page.evaluate(design=>{document.documentElement.dataset.hubDesign=design;return getComputedStyle(document.documentElement).getPropertyValue('--red').trim();},design);
    assert.equal(red,'#ff3030',route+' bright red in '+design);
   }
   await page.evaluate(()=>document.documentElement.dataset.hubDesign='default');
   // Existing surface/terminal checks below target Chromium. WebKit is added
   // here to verify the shared red across every page and all three designs.
   if(engine===webkit)continue;
   if(route!=='/'){
    assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundImage),'none',route+' has no legacy background');
    assert.equal(await page.evaluate(()=>getComputedStyle(document.body,'::before').backgroundImage),'none',route+' has no legacy aura');
   }
   if(route==='/'){
    const support=page.locator('.hubNavDonate');
    assert.equal(await support.evaluate(el=>getComputedStyle(el).color),'rgb(232, 186, 102)','support link stays gold');
    if(width>680){await support.hover();assert.equal(await support.evaluate(el=>getComputedStyle(el).color),'rgb(232, 186, 102)');}
   }
   if(route.includes('terminal')||route.startsWith('/ssh/')){
    assert.equal(await page.locator('#terminal').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(26, 29, 33)');
    const theme=await page.evaluate(()=>terminalTheme());assert.equal(theme.background,'#1a1d21');assert.equal(theme.foreground,'#e8eaed');
   }
   await page.screenshot({path:path.join(out,engine.name()+'-'+route.replace(/\W+/g,'_')+'-'+width+'.png'),fullPage:false});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),route+' has no overflow');
  }
  assert.deepEqual(errors,[]);console.log('SHARED_PALETTE_OK',engine.name(),width,Object.keys(pages).length,'pages');await context.close();
 }
}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
