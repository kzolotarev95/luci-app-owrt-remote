// Real exported Hub UI: pointer drag, accessible arrows, persistence and filters.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium,webkit} = require('playwright');
const out=path.resolve('.test-output/router-order');fs.mkdirSync(out,{recursive:true});
execFileSync('python',['-c',String.raw`
import os,sys,tempfile,importlib.util
from pathlib import Path
with tempfile.TemporaryDirectory() as state:
 os.environ['OWRT_REMOTE_STATE_DIR']=state
 spec=importlib.util.spec_from_file_location('order_ui','vps/owrt-remote-hub.py');h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)
 routers=[{'id':f'node-{n}','name':f'Router {n}','online':n!=2,'group_id':'','permissions':{'owner':True,'manage':True},'status':{'model':'Cudy TR3000 v1'},'tachyon':{'installed':False}} for n in range(1,5)]
 Path('.test-output/router-order/hub.html').write_text(h.dashboard_html(routers,'owner',auth_bootstrap={'username':'owner','is_owner':True}),encoding='utf-8')
`],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const template=fs.readFileSync(path.join(out,'hub.html'),'utf8');
const base=JSON.parse(template.match(/window\.ROUTERS = (\[.*\]);/)[1]);
(async()=>{
 for(const engine of [chromium,webkit]) {
  const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});
  try {for(const width of (engine===webkit?[390]:[1280,390])) {
   let order=[],fail=false,moves=0;
   const rows=()=>base.map(row=>({...row,display_order:order.includes(row.id)?order.indexOf(row.id):null}));
   const html=()=>template.replace(/window\.ROUTERS = (\[.*\]);/,'window.ROUTERS = '+JSON.stringify(rows())+';');
   const context=await browser.newContext({viewport:{width,height:950},hasTouch:width<500,isMobile:width<500,
    ...(width<500?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'}:{})});
   await context.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{value:true});localStorage.setItem('owrtRemote:seasonEffectMode','off');localStorage.setItem('owrtRemote:hubDesign','default');});
   await context.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(u.pathname==='/')return route.fulfill({contentType:'text/html;charset=utf-8',body:html()});
    if(u.pathname==='/api/routers/order'){
     assert.equal(route.request().headers()['x-router-order'],'1');moves++;
     if(fail)return route.fulfill({status:503,json:{error:'Fixture: save unavailable'}});
     const body=route.request().postDataJSON();order=[...new Set([...order,...body.base_order])];
     order.splice(order.indexOf(body.id),1);order.splice(order.indexOf(body.target)+(body.placement==='after'?1:0),0,body.id);
     return route.fulfill({json:{ok:true,router_order:order}});
    }
    if(u.pathname==='/api/routers')return route.fulfill({json:{routers:rows(),router_order:order,router_order_supported:true}});
    if(u.pathname==='/api/auth/meta')return route.fulfill({json:{auth:{username:'owner',is_owner:true}}});
    if(u.pathname==='/api/notifications/wait')return route.fulfill({status:429,json:{busy:true}});
    if(u.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,groups:[],notifications:[]}});
    return route.abort();
   });
   const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const ids=()=>page.locator('#cards article').evaluateAll(els=>els.map(el=>el.dataset.routerId));
   const arrow=(id,direction)=>page.locator('[data-order-id="'+id+'"][data-router-move="'+direction+'"]');
   await page.goto('http://hub.test/');await page.locator('#cards article').nth(3).waitFor();
   const control=await arrow('node-1','previous').boundingBox();
   assert(control.width<=34.1&&control.height<=32.1,'Compact reorder controls');
   assert.deepEqual(await ids(),['node-1','node-2','node-3','node-4']);
   assert.equal(await arrow('node-1','previous').isDisabled(),true);
   await arrow('node-3','previous').click();await page.waitForFunction(()=>document.querySelector('.hubOrderStatus')?.textContent==='Порядок карточек сохранён');
   assert.deepEqual(await ids(),['node-1','node-3','node-2','node-4']);
   await page.evaluate(()=>loadRouters());assert.deepEqual(await ids(),order);
   await page.reload();await page.locator('#cards article').nth(3).waitFor();assert.deepEqual(await ids(),order);
   const handle=page.locator('[data-router-drag="node-4"]'),target=page.locator('article[data-router-id="node-1"]');
   await handle.scrollIntoViewIfNeeded();let source=await handle.boundingBox(),dest=await target.boundingBox();
   await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
   if(dest.y<0){
    await page.mouse.move(source.x+source.width/2,25,{steps:8});
    await page.waitForFunction(()=>document.querySelector('article[data-router-id="node-1"]').getBoundingClientRect().top>=160);
    await page.mouse.move(source.x+source.width/2,400);
    await page.waitForTimeout(60);
    dest=await target.boundingBox();
   }
   await page.mouse.move(dest.x+dest.width/2,dest.y+Math.min(60,dest.height/4),{steps:12});
   await page.waitForFunction(()=>window.HubRouterOrder.busy());
   await page.waitForFunction(()=>document.querySelector('.hubOrderTarget')?.dataset.routerId==='node-1');
   // A poll can finish while the gesture is active without replacing its handle.
   await page.evaluate(()=>{window.dragHandleBefore=document.querySelector('[data-router-drag="node-4"]');return loadRouters();});
   assert(await page.evaluate(()=>window.dragHandleBefore===document.querySelector('[data-router-drag="node-4"]')));
   await page.mouse.up();await page.waitForFunction(()=>!window.HubRouterOrder.busy());
   assert.deepEqual(await ids(),['node-4','node-1','node-3','node-2']);
   assert.equal(moves,2);
   // Cancellation leaves server and visible order unchanged.
   source=await page.locator('[data-router-drag="node-4"]').boundingBox();dest=await page.locator('article[data-router-id="node-1"]').boundingBox();
   await page.mouse.move(source.x+15,source.y+15);await page.mouse.down();await page.mouse.move(dest.x+25,dest.y+15,{steps:5});
   await page.keyboard.press('Escape');await page.mouse.up();assert.equal(moves,2);
   fail=true;await arrow('node-1','previous').click();await page.waitForFunction(()=>document.querySelector('.hubOrderStatus')?.dataset.error==='true');
   assert.deepEqual(await ids(),['node-4','node-1','node-3','node-2']);fail=false;
   await page.evaluate(()=>{routerStatusFilterValue='online';renderRouterView();});
   await arrow('node-3','previous').click();await page.waitForFunction(()=>!window.HubRouterOrder.busy());
   assert.deepEqual(await ids(),['node-4','node-3','node-1']);assert(order.includes('node-2'));
   await page.evaluate(()=>{routerStatusFilterValue='all';routerSearchQuery='Router 1';renderRouterView();});
   assert.deepEqual(await ids(),['node-1']);assert(await arrow('node-1','previous').isDisabled());assert(await arrow('node-1','next').isDisabled());
   await page.evaluate(()=>{routerSearchQuery='';openRouterGroups.add('other');routerGroups=[{id:'other',name:'Other group'}];window.ROUTERS.forEach(row=>row.group_id=row.id==='node-4'?'other':'');renderRouterView();});
   assert(await arrow('node-4','previous').isDisabled());assert(await arrow('node-4','next').isDisabled());
   assert.deepEqual(errors,[]);
   await page.screenshot({path:path.join(out,engine.name()+'-'+width+'.png'),fullPage:true});
   await context.close();console.log('ROUTER_ORDER_UI_OK',engine.name(),width);
  }}finally{await browser.close();}
 }
})().catch(error=>{console.error(error);process.exit(1)});
