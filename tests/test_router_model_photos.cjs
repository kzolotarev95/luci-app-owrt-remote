// Real dashboard and images; all APIs intercepted, no router/VPS contacted.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');const {chromium,webkit}=require('playwright');
const output=path.resolve('.test-output/router-model-photos');fs.mkdirSync(output,{recursive:true});
execFileSync(process.env.TEST_PYTHON||'python',['-c',String.raw`
import importlib.util,json,os,sys,tempfile,time
from pathlib import Path
from unittest import mock
with tempfile.TemporaryDirectory() as state:
 os.environ['OWRT_REMOTE_STATE_DIR']=state
 spec=importlib.util.spec_from_file_location('photo_browser_hub','vps/owrt-remote-hub.py')
 hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
 names=['Cudy WBR3000UAX v1 (OpenWrt U-Boot layout)','Xiaomi Mi Router AX3000T (OpenWrt U-Boot layout)','Netis N6 v2','ASUS TUF-AX4200Q','Xiaomi Redmi Router AX6S','Netis NX62','Friendly NanoPi R3S','QEMU Standard PC (1440FX + PIX, 1996)','unsupported model','']
 routers=[dict(id='node-'+str(i+1),name='Основа' if i==0 else 'Советская 30' if i==1 else 'Роутер '+str(i+1),online=i==0,group_id='',last_seen_iso='2026-11-26T00:00:00Z',permissions={'manage':True,'admin':True,'ssh':True,'owner':True},status={'model':model,'xray':'running','memory':'24000 / 48000 kB','temperature':'43.8 C'},ssh_entry_port=19001+i) for i,model in enumerate(names)]
 auth={'username':'owner','is_owner':True,'can_add_router':True}
 resources={'ok':True,'cpu':{'percent':1,'cores':1},'memory':{'percent':29.9},'disk':{'percent':40}}
 with mock.patch.object(hub,'web_push_backend_status',return_value={'ready':False}):page=hub.dashboard_html(routers,'owner',auth_bootstrap=auth,vps_resources=resources)
 out=Path('.test-output/router-model-photos')
 out.joinpath('dashboard.html').write_text(page,encoding='utf-8')
 out.joinpath('fixture.json').write_text(json.dumps({'routers':routers,'auth':auth,'resources':resources,'images':hub.HUB_ROUTER_MODEL_PHOTO_DATA}),encoding='utf-8')
`],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const fixture=JSON.parse(fs.readFileSync(path.join(output,'fixture.json'),'utf8'));
const photoChecks=JSON.parse(fs.readFileSync('vps/router-model-photos/catalog.json','utf8')).models.filter(m=>m.quality_checks?.length);
(async()=>{for(const engine of [chromium,webkit]){const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});try{
 for(const width of engine===webkit?[390]:[320,390,1366,2560]){
  const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<681,hasTouch:width<681,
   ...(engine===webkit?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'}:{})});
  await context.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{value:true});localStorage.setItem('owrtRemote:hubDesign','default');localStorage.setItem('owrtRemote:seasonEffectMode','off');});
  let broken=false;const errors=[],requests=[];
  await context.route('**/*',route=>{const u=new URL(route.request().url());requests.push(u.href);
   if(u.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(output,'dashboard.html'),'utf8')});
   if(u.pathname.startsWith('/router-model-photos/')){
    const data=fixture.images[u.pathname.split('/').pop()];
    return data&&!broken?route.fulfill({contentType:'image/webp',body:Buffer.from(data,'base64')}):route.fulfill({status:404,body:'missing photo'});
   }
   if(u.pathname==='/api/routers')return route.fulfill({json:{routers:fixture.routers}});
   if(u.pathname==='/api/auth/meta')return route.fulfill({json:{ok:true,auth:fixture.auth}});
   if(u.pathname==='/api/vps/resources')return route.fulfill({json:fixture.resources});
   if(u.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
   return route.fulfill({json:{ok:true,groups:[],notifications:[],sessions:[]}});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://hub.test/');await page.locator('.card').nth(5).waitFor();
  await page.locator('.hubNetworkToggle').click();
  assert.deepEqual(await page.evaluate(()=>[
   {status:{model:'Netis NX62'}},
   {status:{model:'FriendlyElec NanoPi R3S'}},{status:{model:'FriendlyARM NanoPi R3S'}},
   {status:{model:'QEMU Standard PC (i440FX + PIIX, 1996)'}}
  ].map(row=>HubRouterPhotos.resolve(row)?.id)),[
   'netis-nx62','friendly-nanopi-r3s','friendly-nanopi-r3s','qemu-standard-pc']);
  assert.equal(await page.evaluate(()=>HubRouterPhotos.resolve({status:{model:'NanoPi R3S Plus'}})),null,'different hardware is never substituted');
  if(width===1366){
   // Pixel checks catch the real regression: a transparent mask erasing antennas.
   for(const model of photoChecks){
    const filename=Object.keys(fixture.images).find(name=>name.startsWith(model.id+'.'));
    const failures=await page.evaluate(async({data,checks})=>{
     const img=new Image();img.src='data:image/webp;base64,'+data;await img.decode();
     const canvas=document.createElement('canvas');canvas.width=320;canvas.height=240;
     const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);const pixels=ctx.getImageData(0,0,320,240).data;
     return checks.filter(p=>{
      for(let y=p.y-p.radius;y<=p.y+p.radius;y++)for(let x=p.x-p.radius;x<=p.x+p.radius;x++)
       if(pixels[(y*320+x)*4+3]<p.min_alpha)return true;
      return false;
     }).map(p=>p.feature);
    },{data:fixture.images[filename],checks:model.quality_checks});
    assert.deepEqual(failures,[],model.id+' must preserve every reviewed antenna and housing region');
   }
  }
  const modelIds=['cudy-wbr3000uax','xiaomi-mi-router-ax3000t','netis-n6v2','asus-tuf-ax4200q','xiaomi-redmi-router-ax6s','netis-nx62','friendly-nanopi-r3s','qemu-standard-pc','',''];
  for(let i=0;i<modelIds.length;i++){
   const id='node-'+(i+1),card=page.locator('.card[data-router-id="'+id+'"]'),map=page.locator('.hubNetworkBranch[data-router-id="'+id+'"]');
   assert.equal(await card.locator('.hubRouterPhoto').getAttribute('data-photo-model'),modelIds[i]);
   assert.equal(await map.locator('.hubRouterPhoto').getAttribute('data-photo-model'),modelIds[i]);
   if(i<8){
    await card.locator('.hubRouterPhoto').scrollIntoViewIfNeeded();
    await page.waitForFunction(id=>document.querySelector('.card[data-router-id="'+id+'"] .hubRouterPhoto')?.classList.contains('has-photo'),id);
    assert.equal(await card.locator('img.hubRouterPhotoImage').getAttribute('src'),await map.locator('img.hubRouterPhotoImage').getAttribute('src'));
   }else assert.equal(await card.locator('img.hubRouterPhotoImage').count(),0);
  }
  assert.equal(await page.locator('.card[data-router-id="node-1"] .name').textContent(),'Основа');
  assert.equal(await page.locator('.hubNetworkBranch[data-router-id="node-2"] .hubNetworkBranchName').textContent(),'Советская 30');
  await page.evaluate(()=>{window.photoBeforeRefresh=document.querySelector('.card[data-router-id="node-1"] .hubRouterPhotoImage');renderRouterView();});
  assert(await page.locator('.card[data-router-id="node-1"] .hubRouterPhotoImage').evaluate(el=>el===window.photoBeforeRefresh&&el.complete&&el.naturalWidth>0),'refresh retains the already decoded image element');
  for(let update=0;update<6;update++){
   await page.evaluate(()=>renderRouterView());
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert(await page.locator('.card[data-router-id="node-1"] .hubRouterPhotoImage').evaluate(el=>el===window.photoBeforeRefresh&&el.complete&&el.naturalWidth>0),'photos stay decoded across presented refresh frames');
  }
  // WebKit does not compute authored colours for a button in a hidden form.
  const formWasHidden=await page.locator('#routerFormWrap').evaluate(el=>{const hidden=el.hidden;el.hidden=false;return hidden;});
  await page.locator('#routerForm').waitFor({state:'visible'});
  const greenComparison=await page.evaluate(()=>{
   const green=getComputedStyle(document.querySelector('.status.on')).color;
   return {green,buttons:['#routerForm button[type="submit"]','.actionToggle.primary'].map(selector=>getComputedStyle(document.querySelector(selector)).color)};
  });
  assert(greenComparison.buttons.every(color=>color===greenComparison.green),'Add and mobile action buttons share Online green: '+JSON.stringify(greenComparison));
  await page.locator('#routerFormWrap').evaluate((el,hidden)=>{el.hidden=hidden;},formWasHidden);
  assert.equal(await page.locator('.card[data-router-id="node-1"] .hubRouterPhotoImage').evaluate(el=>getComputedStyle(el).opacity),'1','refresh does not fade the photo');
  await page.mouse.move(0,0);await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(output,'dashboard-'+engine.name()+'-'+width+'.png'),fullPage:true});
  await page.locator('.card[data-router-id="node-5"]').screenshot({path:path.join(output,'ax6s-'+engine.name()+'-'+width+'.png')});
  assert(await page.evaluate(()=>!HubRouterPhotos.resolve({name:'Cudy WBR3000UAX v1',status:{model:'unsupported'}})),'never select a photo by the custom router name');
  assert(await page.evaluate(()=>!HubRouterPhotos.resolve({status:{model:'Cudy WBR3000UAX v10',board:'Cudy WBR3000UAX'}})),'unknown revision must not match another model');
  assert(await page.evaluate(()=>!HubRouterPhotos.resolve({status:{model:'Xiaomi Mi Router 4A (100M)'}})),'Fast Ethernet 4A is not the Gigabit edition');
  for(const name of ['Xiaomi Redmi Router AX6S','Xiaomi Redmi AX6S','Redmi Router AX6S','Redmi AX6S','Xiaomi Redmi Aiot AX6S','Xiaomi Redmi Router AX6S (OpenWrt U-Boot layout)'])
   assert.equal(await page.evaluate(model=>HubRouterPhotos.resolve({status:{model}})?.id,name),'xiaomi-redmi-router-ax6s',name+' resolves to the AX6S photo');
  for(const name of ['Xiaomi Redmi Router AX6','Xiaomi Redmi Router AX6000','Xiaomi Redmi Router AX6S v2'])
   assert.notEqual(await page.evaluate(model=>HubRouterPhotos.resolve({status:{model}})?.id,name),'xiaomi-redmi-router-ax6s',name+' must not borrow the AX6S photo');
  await page.evaluate(()=>{window.ROUTERS[0].status.model='ASUS RT-AX59U';renderRouterView();});
  await page.waitForFunction(()=>document.querySelector('.hubNetworkBranch[data-router-id="node-1"] .hubRouterPhoto')?.dataset.photoModel==='asus-rt-ax59u');
  assert(await page.locator('.card[data-router-id="node-1"] .hubRouterPhotoImage').evaluate(el=>el!==window.photoBeforeRefresh),'a different hardware model replaces the retained photo');
  assert.equal(await page.locator('.card[data-router-id="node-1"] .hubRouterPhoto').getAttribute('data-photo-model'),'asus-rt-ax59u');
  broken=true;
  await page.evaluate(()=>{window.ROUTERS[0].status.model='GL.iNet GL-MT6000';renderRouterView();});
  const changed=page.locator('.card[data-router-id="node-1"] .hubRouterPhoto');await changed.scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>{const photo=document.querySelector('.card[data-router-id="node-1"] .hubRouterPhoto');const img=photo?.querySelector('img');return photo&&!photo.classList.contains('has-photo')&&(!img||img.hidden);});
  assert(!(await changed.evaluate(el=>el.classList.contains('has-photo'))),'broken photo uses neutral fallback');
  await page.evaluate(()=>renderRouterView());
  assert.equal(await changed.locator('img').count(),0,'failed photo is not requested on every refresh');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal overflow');
  assert(requests.every(url=>url.startsWith('http://hub.test/')),'images never load from third-party hosts');
  assert.deepEqual(errors,[]);
  console.log('ROUTER_MODEL_PHOTOS_OK',engine.name(),width);await context.close();
 }
}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
