// Vantage dashboard integration against actual exported Hub HTML, with isolated API fixtures.
// Independently renders the current Hub implementation; no pre-generated preview is required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/vantage-dashboard');
const now=Math.floor(Date.now()/1000);
const fixtures = {
  auth:{username:'owner',is_owner:true,can_add_router:true,captcha:{mode:'digits'},social:{}},
  resources:{ok:true,cpu:{percent:12.4,cores:1},
    memory:{percent:36.8,used_bytes:395136991,total_bytes:1073741824},
    disk:{percent:41.2,used_bytes:8847632629,total_bytes:21474836480}},
  routers:Array.from({length:6},(_,index)=>({
    id:'node-'+(index+1),name:'Router '+(index+1),role:'node',group_id:'',online:index%3!==2,
    last_seen:now-5,last_seen_iso:new Date((now-5)*1000).toISOString(),ssh_entry_port:19181+index,
    access_url:'/access/node-'+(index+1)+'/',ssh_url:'/ssh/node-'+(index+1)+'/',config_url:'/config/node-'+(index+1),
    notes_preview:index===0?'Проверить WAN <Дом> & SSH':'',has_notes:index===0,
    permissions:Object.fromEntries(['owner','admin','ssh','manage','notes','notes_edit','config','delete','wol'].map(key=>[key,true])),
    status:{model:'Xiaomi Mi Router AX3000T',release:'OpenWrt 25.12.5',xray:'running',ssh:'running',
      memory:'64 MB free / 234 MB',flash:'30 MB free / 60 MB',uptime:1000,load:'0.1 0.2 0.3',temperature:'44'},
    tachyon:{installed:true,online:true,version:'2.0.0-beta.17',url:'/tachyon/node-'+(index+1)+'/'}
  })),
  history:{from:now-86400,to:now,interval:60,gap_after:90,
    points:Array.from({length:1440},(_,minute)=>[now-(1440-minute)*60,minute<720?3:4,6])}
};
fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'fixtures.json'),JSON.stringify(fixtures));
execFileSync(process.env.TEST_PYTHON || 'python',['-c',String.raw`
import importlib.util, json, os, sys, tempfile
from pathlib import Path
from unittest import mock
fixtures = json.loads(Path('.test-output/vantage-dashboard/fixtures.json').read_text(encoding='utf-8'))
with tempfile.TemporaryDirectory(prefix='vantage-dashboard-preview-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR'] = state
    spec = importlib.util.spec_from_file_location('vantage_dashboard_preview', 'vps/owrt-remote-hub.py')
    hub = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = hub
    spec.loader.exec_module(hub)
    with mock.patch.object(hub, 'load_auth', return_value={'username':'owner','captcha':{'mode':'digits'}}), mock.patch.object(hub, 'web_push_backend_status', return_value={'ready':False}):
        rendered = hub.dashboard_html(fixtures['routers'], 'owner', auth_bootstrap=fixtures['auth'], vps_resources=fixtures['resources'], availability=fixtures['history'])
    Path('.test-output/vantage-dashboard/owner.html').write_text(rendered, encoding='utf-8')
`],{cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const source = fs.readFileSync(path.join(output,'owner.html'),'utf8');
assert(source.includes('function initHubVantageDashboard()'), 'actual Hub must include dashboard integration');
const clone = value => JSON.parse(JSON.stringify(value));
const rowsFor = count => Array.from({length:count},(_,index)=>{
  const row=clone(fixtures.routers[index%fixtures.routers.length]);
  row.id='node-'+(index+1);row.name=index===0?'Роутер <Дом> & центр':'Площадка '+(index+1);
  row.group_id=index%2?'remote':'home';row.online=index%3!==2;
  row.status.xray=index%4===0?'stopped':'running';
  return row;
});
const sameIds = async page => {
  const ids=await page.evaluate(()=>({
    cards:Array.from(document.querySelectorAll('.routerCardSlot .card[data-router-id]')).map(el=>el.dataset.routerId).sort(),
    map:Array.from(document.querySelectorAll('.hubNetworkBranch')).map(el=>el.dataset.routerId).sort(),
    expected:sortHubRouterList(filteredRouters(window.ROUTERS)).slice(0,document.querySelector('.hubNetwork').dataset.expanded==='true'?Infinity:7).map(row=>String(row.id)).sort(),
    allowed:filteredRouters(window.ROUTERS).map(row=>String(row.id)).sort()
  }));
  assert.deepEqual(ids.map,ids.expected,'map follows current list order and expansion');
  assert.deepEqual(ids.cards,ids.allowed,'cards use only permitted filtered rows');
  return ids;
};
const noOverflow = async(page,label)=>{
  const info=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
    excess:Array.from(document.querySelectorAll('.hubNetwork,.hubKpiRow,.hubExtraStats,.routerQuickMetrics,.hubShellTopbar,.hubRoutersHeading,.routerCardSlot'))
      .map(el=>({class:el.className,left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right}))
      .filter(el=>el.left < -1 || el.right>innerWidth+1)}));
  assert(info.scroll<=info.width+1,label+' horizontal overflow '+JSON.stringify(info));
};
const cardBodyFor=(page,id)=>page.locator('.card[data-router-id="'+id+'"] .cardBody');

(async()=>{
  const browser=await chromium.launch({headless:true,
    ...(process.env.OWRT_TEST_BROWSER_BIN?{executablePath:process.env.OWRT_TEST_BROWSER_BIN}:{channel:'chrome'})});
  let count=0;
  try {
    for(const width of [320,390,844,1366,1920,2560]) {
      for(const total of [0,1,6,26]) {
        const routers=rowsFor(total),online=routers.filter(row=>row.online).length;
        const context=await browser.newContext({viewport:{width,height:width===844?390:1000},
          isMobile:width<900,hasTouch:width<900});
        const page=await context.newPage(),errors=[],requests=[];
        page.setDefaultTimeout(8000);page.on('pageerror',error=>errors.push(error.message));
        let resources=clone(fixtures.resources),createMode='error',creates=0;
        await context.addInitScript(()=>{
          localStorage.setItem('owrtRemote:hubDesign','default');
          localStorage.setItem('owrtRemote:seasonEffectMode','off');
        });
        await context.route('**/*',async route=>{
          const request=route.request(),url=new URL(request.url());
          requests.push({path:url.pathname,time:Date.now()});
          if(url.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',
            body:source.replace(/^window\.ROUTERS = .*;$/m,'window.ROUTERS = '+JSON.stringify(routers)+';')});
          if(url.pathname==='/api/routers')return route.fulfill({json:{routers,...(url.searchParams.get('availability')==='1'?{availability:fixtures.history}:{})}});
          if(url.pathname==='/api/router-groups')return route.fulfill({json:{groups:[{id:'home',name:'Дом',router_count:Math.ceil(total/2)},{id:'remote',name:'Удалённые',router_count:Math.floor(total/2)}]}});
          if(url.pathname==='/api/auth/meta')return route.fulfill({json:{ok:true,auth:fixtures.auth}});
          if(url.pathname==='/api/vps/resources')return route.fulfill({json:resources});
          if(url.pathname==='/api/router'&&request.method()==='POST'){
            creates++;
            if(createMode==='error')return route.fulfill({status:409,body:'Не удалось создать роутер'});
            if(createMode==='offline')return route.abort();
            const body=new URLSearchParams(request.postData());
            const router={...clone(fixtures.routers[0]),id:body.get('id'),name:body.get('name'),group_id:'',online:false};
            routers.push(router);return route.fulfill({status:201,json:{ok:true,router,auth:fixtures.auth}});
          }
          if(url.pathname==='/api/hub-update')return route.fulfill({json:{ok:true,checked:true,available:false,current_version:'v125',remote_version:'v125',current_revision:'fixture',remote_revision:'fixture'}});
          if(url.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
          if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,notifications:[],sessions:[],auth:fixtures.auth,groups:[]}});
          return route.fulfill({contentType:'text/plain',body:'isolated fixture'});
        });
        try {
          await page.goto('http://hub.test/',{waitUntil:'domcontentloaded'});
          await page.waitForFunction(expected=>document.querySelector('.hubNetwork')?.dataset.routerCount===String(expected),total);
          assert.equal(await page.locator('.hubNetwork').getAttribute('data-online-count'),String(online));
          assert.equal(await page.locator('.hubNetwork').getAttribute('data-offline-count'),String(total-online));
          assert.equal(await page.locator('.hubNetworkFootHelp,.hubNetworkFootXray').count(),0);
          assert.equal(await page.locator('.hubNetworkBranches .hubNetworkBranch').count(),Math.min(7,total));
          assert.equal(await page.locator('.hubNetworkControls').isVisible(),total>7);
          assert.equal(await page.locator('.hubNetworkEmpty').isVisible(),!total);
          assert.equal(await page.locator('.hubNetworkStage').isVisible(),!!total);
          await sameIds(page);
          assert.equal(await page.locator('.hubResourceKpi [data-vps-resource="cpu"] strong').textContent(),'12,4%');
          assert.equal(await page.locator('.hubResourceKpi [data-vps-resource="memory"] strong').textContent(),'36,8%');
          assert.equal(await page.locator('.hubResourceKpi [data-vps-resource="memory"]').evaluate(el=>Number(el.style.getPropertyValue('--hub-resource-fraction'))),.368);
          assert.equal(await page.locator('#hubResourcesPanel [data-vps-resource]').count(),1,'disk banner has one metric');
          assert.equal(await page.locator('#hubResourcesPanel [data-vps-resource]').getAttribute('data-vps-resource'),'disk');
          assert.equal(await page.locator('#hubResourcesPanel [data-vps-resource-value]').textContent(),'41,2%');
          const diskPairs=await page.locator('.hubDiskSpace>div').evaluateAll(rows=>rows.map(row=>{
            const key=row.querySelector('dt').getBoundingClientRect(),value=row.querySelector('dd').getBoundingClientRect();
            return {gap:value.left-key.right,width:row.getBoundingClientRect().width};
          }));
          assert(diskPairs.every(pair=>pair.gap<=11&&pair.width<200),'Disk values remain next to their labels at width '+width);
          if(total) {
            const first=page.locator('.hubNetworkBranches .hubNetworkBranch').first();
            const firstId=await first.getAttribute('data-router-id');await first.click();
            await page.waitForFunction(()=>cardDetailsAnimations.size===0);
            assert.equal(await cardBodyFor(page,firstId).evaluate(el=>el.hidden),false,'map opens actual existing router card');
            assert.equal(await page.locator('.card[data-router-id="'+firstId+'"] .cardDetailsToggle').getAttribute('aria-expanded'),'true');
            await noOverflow(page,width+'/'+total+' open card');
            await page.locator('.card[data-router-id="'+firstId+'"] .cardDetailsToggle').click();
            await page.waitForFunction(()=>cardDetailsAnimations.size===0);
          }
          if(width===1366&&total===26) {
            const remoteToggle=page.locator('[data-router-group-toggle="remote"]');
            if(await remoteToggle.getAttribute('aria-expanded')==='true')await remoteToggle.click();
            await page.locator('.hubNetworkToggle').click();
            assert.equal(await page.locator('.hubNetworkBranch').count(),26);
            await sameIds(page);
            await page.locator('.hubNetworkBranches [data-router-id="node-26"]').click();
            await page.waitForFunction(()=>cardDetailsAnimations.size===0);
            assert.equal(await remoteToggle.getAttribute('aria-expanded'),'true','map opens collapsed group before its router');
            assert.equal(await cardBodyFor(page,'node-26').evaluate(el=>el.hidden),false,'overflow route opens real card');
            await page.locator('.card[data-router-id="node-26"] .cardDetailsToggle').click();
            await page.waitForFunction(()=>cardDetailsAnimations.size===0);
            await page.locator('.hubNetworkToggle').click();
            assert.equal(await page.locator('.hubNetworkBranch').count(),7);
          }
          if(total>=6) {
            await page.locator('#routerStatusFilter').selectOption('offline');
            await page.waitForFunction(expected=>Number(document.querySelector('.hubNetwork')?.dataset.routerCount)===expected,total-online);
            assert.equal((await sameIds(page)).map.length,Math.min(7,total-online));
            await page.locator('#routerStatusFilter').selectOption('all');
            await page.locator('#routerGroupFilter').selectOption('home');
            await page.waitForFunction(expected=>Number(document.querySelector('.hubNetwork')?.dataset.routerCount)===expected,Math.ceil(total/2));
            assert.equal((await sameIds(page)).map.length,Math.min(7,Math.ceil(total/2)));
            await page.evaluate(()=>{routerSearchQuery='node-1';renderRouterView();});
            await sameIds(page);
            await page.evaluate(()=>{routerSearchQuery='';renderRouterView();});
            await page.locator('#routerGroupFilter').selectOption('');
          }
          await noOverflow(page,width+'/'+total+' dashboard');
          if(width>=1366){
            const diskCenter=await page.locator('.hubDiskPanel').evaluate(panel=>{
              const box=panel.getBoundingClientRect(),ring=panel.querySelector('.hubDiskRing').getBoundingClientRect();
              return {panel:box.top+box.height/2,ring:ring.top+ring.height/2};
            });
            assert(Math.abs(diskCenter.panel-diskCenter.ring)<16,'Desktop disk content is vertically centred '+JSON.stringify(diskCenter));
            assert.equal(await page.locator('#hubDiskBody').evaluate(el=>getComputedStyle(el).display),'flex','Desktop disk fills the available height');
            await page.evaluate(()=>document.documentElement.dataset.hubIos='1');
            assert.notEqual(await page.locator('#hubDiskBody').evaluate(el=>getComputedStyle(el).display),'flex','Desktop-only disk layout excludes iOS');
            await page.evaluate(()=>delete document.documentElement.dataset.hubIos);
            const sizes=await page.locator('.hubOverviewPanel').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height,top:r.top};}));
            assert(Math.abs(sizes[0].width-sizes[1].width)<2,'availability and disk have equal desktop width');
            assert(Math.abs(sizes[0].height-sizes[1].height)<2,'availability and disk have equal desktop height');
            assert(Math.abs(sizes[0].top-sizes[1].top)<2,'availability and disk align on top');
          }
          assert(await page.locator('.hubVantageIntro h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))<=16,'Compact network status heading');
          assert.deepEqual(errors,[],width+'/'+total+' browser errors');
          if(width===1366&&total===6) {
            resources={...resources,cpu:{percent:24.9,cores:2},memory:{percent:62.5,used_bytes:5*1024**3,total_bytes:8*1024**3},disk:{percent:90,used_bytes:9*1024**3,total_bytes:10*1024**3,free_bytes:1024**3}};
            await page.waitForFunction(()=>document.querySelector('.hubResourceKpi [data-vps-resource="cpu"] strong').textContent==='24,9%');
            assert.equal(await page.locator('.hubResourceKpi [data-vps-resource="memory"] strong').textContent(),'62,5%');
            assert.equal(await page.locator('.hubResourceKpi [data-vps-resource="cpu"]').evaluate(el=>Number(el.style.getPropertyValue('--hub-resource-fraction'))),.249);
            assert.equal(await page.locator('#hubResourcesPanel [data-vps-resource-value]').textContent(),'90%');
            assert.equal(await page.locator('[data-vps-disk-bytes="free"]').textContent(),'1 ГиБ');
            assert.equal(await page.locator('.hubDiskBanner').getAttribute('data-disk-level'),'critical');
            await page.locator('#hubMapMotion').click();
            assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true');
            assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).animationPlayState),'paused');
            await page.reload({waitUntil:'domcontentloaded'});
            await page.waitForFunction(()=>Boolean(window.hubDashboardNetwork));
            assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true','user pause survives reload');
            assert.equal(await page.locator('#hubMapMotion').getAttribute('aria-pressed'),'true');
            await page.locator('#hubMapMotion').click();
            await page.emulateMedia({reducedMotion:'reduce'});
            await page.waitForFunction(()=>document.querySelector('.hubNetwork').dataset.paused==='true');
            assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true');
            assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).animationName),'none');
            await page.emulateMedia({reducedMotion:'no-preference'});
            await page.waitForFunction(()=>document.querySelector('.hubNetwork').dataset.paused==='false');
            await page.evaluate(()=>document.documentElement.dataset.hubDesign='lite');
            assert.equal(await page.locator('.hubNetworkPulse').first().evaluate(el=>getComputedStyle(el).animationName),'none','lite stops diagram animation');
            await page.evaluate(()=>document.documentElement.dataset.hubDesign='default');
            await page.evaluate(()=>{window.dispatchEvent(new Event('pagehide'));});
            assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true','pagehide stops map motion');
            await page.evaluate(()=>{window.dispatchEvent(new Event('pageshow'));});
            const apiPaths=new Set(requests.filter(row=>row.path.startsWith('/api/')).map(row=>row.path));
            assert.deepEqual([...apiPaths].filter(api=>!['/api/routers','/api/router-groups','/api/auth/meta','/api/vps/resources','/api/hub-update','/api/notifications/wait','/api/push/status'].includes(api)),[],'map adds no polling endpoint');
          }
          if(total===6&&(width===390||width===1920)) {
            await page.mouse.move(0,0);
            await page.evaluate(async()=>{
              const groupIds=Array.from(document.querySelectorAll('[data-router-group-toggle]')).map(el=>el.dataset.routerGroupToggle);
              for(const id of groupIds) {
                const toggle=Array.from(document.querySelectorAll('[data-router-group-toggle]')).find(el=>el.dataset.routerGroupToggle===id);
                if(toggle?.getAttribute('aria-expanded')==='false')toggle.click();
              }
              if(innerWidth>=1100)document.querySelectorAll('#hubSidebar .hubNavSection').forEach(section=>section.open=true);
              window.scrollTo(0,0);
              await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            });
            await page.screenshot({path:path.join(output,'dashboard-'+width+'.png'),fullPage:true});
          }
          if(total===6&&(width===390||width===1366)){
            const openSection=async id=>{
              if(width===390&&!await page.locator('#hubSidebar').evaluate(el=>el.classList.contains('is-open')))await page.locator('#hubSidebarMenuButton').click();
              const section=page.locator(id);if(!await section.evaluate(el=>el.open))await section.locator('summary').click();
            };
            await openSection('#hubSettingsSection');
            await page.locator('[data-hub-action="stats"]').click();
            assert(await page.locator('#routerStats').isHidden());
            assert.equal(await page.evaluate(()=>localStorage.getItem('owrtRemote:routerStatsVisible')),'0');
            await page.reload();await page.waitForFunction(()=>Boolean(window.hubDashboardNetwork));
            assert(await page.locator('.hubKpiRow').isHidden(),'hidden choice survives reload');
            await page.setViewportSize({width:width===390?1100:600,height:900});
            assert(await page.locator('#routerStats').isHidden(),'resize cannot force statistics open');
            await page.setViewportSize({width,height:1000});
            await openSection('#hubSettingsSection');
            assert.equal(await page.locator('[data-hub-action="stats"] span').textContent(),'Открыть статистику');
            await page.locator('[data-hub-action="stats"]').click();
            await page.reload();await page.waitForFunction(()=>Boolean(window.hubDashboardNetwork));
            assert(await page.locator('.hubKpiRow').isVisible(),'visible choice survives reload');
            if(width===1366){
              await openSection('#hubManagementSection');await page.locator('[data-hub-action="add"]').click();
              const form=page.locator('#routerForm');await form.locator('[name="id"]').fill('node-new');
              await form.locator('[name="name"]').fill('Новый роутер');await form.locator('[name="entry_port"]').fill('18999');
              const navigations=()=>requests.filter(row=>row.path==='/').length;
              let before=navigations();await form.locator('[type="submit"]').click();
              await page.waitForFunction(()=>!document.querySelector('#routerMsg').hidden);
              assert.equal(navigations(),before,'rejected creation cannot reload');assert.equal(await form.locator('[name="name"]').inputValue(),'Новый роутер');
              createMode='offline';await form.locator('[type="submit"]').click();
              await page.waitForFunction(()=>document.querySelector('#routerMsg').textContent.includes('Не удалось добавить роутер'));
              assert.equal(navigations(),before,'network failure cannot reload');assert(!await form.locator('[type="submit"]').isDisabled());
              createMode='ok';await form.locator('[type="submit"]').click();
              await page.waitForFunction(()=>window.ROUTERS?.some(r=>r.id==='node-new'));
              assert.equal(navigations(),before+1,'successful creation reloads once');assert.equal(creates,3);
            }
          }
          count++;console.log('VANTAGE_DASHBOARD_OK',width,total);
        } finally {await context.close();}
      }
    }
    const mapSource=fs.readFileSync(path.resolve('vps/dashboard-network.js'),'utf8');
    assert(!/\b(?:fetch|setTimeout|setInterval|XMLHttpRequest)\s*\(/.test(mapSource),'map creates no polling loop');
    console.log('VANTAGE_DASHBOARD_INTEGRATION_OK',count);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
