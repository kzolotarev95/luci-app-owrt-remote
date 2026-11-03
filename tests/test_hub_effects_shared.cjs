// Compare actual canvas output and pointer physics across Hub pages. All APIs
// are local fixtures; no router, terminal session, or VPS is contacted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/hub-design');
fs.mkdirSync(output, {recursive:true});
const preview = path.join(output, 'shared-pages.json');
execFileSync(process.env.TEST_PYTHON || 'python', ['tests/test_hub_design_global.py','--export-preview',preview],
  {env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const pages = JSON.parse(fs.readFileSync(preview, 'utf8'));
const modes = ['snow','rain','cosmos','embers','orbit','prism','pulse','laser'];

async function renderSample(page, mode, usePointer) {
  return page.evaluate(({mode,usePointer}) => {
    seasonFxStop();
    const random = Math.random, now = Date.now;
    let seed = 12345;
    Math.random = () => ((seed = Math.imul(1664525,seed)+1013904223>>>0)/4294967296);
    Date.now = () => 1000;
    try {
      Object.assign(seasonalFxPointer,{x:0,y:0,lastX:0,lastY:0,targetWind:0,wind:0,lastMoveTs:0,active:false});
      setSeasonEffectMode(mode,{persist:false});
      if (usePointer) {
        window.dispatchEvent(new PointerEvent('pointermove',{clientX:90,clientY:110}));
        window.dispatchEvent(new PointerEvent('pointermove',{clientX:140,clientY:120}));
      }
      for (let i=1;i<=12;i++) window.testFrame(i*16);
      const bytes=seasonalFxCtx.getImageData(0,0,seasonalFxCanvas.width,seasonalFxCanvas.height).data;
      let digest=2166136261, visible=0;
      for(let i=0;i<bytes.length;i++) {digest=Math.imul(digest^bytes[i],16777619)>>>0;if(i%4===3&&bytes[i])visible++;}
      return {digest,visible,count:seasonalFxParticles.length,
        pointer:JSON.parse(JSON.stringify(seasonalFxPointer)),
        size:seasonalFxSize,pending:window.testFrames.size,
        particles:JSON.parse(JSON.stringify(seasonalFxParticles.slice(0,3)))};
    } finally {Math.random=random;Date.now=now;seasonFxStop();}
  }, {mode,usePointer});
}

(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const context=await browser.newContext({viewport:{width:900,height:700},deviceScaleFactor:1});
    await context.addInitScript(()=>{
      window.testFrames=new Map();let frameId=0;
      window.requestAnimationFrame=callback=>{window.testFrames.set(++frameId,callback);return frameId;};
      window.cancelAnimationFrame=id=>window.testFrames.delete(id);
      window.testFrame=ts=>{const callbacks=[...window.testFrames.values()];window.testFrames.clear();callbacks.forEach(fn=>fn(ts));};
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});
    });
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(pages[url.pathname])return route.fulfill({contentType:'text/html',body:pages[url.pathname]});
      if(url.pathname==='/api/routers')return route.fulfill({json:{routers:[]}});
      if(url.pathname==='/api/router-groups')return route.fulfill({json:{groups:[]}});
      if(url.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
      if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,routers:[],auth:{username:'root',is_owner:true}}});
      return route.abort();
    });
    const all=[],errors=[];
    const baseline={};
    for(const route of Object.keys(pages)){
      const page=await context.newPage();all.push(page);
      page.on('pageerror',error=>errors.push(route+': '+error.message));
      await page.goto('http://hub.test'+route);
      await page.waitForFunction(()=>hubAppearanceInitialized&&seasonEffectsInitialized,null,{polling:50,timeout:10000});
      await page.evaluate(()=>setHubDesign('winter',{persist:false}));
      assert.equal(await page.locator('canvas.seasonalFx').count(),1,route+' has one canvas');
      for(const mode of modes){
        const pointed=await renderSample(page,mode,true);
        const neutral=await renderSample(page,mode,false);
        assert.equal(pointed.pointer.x,140,route+' '+mode+' pointer X');
        assert.equal(pointed.pointer.y,120,route+' '+mode+' pointer Y');
        assert.equal(pointed.pointer.active,true);
        assert.equal(pointed.pending,1,'one animation loop');
        assert(pointed.visible>0,route+' '+mode+' actually draws pixels');
        assert.notEqual(pointed.digest,neutral.digest,route+' '+mode+' responds to pointer');
        if(route==='/')baseline[mode]=pointed;
        else assert.deepEqual(pointed,baseline[mode],route+' '+mode+' matches dashboard pixels and physics');
      }
      // Repeated initialization must not add a second pointer handler or loop.
      await page.evaluate(()=>{initHubAppearance();initSeasonEffects();setSeasonEffectMode('prism',{persist:false});});
      assert.equal(await page.evaluate(()=>testFrames.size),1,route+' repeated initialization');
    }
    // Changes from the dashboard reach already-open login, terminal, queue,
    // logs and notes tabs, including lite suppression and effect restoration.
    const main=all[0];
    for(const mode of modes){
      await main.evaluate(mode=>setSeasonEffectMode(mode),mode);
      for(const page of all.slice(1))await page.waitForFunction(mode=>seasonEffectMode===mode,mode,{polling:50,timeout:10000});
    }
    await main.evaluate(()=>setHubDesign('lite'));
    for(const page of all){
      await page.waitForFunction(()=>hubDesignMode==='lite',null,{polling:50,timeout:10000});
      assert.deepEqual(await page.evaluate(()=>[seasonalFxFrame,seasonalFxParticles.length,seasonalFxCanvas.width,seasonalFxCanvas.height]),[0,0,1,1]);
    }
    await main.evaluate(()=>setHubDesign('winter'));
    for(const page of all){
      await page.waitForFunction(()=>hubDesignMode==='winter'&&seasonalFxFrame>0,null,{polling:50,timeout:10000});
      assert.equal(await page.evaluate(()=>seasonEffectMode),'laser');
      await page.evaluate(()=>{window.dispatchEvent(new PointerEvent('pointermove',{clientX:280,clientY:210}));});
      assert.deepEqual(await page.evaluate(()=>[seasonalFxPointer.x,seasonalFxPointer.y,seasonalFxPointer.active]),[280,210,true]);
      await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
      assert.deepEqual(await page.evaluate(()=>[seasonalFxPointer.active,seasonalFxPointer.targetWind]),[false,0]);
    }
    assert.deepEqual(errors,[]);
    await context.close();
    console.log('HUB_SHARED_EFFECTS_OK: 8 effects, 7 pages, identical canvas pixels and cursor physics, single loop, cross-tab design/effect sync, lite pause/restore');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
