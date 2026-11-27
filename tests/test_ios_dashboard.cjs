// Actual embedded Hub HTML, isolated APIs and iOS browser geometry/motion.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const output=path.resolve('.test-output/ios-dashboard');fs.mkdirSync(output,{recursive:true});
const now=Math.floor(Date.now()/1000);
const routers=Array.from({length:11},(_,i)=>({id:'node-'+i,name:i===1?'Очень длинное имя роутера дома':'Роутер '+i,
  group_id:'',online:i%3!==2,last_seen:now-3,permissions:{owner:true,manage:true},
  status:{model:'Cudy TR3000 v1',xray:'running',memory_percent:40},tachyon:{installed:false}}));
const resources={ok:true,cpu:{percent:12.4},memory:{percent:36.8,used_bytes:395136991,total_bytes:1073741824},disk:{percent:41.2,used_bytes:8847632629,total_bytes:21474836480}};
const history={from:now-86400,to:now,interval:60,gap_after:90,points:[[now-120,7,11],[now-60,8,11],[now,8,11]]};
fs.writeFileSync(path.join(output,'fixture.json'),JSON.stringify({routers,resources,history}));
execFileSync('python',['-c',String.raw`
import importlib.util,json,os,sys,tempfile
from pathlib import Path
from unittest import mock
f=json.loads(Path('.test-output/ios-dashboard/fixture.json').read_text(encoding='utf-8'))
with tempfile.TemporaryDirectory() as state:
 os.environ['OWRT_REMOTE_STATE_DIR']=state
 spec=importlib.util.spec_from_file_location('ios_dashboard','vps/owrt-remote-hub.py');h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)
 with mock.patch.object(h,'load_auth',return_value={'username':'owner','captcha':{'mode':'digits'}}),mock.patch.object(h,'web_push_backend_status',return_value={'ready':False}):
  html=h.dashboard_html(f['routers'],'owner',auth_bootstrap={'username':'owner','is_owner':True},vps_resources=f['resources'],availability=f['history'])
 Path('.test-output/ios-dashboard/actual.html').write_text(html,encoding='utf-8')
 Path('.test-output/ios-dashboard/photos.json').write_text(json.dumps(h.HUB_ROUTER_MODEL_PHOTO_DATA),encoding='utf-8')
`],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const html=fs.readFileSync(path.join(output,'actual.html'),'utf8');
const photos=JSON.parse(fs.readFileSync(path.join(output,'photos.json'),'utf8'));
const iosUA='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
(async()=>{
 for(const engine of (process.env.OWRT_IOS_WEBKIT_ONLY?[webkit]:(fs.existsSync(webkit.executablePath())?[chromium,webkit]:[chromium]))){
  const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});
  try{for(const width of [320,390,430,844]){
   const context=await browser.newContext({viewport:{width,height:width===844?390:900},userAgent:iosUA,isMobile:true,hasTouch:true});
   await context.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{value:true});localStorage.setItem('owrtRemote:seasonEffectMode','off');localStorage.setItem('owrtRemote:hubDesign','default');});
   const page=await context.newPage(),errors=[];let photoRequests=0;page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const u=new URL(route.request().url());
    if(u.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(u.pathname.startsWith('/router-model-photos/')){photoRequests++;return route.fulfill({contentType:'image/webp',body:Buffer.from(photos[u.pathname.split('/').pop()],'base64')});}
    if(u.pathname==='/api/routers')return route.fulfill({json:{routers,availability:history}});
    if(u.pathname==='/api/vps/resources')return route.fulfill({json:resources});
    if(u.pathname==='/api/auth/meta')return route.fulfill({json:{auth:{username:'owner',is_owner:true}}});
    if(u.pathname==='/api/notifications/wait')return route.fulfill({status:429,json:{busy:true}});
    return route.fulfill({json:{ok:true,groups:[],notifications:[]}});
   });
   await page.goto('http://hub.test/');
   try {await page.waitForFunction(()=>document.querySelector('.hubNetwork')?.dataset.visibleCount==='8');}
   catch(error){console.error({engine:engine.name(),width,url:page.url(),errors,state:await page.evaluate(()=>({standalone:navigator.standalone,ios:document.documentElement.dataset.hubIos,body:document.body.innerText.slice(0,250),map:document.querySelector('.hubNetwork')?.outerHTML.slice(0,400)}))});throw error;}
   assert.equal(await page.locator('html').getAttribute('data-hub-ios'),'1');
   assert(await page.locator('.hubVantageIntro h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))<=14,'Compact iOS network status heading');
   assert.equal(await page.locator('.hubNetworkBranch').count(),8);
   for(const selector of ['.hubNetworkHeading','.hubDiskTitle','.hubOverviewHead>.hubSectionTitle','.hubVantageIntro>div','.hubRoutersHeading>.hubSectionTitle']){
    const aligned=await page.locator(selector).first().evaluate(el=>{
     const box=el.getBoundingClientRect(),panel=el.closest('.hubNetworkHead,.hubOverviewHead,.hubVantageIntro,.hubRoutersHeading').getBoundingClientRect();
     return {center:box.left+box.width/2,expected:panel.left+panel.width/2};
    });
    assert(Math.abs(aligned.center-aligned.expected)<2,selector+' centered '+JSON.stringify(aligned));
   }
   const heights=await page.locator('.hubKpiRow .statCard').evaluateAll(els=>els.map(el=>el.getBoundingClientRect().height));
   assert(Math.max(...heights)<95,'compact iOS statistics '+heights);
   const disk=await page.locator('.hubDiskRing').boundingBox(),banner=await page.locator('.hubDiskBanner').boundingBox();
   assert(Math.abs(disk.x+disk.width/2-banner.x-banner.width/2)<2,'disk ring centered');
   const pairs=await page.locator('.hubDiskSpace>div').evaluateAll(rows=>rows.map(row=>{
    const key=row.querySelector('dt').getBoundingClientRect(),value=row.querySelector('dd').getBoundingClientRect();
    return {gap:value.left-key.right,width:row.getBoundingClientRect().width};
   }));
   assert(pairs.every(pair=>pair.gap<=11&&pair.width<200),'disk label/value pairs stay compact '+JSON.stringify(pairs));
   await page.locator('.hubNetworkStage').scrollIntoViewIfNeeded();
   const pulse=page.locator('.hubNetworkPulse').first();
   await page.waitForFunction(()=>document.querySelector('.hubNetworkWires')?.getBoundingClientRect().width>0&&document.querySelector('.hubNetworkPulse'));
   assert.equal(await pulse.evaluate(el=>getComputedStyle(el).display),'inline');
   const offset=await pulse.evaluate(el=>getComputedStyle(el).strokeDashoffset);
   await page.waitForTimeout(180);
   assert.notEqual(await pulse.evaluate(el=>getComputedStyle(el).strokeDashoffset),offset,'wire animation moves');
   const endpoints=await page.locator('.hubNetworkWires').evaluate(svg=>Array.from(svg.querySelectorAll('.hubNetworkWire')).map(p=>{
    const end=p.getPointAtLength(p.getTotalLength()),matrix=svg.getScreenCTM();return {x:end.x*matrix.a+matrix.e,y:end.y*matrix.d+matrix.f};}));
   const targets=await page.locator('.hubNetworkBranch').evaluateAll(els=>els.map(el=>{const b=el.getBoundingClientRect();return {left:b.left,right:b.right,y:b.top+b.height/2};}));
   endpoints.forEach((p,i)=>{assert(Math.abs(p.y-targets[i].y)<2,'wire ends at row '+i);assert(Math.min(Math.abs(p.x-targets[i].left),Math.abs(p.x-targets[i].right))<2,'wire ends at router edge '+i);});
   await page.locator('.hubNetworkToggle').click();assert.equal(await page.locator('.hubNetworkBranch').count(),11);
   await page.waitForFunction(()=>document.querySelectorAll('.hubNetworkWire').length===11);
   await page.locator('.hubNetworkToggle').click();assert.equal(await page.locator('.hubNetworkBranch').count(),8);
   const firstCard=page.locator('.card[data-router-id="node-0"]');
   await firstCard.locator('.hubRouterPhoto').scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>document.querySelector('.card[data-router-id="node-0"] .hubRouterPhotoImage')?.naturalWidth>0);
   await page.evaluate(()=>{
    window.photoBefore=document.querySelector('.card[data-router-id="node-0"] .hubRouterPhotoImage');
    window.photoDetached=0;window.photoSrcWrites=0;
    window.photoObserver=new MutationObserver(records=>records.forEach(record=>{
     for(const removed of record.removedNodes)if(removed===photoBefore||removed.contains?.(photoBefore))photoDetached++;
     if(record.type==='attributes'&&record.target===photoBefore)photoSrcWrites++;
    }));
    photoObserver.observe(document.querySelector('#cards'),{subtree:true,childList:true,attributes:true,attributeFilter:['src','srcset']});
   });
   for(let update=0;update<8;update++){
    await page.evaluate(n=>{const r=window.ROUTERS.find(r=>r.id==='node-0');r.online=n%2===0;r.status.memory_percent=40+n;r.notes_preview=n%2?'Изменённая заметка':'';renderRouterView();},update);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   }
   assert.deepEqual(await page.evaluate(()=>({detached:photoDetached,srcWrites:photoSrcWrites,same:photoBefore===document.querySelector('.card[data-router-id="node-0"] .hubRouterPhotoImage'),decoded:photoBefore.complete&&photoBefore.naturalWidth>0})),{detached:0,srcWrites:0,same:true,decoded:true},'iOS photo and ancestors stay connected through real status/notes updates');
   await page.evaluate(()=>photoObserver.disconnect());
   await page.setViewportSize({width:width===844?390:844,height:width===844?900:390});
   await page.waitForTimeout(120);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'rotated iOS fits viewport');
   await page.setViewportSize({width,height:width===844?390:900});
   await page.locator('.hubNetworkStage').scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(output,engine.name()+'-'+width+'.png'),fullPage:true});
   assert(photoRequests<=2,'one photo URL does not reload on each status refresh: '+photoRequests);
   await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await pulse.evaluate(el=>getComputedStyle(el).display),'none');
   assert.deepEqual(errors,[]);await context.close();console.log('IOS_DASHBOARD_OK',engine.name(),width);
  }}finally{await browser.close();}
 }
})().catch(error=>{console.error(error);process.exit(1)});
