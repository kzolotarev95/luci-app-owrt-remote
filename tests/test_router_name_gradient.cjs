// Real dashboard markup and native WebKit rendering, without user infrastructure.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.test-output/router-name-gradient');
fs.mkdirSync(out,{recursive:true});
execFileSync('python',['-c',String.raw`
import importlib.util,json,os,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as state:
 os.environ['OWRT_REMOTE_STATE_DIR']=state
 spec=importlib.util.spec_from_file_location('gradient_hub','vps/owrt-remote-hub.py');h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)
 routers=[{'id':id,'name':name,'online':id!='node-3','group_id':'','permissions':{'owner':True,'manage':True,'admin':True,'ssh':True},'status':{'model':'Cudy WBR3000UAX v1','xray':'running','temperature':'43.8','memory':'100 MB free / 200 MB'},'tachyon':{'installed':False}} for id,name in [('main','Основа'),('node-2','Советская 30'),('node-3','Очень длинное имя <Дом> & сеть node-3')]]
 Path('.test-output/router-name-gradient/hub.html').write_text(h.dashboard_html(routers,'owner',auth_bootstrap={'username':'owner','is_owner':True}),encoding='utf-8')
`],{cwd:root,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const html=fs.readFileSync(path.join(out,'hub.html'),'utf8');
const rows=JSON.parse(html.match(/window\.ROUTERS = (\[.*\]);/)[1]);
(async()=>{
 for(const engine of [chromium,webkit]){
  const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});
  try{for(const width of engine===webkit?[390,320]:[1920,390]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<500,hasTouch:width<500,
    ...(width<500?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'}:{})});
   await context.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{value:true});localStorage.setItem('owrtRemote:hubDesign','default');localStorage.setItem('owrtRemote:seasonEffectMode','off');});
   await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/')return route.fulfill({contentType:'text/html;charset=utf-8',body:html});
    if(url.pathname.startsWith('/router-model-photos/')){
     const file=path.join(root,'vps/router-model-photos',path.basename(url.pathname).replace(/\.[0-9a-f]{12}(?=\.webp$)/,''));
     if(fs.existsSync(file))return route.fulfill({contentType:'image/webp',body:fs.readFileSync(file)});
    }
    if(url.pathname==='/api/routers')return route.fulfill({json:{routers:rows}});
    if(url.pathname==='/api/auth/meta')return route.fulfill({json:{auth:{username:'owner',is_owner:true}}});
    if(url.pathname==='/api/notifications/wait')return route.fulfill({status:429,json:{busy:true}});
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,groups:[],notifications:[]}});
    return route.abort();
   });
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://hub.test/');
   await page.locator('#cards article').nth(2).waitFor();
   await page.locator('article[data-router-id="main"] .hubRouterPhoto').scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>document.querySelector('article[data-router-id="main"] .hubRouterPhoto')?.classList.contains('has-photo'));
   await page.waitForTimeout(200);
   const names=page.locator('#cards .name .routerNameGradient');
   assert.equal(await names.count(),3);
   for(const name of await names.all()){
    const css=await name.evaluate(el=>({gradient:getComputedStyle(el).backgroundImage,fill:getComputedStyle(el).webkitTextFillColor,animation:getComputedStyle(el).animationName}));
    assert(css.gradient.includes('rgb(33, 143, 196)')&&css.gradient.includes('rgb(255, 48, 48)'),JSON.stringify(css));
    assert.equal(css.fill,'rgba(0, 0, 0, 0)');assert.equal(css.animation,'none');
   }
   assert.equal(await page.locator('.hubNetworkBranchName .routerNameGradient').count(),3);
   assert.equal(await page.locator('article[data-router-id="node-3"] .name').textContent(),rows[2].name);
   assert.equal(await page.locator('article[data-router-id="node-3"] .name script').count(),0);
   await page.evaluate(()=>{window.previewNameNode=document.querySelector('article[data-router-id="main"] .routerNameGradient');window.previewPhoto=document.querySelector('article[data-router-id="main"] .hubRouterPhotoImage');});
   await page.evaluate(()=>loadRouters());
   assert.equal(await page.locator('article[data-router-id="main"] .routerNameGradient').textContent(),'Основа');
   assert((await page.locator('article[data-router-id="main"] .routerNameGradient').evaluate(el=>getComputedStyle(el).backgroundImage)).includes('linear-gradient'),'poll preserves gradient styling');
   assert(await page.evaluate(()=>window.previewPhoto===document.querySelector('article[data-router-id="main"] .hubRouterPhotoImage')&&window.previewPhoto.isConnected),'poll preserves photo DOM');
   await page.evaluate(()=>{window.ROUTERS.find(r=>r.id==='main').name='Переименованный домашний роутер 👋';renderRouterView();});
   assert.equal(await page.locator('article[data-router-id="main"] .name .routerNameGradient').textContent(),'Переименованный домашний роутер 👋');
   for(const design of ['winter','lite','default']){
    await page.evaluate(design=>document.documentElement.dataset.hubDesign=design,design);
    assert((await names.first().evaluate(el=>getComputedStyle(el).backgroundImage)).includes('linear-gradient'));
   }
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'long name does not overflow');
   for(const name of await names.all()) assert(await name.evaluate(el=>el.getBoundingClientRect().width<=el.closest('.name').getBoundingClientRect().width+1));
   await page.locator('#cards').screenshot({path:path.join(out,engine.name()+'-'+width+'.png')});
   assert.deepEqual(errors,[]);
   await context.close();console.log('ROUTER_NAME_GRADIENT_OK',engine.name(),width);
  }}finally{await browser.close();}
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
