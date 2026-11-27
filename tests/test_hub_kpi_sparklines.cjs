// Actual exported dashboard; only disposable API fixtures are used.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const output=path.resolve('.test-output/kpi-sparklines');fs.mkdirSync(output,{recursive:true});
execFileSync(process.env.TEST_PYTHON||'python',['-c',String.raw`
import importlib.util,json,os,sys,tempfile
from pathlib import Path
from unittest import mock
with tempfile.TemporaryDirectory() as state:
 os.environ['OWRT_REMOTE_STATE_DIR']=state
 spec=importlib.util.spec_from_file_location('sparkline_preview_hub','vps/owrt-remote-hub.py')
 hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
 routers=[dict(id='node-'+str(i+1),name='Тестовый роутер '+str(i+1),online=i<2,group_id='',permissions={'owner':True},status={'xray':'running','model':'Unknown'}) for i in range(3)]
 auth={'username':'owner','is_owner':True,'can_add_router':True}
 resources={'ok':True,'cpu':{'percent':1.2,'cores':1},'memory':{'percent':31.3},'disk':{'percent':40}}
 with mock.patch.object(hub,'web_push_backend_status',return_value={'ready':False}): page=hub.dashboard_html(routers,'owner',auth_bootstrap=auth,vps_resources=resources)
 out=Path('.test-output/kpi-sparklines')
 out.joinpath('dashboard.html').write_text(page,encoding='utf-8')
 out.joinpath('fixture.json').write_text(json.dumps({'routers':routers,'auth':auth,'resources':resources}),encoding='utf-8')
`],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const fixture=JSON.parse(fs.readFileSync(path.join(output,'fixture.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  for(const width of [320,390,1366,2560]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<681,hasTouch:width<681});
   await context.addInitScript(()=>{localStorage.setItem('owrtRemote:hubDesign','default');localStorage.setItem('owrtRemote:seasonEffectMode','off');});
   let routers=structuredClone(fixture.routers),resources=structuredClone(fixture.resources);
   let accountScope='owner',hold=true,blocked=[];
   const release=()=>{hold=false;blocked.splice(0).forEach(resolve=>resolve());};
   const requests=[],errors=[];
   await context.route('**/*',async route=>{
    const u=new URL(route.request().url());requests.push(u.pathname);
    if(u.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(output,'dashboard.html'),'utf8').replace('HubKpiSparks?.restore("owner")','HubKpiSparks?.restore('+JSON.stringify(accountScope)+')')});
    if(hold&&(u.pathname==='/api/routers'||u.pathname==='/api/vps/resources'))await new Promise(resolve=>blocked.push(resolve));
    if(u.pathname==='/api/routers')return route.fulfill({json:{routers}});
    if(u.pathname==='/api/vps/resources')return route.fulfill({json:resources});
    if(u.pathname==='/api/auth/meta')return route.fulfill({json:{ok:true,auth:fixture.auth}});
    if(u.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
    return route.fulfill({json:{ok:true,groups:[],notifications:[],sessions:[]}});
   });
   const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
   await page.goto('http://hub.test/',{waitUntil:'domcontentloaded'});
   await page.waitForFunction(()=>document.querySelectorAll('.hubKpiSpark[data-samples="1"]').length===6);
   assert.equal(await page.locator('.hubKpiSpark').count(),6);
   assert.equal(await page.locator('.hubKpiSparkLine').evaluateAll(nodes=>nodes.every(node=>!node.getAttribute('d').includes('L'))),true,'a first reading does not invent a fluctuating history');
   assert.equal(await page.locator('.hubKpiSparkLine').evaluateAll(nodes=>nodes.every(node=>node.getAttribute('d').includes('H198'))),true,'all six lines are present before any resource or router response arrives');
   release();
   // Verify wiring through the real existing router and resource request handlers.
   resources={...resources,cpu:{percent:9,cores:1},memory:{percent:40}};
   routers[0].online=false;
   await page.evaluate(()=>loadRouters());
   await page.waitForFunction(()=>document.querySelector('.hubKpiSpark[data-metric="cpu"]').getAttribute('aria-label').includes('9%'));
   assert((await page.locator('.hubKpiSpark[data-metric="online"]').getAttribute('aria-label')).includes('В сети: 1.'));
   const sampleCounts=await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.map(n=>n.dataset.samples));
   await page.locator('#routerStatusFilter').selectOption('offline');
   assert.deepEqual(await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.map(n=>n.dataset.samples)),sampleCounts,'filter changes do not generate samples or reset history');
   await page.locator('#routerStatusFilter').selectOption('all');
   const fixedNow=await page.evaluate(()=>{
    const next=Date.now()+6000;Date.now=()=>next;
    HubKpiSparks.recordResources({cpu:{percent:55},memory:{percent:60}},false);
    HubKpiSparks.recordRouters(window.ROUTERS);
    return next;
   });
   await context.addInitScript(now=>{Date.now=()=>now;},fixedNow);
   hold=true;
   await page.reload({waitUntil:'domcontentloaded'});
   assert(await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.length===6&&nodes.every(node=>Number(node.dataset.samples)>=2)),'reload restores real history immediately while requests are blocked');
   accountScope='different-fixture-account';
   await page.reload({waitUntil:'domcontentloaded'});
   assert(await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.length===6&&nodes.every(node=>node.dataset.samples==='1')),'different account never restores another account history');
   accountScope='owner';
   await page.reload({waitUntil:'domcontentloaded'});
   assert(await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.every(node=>Number(node.dataset.samples)>=2)),'owner history remains scoped to the original account');
   release();
   await page.evaluate(()=>{
    window.sparkRealNow=Date.now;window.sparkTestNow=Date.now();Date.now=()=>window.sparkTestNow;
    const rows=Array.from({length:6},(_,i)=>({id:'fixture-'+i,online:true,status:{xray:'running'}}));
    for(let n=0;n<60;n++){
     window.sparkTestNow+=5000;
     rows.forEach((row,i)=>{row.online=i<(n<30?6:5);row.status.xray=(i===0&&n>45)?'stopped':'running';});
     HubKpiSparks.recordRouters(rows);
     HubKpiSparks.recordResources({cpu:{percent:2+n%6},memory:{percent:30+n%4}},false);
    }
    window.ROUTERS=rows;renderRouterStats(rows);
   });
   resources={...resources,cpu:{percent:7,cores:1},memory:{percent:33}};
   await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
   await page.waitForFunction(()=>document.querySelector('.hubExtraStats [data-vps-resource="cpu"] strong').textContent==='7%');
   const before=await page.locator('.hubKpiSpark[data-metric="cpu"] .hubKpiSparkLine').getAttribute('d');
   await page.evaluate(()=>{
    const counts=Array.from(document.querySelectorAll('.hubKpiSpark')).map(node=>node.dataset.samples);
    renderRouterStats(window.ROUTERS);
    if(JSON.stringify(counts)!==JSON.stringify(Array.from(document.querySelectorAll('.hubKpiSpark')).map(node=>node.dataset.samples)))throw Error('rerender reset the history');
   });
   assert(before.includes(' L'),'CPU line follows the sampled measurements');
   assert((await page.locator('.hubKpiSpark[data-metric="total"] .hubKpiSparkLine').getAttribute('d')).includes(' H'),'router counts use steps without fractional interpolation');
   await page.locator('.hubKpiRow').screenshot({path:path.join(output,'kpi-'+width+'.png')});
   const bounds=await page.locator('.hubKpiSpark').evaluateAll(nodes=>nodes.map(svg=>{
    const box=svg.getBoundingClientRect(),card=svg.closest('.statCard').getBoundingClientRect();
    const caption=svg.closest('.statCard').querySelector('em').getBoundingClientRect();
    return {fits:box.left>=card.left&&box.right<=card.right+1&&box.top>=caption.bottom-1};
   }));
   assert(bounds.every(item=>item.fits),'charts fit below labels without covering them');
   await page.evaluate(()=>{
    window.sparkTestNow+=5000;HubKpiSparks.recordResources(null,true);
    window.sparkTestNow+=5000;HubKpiSparks.recordResources({cpu:{percent:0},memory:{percent:null}},false);
   });
   assert.equal(await page.locator('.hubKpiSpark[data-metric="cpu"]').getAttribute('data-segments'),'2','network failure creates a gap');
   assert.equal(await page.locator('.hubKpiSpark[data-metric="memory"]').getAttribute('data-stale'),'true','missing RAM does not become zero');
   assert((await page.locator('.hubKpiSpark[data-metric="cpu"]').getAttribute('aria-label')).includes('ЦП VPS: 0%'),'zero CPU is a real reading');
   await page.evaluate(()=>{for(let i=0;i<180;i++){window.sparkTestNow+=5000;HubKpiSparks.recordResources({cpu:{percent:3},memory:{percent:32}},false);}});
   assert(Number(await page.locator('.hubKpiSpark[data-metric="cpu"]').getAttribute('data-samples'))<=61,'history is bounded by its five-minute window');
   await page.evaluate(()=>{Date.now=window.sparkRealNow;});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal overflow');
   assert(requests.every(request=>request==='/'||request.startsWith('/api/')||request.startsWith('/favicon')),'charts use only existing dashboard requests: '+JSON.stringify([...new Set(requests)]));
   assert.deepEqual(errors,[]);
   console.log('HUB_KPI_SPARKLINES_OK',width);await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
