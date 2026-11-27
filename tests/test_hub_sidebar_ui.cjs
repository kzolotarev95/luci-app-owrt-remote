// Actual Hub HTML/JS with fixture APIs. No router or VPS is contacted.
// NODE_PATH must contain Playwright; Chrome is used unless OWRT_TEST_BROWSER_BIN is set.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/hub-sidebar');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python', ['-c', String.raw`
import importlib.util, json, os, sys, tempfile, time
from pathlib import Path
from unittest import mock
out = Path('.test-output/hub-sidebar')
with tempfile.TemporaryDirectory(prefix='sidebar-ui-preview-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR'] = state
    spec = importlib.util.spec_from_file_location('sidebar_ui_preview', 'vps/owrt-remote-hub.py')
    hub = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = hub
    spec.loader.exec_module(hub)
    resources = {'ok':True, 'cpu':{'percent':12.4,'cores':1},
                 'memory':{'percent':36.8,'used_bytes':395136991,'total_bytes':1073741824},
                 'disk':{'percent':41.2,'used_bytes':8847632629,'total_bytes':21474836480}}
    routers = [{
        'id':'node-'+str(i), 'name':('Советская 30' if i==1 else 'Router '+str(i)),
        'role':'node', 'group_id':'', 'online':i not in (3,4), 'last_seen':1795320000,
        'notes_preview':'Проверить WAN <Дом> & SSH' if i == 1 else '', 'has_notes':i == 1,
        'last_seen_iso':'2026-11-22T00:00:00Z', 'ssh_entry_port':19180+i,
        'access_url':'/access/node-'+str(i)+'/', 'ssh_url':'/ssh/node-'+str(i)+'/',
        'config_url':'/config/node-'+str(i),
        'permissions':{p:True for p in ('owner','admin','ssh','manage','notes','notes_edit','config','delete','wol')},
        'status':{'model':'Xiaomi Mi Router AX3000T (OpenWrt U-Boot layout)',
                  'release':'OpenWrt 25.12.5', 'xray':'running' if i!=4 else 'stopped',
                  'ssh':'running', 'memory':'64 MB free / 234 MB', 'flash':'30 MB free / 60 MB',
                  'uptime':1000, 'load':'0.1 0.2 0.3', 'temperature':'44'},
        'tachyon':{'installed':True,'online':i!=4,'version':'2.0.0-beta.17','url':'/tachyon/node-'+str(i)+'/'}
    } for i in range(1,7)]
    auth = {'username':'owner','is_owner':True,'can_add_router':True,'captcha':{'mode':'digits'},'social':{}}
    def availability(rows):
        to = int(time.time())
        online = sum(bool(row['online']) for row in rows)
        return {'from':to-86400,'to':to,'interval':60,'gap_after':90,'points':[
            [to-(1440-minute)*60,max(0,online-(minute//240)%2),len(rows)]
            for minute in range(1440) if not 700 <= minute <= 710]}
    with mock.patch.object(hub, 'load_auth', return_value={'username':'owner','captcha':{'mode':'digits'}}), mock.patch.object(hub, 'web_push_backend_status', return_value={'ready':False}):
        out.joinpath('owner.html').write_text(hub.dashboard_html(routers,'owner',auth_bootstrap=auth,vps_resources=resources,availability=availability(routers)),encoding='utf-8')
        delegated = [{**r,'permissions':{**r['permissions'],'owner':False,'delete':False,'manage':False,'config':False,'wol':False}} for r in routers[:2]]
        viewer = {**auth,'username':'viewer','is_owner':False,'can_add_router':False,'assigned_routers':[{'id':r['id'],'name':r['name'],'flags':['A','B']} for r in delegated]}
        out.joinpath('delegated.html').write_text(hub.dashboard_html(delegated,'viewer',auth_bootstrap=viewer,vps_resources=resources,availability=availability(delegated)),encoding='utf-8')
        manager = {**viewer,'username':'manager','can_add_router':True}
        out.joinpath('manager.html').write_text(hub.dashboard_html(delegated,'manager',auth_bootstrap=manager,vps_resources=resources,availability=availability(delegated)),encoding='utf-8')
    out.joinpath('fixtures.json').write_text(json.dumps({'routers':routers,'delegated':delegated,'resources':resources,'auth':auth,'viewer':viewer,'manager':manager,'history':availability(routers),'delegated_history':availability(delegated)},ensure_ascii=False),encoding='utf-8')
print('Sidebar preview fixtures exported')
`], {cwd:path.resolve(__dirname, '..'), env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const fixtures = JSON.parse(fs.readFileSync(path.join(output,'fixtures.json'),'utf8'));
const noOverflow = async (page, label) => assert(await page.evaluate(() =>
  document.documentElement.scrollWidth <= innerWidth + 1), label+' has horizontal overflow');
const settled = page => page.waitForFunction(() => cardDetailsAnimations.size === 0);

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.OWRT_TEST_BROWSER_BIN ? {executablePath:process.env.OWRT_TEST_BROWSER_BIN} : {channel:'chrome'})});
  let count=0;
  try {
    for (const scenario of [
      {name:'desktop-owner',width:1366,height:960,profile:'owner'},
      {name:'portrait-owner',width:390,height:844,mobile:true,profile:'owner'},
      {name:'landscape-owner',width:844,height:390,mobile:true,profile:'owner'},
      {name:'narrow-owner',width:320,height:700,mobile:true,profile:'owner'},
      {name:'desktop-delegated',width:1366,height:960,profile:'delegated'},
      {name:'portrait-delegated',width:390,height:844,mobile:true,profile:'delegated'},
      {name:'desktop-manager',width:1366,height:960,profile:'manager'},
    ]) {
      const owner=scenario.profile==='owner';
      const canAdd=owner || scenario.profile==='manager';
      let routers=JSON.parse(JSON.stringify(owner?fixtures.routers:fixtures.delegated));
      const auth=owner?fixtures.auth:scenario.profile==='manager'?fixtures.manager:fixtures.viewer;
      const context=await browser.newContext({viewport:{width:scenario.width,height:scenario.height},
        hasTouch:!!scenario.mobile,isMobile:!!scenario.mobile});
      let page;
      try {
        await context.addInitScript(() => {
          localStorage.setItem('owrtRemote:hubDesign','default');
          localStorage.setItem('owrtRemote:seasonEffectMode','off');
        });
        const apiCalls=[];
        await context.route('**/*', async route => {
          const request=route.request(),u=new URL(request.url());
          if (u.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(output,scenario.profile+'.html'),'utf8')});
          if (u.pathname.startsWith('/api/')) apiCalls.push({path:u.pathname,query:u.search,method:request.method()});
          if (u.pathname==='/api/routers') return route.fulfill({json:{routers,...(u.searchParams.get('availability')==='1'?{availability:owner?fixtures.history:fixtures.delegated_history}:{})}});
          if (u.pathname==='/api/router/node-1/delete'&&request.method()==='POST'){
            routers=routers.filter(row=>row.id!=='node-1');
            return route.fulfill({json:{ok:true,deleted:true}});
          }
          if (u.pathname==='/api/router-groups') return route.fulfill({json:{groups:[{id:'home',name:'Дом',router_count:2}]}});
          if (u.pathname==='/api/auth/meta') return route.fulfill({json:{ok:true,auth}});
          if (u.pathname==='/api/vps/resources') return route.fulfill({json:fixtures.resources});
          if (u.pathname==='/api/hub-update') return route.fulfill({json:owner?{ok:true,checked:true,available:false,current_version:'v125',remote_version:'v125',current_revision:'fixture',remote_revision:'fixture'}:{ok:false,error:'owner required'},status:owner?200:403});
          if (u.pathname==='/api/notifications/wait') return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
          if (u.pathname.startsWith('/api/')) return route.fulfill({json:{ok:true,notifications:[],sessions:[],auth,groups:[]}});
          return route.fulfill({contentType:'text/plain',body:'Fixture target '+u.pathname});
        });
        page=await context.newPage();page.setDefaultTimeout(7000);
        const errors=[];page.on('pageerror',error=>errors.push(error.message));
        await page.goto('http://hub.test/',{waitUntil:'domcontentloaded'});
        await page.locator('.routerCardSlot').nth(routers.length-1).waitFor();
        const notedCard=page.locator('.card[data-router-id="node-1"]');
        assert.equal(await notedCard.getAttribute('title'),null,'no delayed native title tooltip');
        for(const selector of ['.cardTop>.status','.nameRow>.name','.cardDetailsToggle',...(owner?['.routerGroupPickerToggle']:[])]){
          await notedCard.locator(selector).hover();
          assert.equal(await page.locator('#routerCardTooltip').innerText(),'node-1','header control hover shows only router ID');
          assert.equal(await page.locator('#routerCardTooltip').isVisible(),true);
        }
        await notedCard.locator('.cardNotesPreview').hover();
        assert.equal(await page.locator('#routerCardTooltip').isVisible(),false,'notes do not trigger router ID');
        await notedCard.locator('.cardDetailsToggle').click();
        await notedCard.locator('.cardBody .metric').first().hover();
        assert.equal(await page.locator('#routerCardTooltip').isVisible(),false,'expanded metrics do not trigger router ID');
        await notedCard.locator('.nameRow>.name').hover();
        assert.equal(await page.locator('#routerCardTooltip').isVisible(),true,'header still shows ID with details expanded');
        await notedCard.locator('.cardBody .metric').first().hover();
        assert.equal(await page.locator('#routerCardTooltip').isVisible(),false,'moving within the expanded card hides ID');
        await notedCard.locator('.cardDetailsToggle').click();
        assert.equal(await notedCard.evaluate(el=>getComputedStyle(el).outlineStyle),'none','hover does not highlight card');
        await page.mouse.move(0,0);
        assert.equal(await page.locator('#routerCardTooltip').isVisible(),false,'tooltip disappears after leaving card');
        assert(await notedCard.locator(':scope>.cardNotesPreview').isVisible(),'notes visible with details collapsed');
        assert.equal(await notedCard.locator('.cardNotesPreview').innerText(),'Заметки: Проверить WAN <Дом> & SSH');
        assert.equal(await notedCard.locator('.cardBody .notesPreview').count(),0,'notes are outside hidden details');
        if(owner){
          const noteBox=await notedCard.locator('.cardNotesPreview').boundingBox();
          const groupBox=await notedCard.locator('.routerCardGroup').boundingBox();
          assert(noteBox.y+noteBox.height<=groupBox.y+1,'notes sit above group controls');
        }
        const sidebar=page.locator('#hubSidebar');
        const menu=page.locator('#hubSidebarMenuButton');
        const dialog=page.locator('#hubShellDialog');
        const nav=action=>sidebar.locator('[data-hub-action="'+action+'"]').first();
        const openMenu=async()=>{
          if (scenario.mobile && !await sidebar.evaluate(el=>el.classList.contains('is-open'))) {
            await menu.click();
            await page.waitForFunction(()=>{
              const sidebar=document.querySelector('#hubSidebar');
              return sidebar.classList.contains('is-open') && getComputedStyle(sidebar).visibility==='visible' && Math.abs(sidebar.getBoundingClientRect().left-12)<1;
            });
          }
        };
        const useNav=async action=>{
          await openMenu();
          const section=nav(action).locator('xpath=ancestor::details[contains(@class,"hubNavSection")]');
          if(await section.count()&&!await section.evaluate(el=>el.open))await section.locator(':scope > summary').click();
          await nav(action).click();
        };
        const closeDialog=async()=>{if(await dialog.isVisible())await page.locator('#hubShellDialogClose').click();};

        assert.equal(await menu.isVisible(),!!scenario.mobile,scenario.name+' menu breakpoint');
        assert.equal(await page.locator('#hubAddRouter').evaluate(el=>!el.hidden),canAdd,'add permission follows can_add_router');
        assert.equal(await page.locator('#hubSidebar .hubNavSection[open]').count(),0,'all three sections start collapsed');
        assert.equal(await page.locator('#hubManagementSection #hubAddRouter').count(),1,'add router lives in Management');
        assert.equal(await page.locator('#hubRefreshRouters,.hubServiceStrip,#hubHeartbeatSummary').count(),0,'removed Refresh and network strip stay absent');
        assert.equal(await page.locator('.hubPageHeading').count(),0,'introductory block removed');
        assert(!(await sidebar.innerText()).includes('Агент · Heartbeat · удалённый доступ'));
        assert(await page.locator('.hubAvailabilityPanel').isVisible(),'availability chart is present on every screen');
        assert.equal(await page.locator('.hubSidebarBrand').getAttribute('aria-label'),'OWRT Hub v125');
        assert.equal(await page.title(),'OWRT Hub');
        assert((await page.locator('#hubSidebarConnection').textContent()).includes('Система работает'),'healthy connection label');
        assert.equal(await page.locator('.hubPhoneBrand').innerText(),'OWRT Hub v125');
        assert.deepEqual(await page.locator('[data-availability-hours]').allTextContents(),['6 ч','12 ч','24 ч']);
        assert.equal(await page.locator('[data-availability-hours][aria-pressed="true"]').textContent(),'24 ч');
        const periodPoints=[];
        for(const hours of [6,12,24]){
          await page.locator('[data-availability-hours="'+hours+'"]').click();
          assert.equal(await page.locator('[data-availability-hours][aria-pressed="true"]').count(),1);
          assert.equal(await page.locator('#hubAvailabilityStart').textContent(),hours+' ч назад');
          assert.equal(await page.locator('#hubAvailabilityMiddle').textContent(),hours/2+' ч назад');
          assert.equal(await page.locator('#hubAvailabilityPeriodLabel').textContent(),'История за '+hours+(hours===24?' часа':' часов'));
          const range=await page.evaluate(()=>({seconds:renderHubAvailability.seconds,start:renderHubAvailability.start,points:renderHubAvailability.points}));
          assert.equal(range.seconds,hours*3600);
          assert(range.points.every(point=>point[0]>=range.start&&point[0]<=range.start+range.seconds),'plot contains only the selected period');
          periodPoints.push(range.points.length);
          assert((await page.locator('#hubAvailabilityHint').textContent()).includes('из '+hours+' ч.'));
          const chartPlot=page.locator('#hubAvailabilityPlot');
          await chartPlot.scrollIntoViewIfNeeded();
          const bounds=await chartPlot.boundingBox();
          const middle=range.start+range.seconds/2;
          const nearest=range.points.reduce((a,b)=>Math.abs(a[0]-middle)<=Math.abs(b[0]-middle)?a:b);
          const stamp=await page.evaluate(at=>new Date(at*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}),nearest[0]);
          await page.mouse.move(bounds.x+bounds.width/2,bounds.y+30);
          assert((await page.locator('#hubAvailabilityTooltip').textContent()).startsWith(stamp),'pointer inspects the midpoint of the selected period');
          await page.mouse.move(0,0);
        }
        assert(periodPoints[0]<periodPoints[1]&&periodPoints[1]<periodPoints[2],'shorter periods filter real measurements');
        const rangeLayout=await page.locator('.hubAvailabilityPanel .hubOverviewHead').evaluate(head=>{
          const title=head.querySelector('h2').getBoundingClientRect(),group=head.querySelector('.hubChartRange').getBoundingClientRect(),panel=head.closest('article').getBoundingClientRect();
          return {overlap:title.right>group.left+1,inside:group.right<=panel.right-5};
        });
        assert(!rangeLayout.overlap&&rangeLayout.inside,'period control fits beside the title');
        assert.equal(await page.locator('#hubAvailabilityOnline').textContent(),String(routers.filter(router=>router.online).length));
        const plot=page.locator('#hubAvailabilityPlot');
        await plot.focus();
        await page.keyboard.press('Home');
        assert((await page.locator('#hubAvailabilityTooltip').textContent()).includes('Онлайн:'),'keyboard shows recorded counts');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#hubAvailabilityTooltip').isVisible(),false);
        const plotBox=await plot.boundingBox();
        await page.mouse.move(plotBox.x+plotBox.width*597/600,plotBox.y+30);
        assert((await page.locator('#hubAvailabilityTooltip').textContent()).includes('Онлайн:'),'latest point can be inspected with a pointer');
        await page.mouse.move(0,0);
        await plot.evaluate(node=>node.blur());
        await page.locator('.hubAvailabilityPanel').screenshot({path:path.join(output,'history-'+scenario.name+'.png')});
        if(scenario.mobile){
          const stats=await page.locator('#routerStats').boundingBox();
          const availability=await page.locator('.hubAvailabilityPanel').boundingBox();
          const resources=await page.locator('.hubResourcesPanel').boundingBox();
          assert(availability.y-stats.y-stats.height>=13,'availability has space after router statistics');
          assert(resources.y-availability.y-availability.height>=13,'resources have space after availability');
          const labelGap=await page.locator('.hubResourcesPanel .vpsResource').evaluateAll(items=>items.map(item=>item.querySelector('strong').getBoundingClientRect().top-item.querySelector('span').getBoundingClientRect().bottom));
          assert(labelGap.every(gap=>gap>=-1&&gap<=1),'resource numbers sit just below their labels: '+JSON.stringify(labelGap));
        }
        await noOverflow(page,scenario.name+' initial');
        if (scenario.mobile) {
          const brand=await page.locator('.hubPhoneBrand').boundingBox();
          assert(brand.width>=80 && brand.height<=32,'mobile title remains readable next to update: '+JSON.stringify(brand));
          const update=await page.locator('#hubShellUpdateHost [data-hub-update]').boundingBox();
          if(update)assert(update.x>=brand.x+brand.width&&Math.abs(update.y+update.height/2-brand.y-brand.height/2)<2,'mobile update shares the title row');
        }
        assert.equal(await page.locator('#hubShellTopbar #hubShellSearchHost').count(),0,'search belongs to the router list');
        const listControls=await page.locator('.hubRouterFilters').evaluate(node=>Array.from(node.children).filter(child=>getComputedStyle(child).display!=='none').map(child=>child.id||child.className));
        assert.deepEqual(listControls,owner?['hubStatusControl','hubShellSearchHost','hubGroupFilterHost']:['hubStatusControl','hubShellSearchHost'],'router list controls retain their order for each account');
        const statusBox=await page.locator('#routerStatusFilter').boundingBox();
        const searchBox=await page.locator(scenario.mobile?'#mobileRouterSearchToggle':'#routerSearchToggle').boundingBox();
        assert(searchBox.x>=statusBox.x+statusBox.width-1&&Math.abs(searchBox.y-statusBox.y)<2,'search follows status on the same row');
        if(owner){
          const groupBox=await page.locator('#hubGroupFilterHost select').boundingBox();
          assert(groupBox.x>=searchBox.x+searchBox.width-1&&Math.abs(groupBox.y-searchBox.y)<2,'groups follow search on the same row');
        }
        if (scenario.mobile) {
          await openMenu();assert(await page.locator('#hubSidebarBackdrop').isVisible());
          await page.keyboard.press('Escape');
          await page.waitForFunction(()=>!document.querySelector('#hubSidebar').classList.contains('is-open'));
          assert.equal(await menu.getAttribute('aria-expanded'),'false');
          await openMenu();
          await page.locator('#hubSidebarBackdrop').click({position:{x:scenario.width-4,y:30}});
          await page.waitForFunction(()=>!document.querySelector('#hubSidebar').classList.contains('is-open'));
          await openMenu();
        }
        await openMenu();
        const compactBounds=await sidebar.boundingBox();
        assert(compactBounds.height<=Math.min(480,scenario.height-(scenario.mobile?24:32)),'closed sections use a compact menu height: '+JSON.stringify(compactBounds));
        const workspaceSection=page.locator('#hubWorkspaceSection');
        await workspaceSection.locator('summary').click();
        assert(await workspaceSection.evaluate(el=>el.open),'section opens on click');
        await workspaceSection.locator('summary').click();
        assert(!await workspaceSection.evaluate(el=>el.open),'section closes on click');
        await workspaceSection.locator('summary').focus();await page.keyboard.press('Enter');
        assert(await workspaceSection.evaluate(el=>el.open),'section opens with keyboard');
        for(const id of ['hubManagementSection','hubSettingsSection'])await page.locator('#'+id+'>summary').click();
        assert.equal(await page.locator('#routerStatusFilter').count(),1,'status filter available to all accounts');
        assert.equal(await page.locator('#hubAddRouter>span').isVisible(),canAdd,'add keeps its label and permission in Management');
        for (const action of ['groups','vps-xray','update']) assert.equal(await nav(action).isVisible(),owner,'owner-only nav '+action);
        for (const href of ['/vps-terminal/','/router-updates/']) {
          const link=sidebar.locator('a[href="'+href+'"]');
          assert.equal(await link.isVisible(),owner,'owner-only '+href);
          if(owner){assert.equal(await link.getAttribute('target'),'_blank');assert.equal(await link.getAttribute('rel'),'noopener noreferrer');}
        }
        assert.equal(await sidebar.locator('a[href="/donate/"]').getAttribute('target'),'_blank');
        assert.equal(await sidebar.locator('a[href="https://t.me/+LZDsQJhUfcNhYWEy"]').getAttribute('target'),'_blank');
        if (scenario.mobile) await page.keyboard.press('Escape');

        const searchInput=page.locator(scenario.mobile?'#mobileRouterSearchInput':'#routerSearchInput');
        await page.locator(scenario.mobile?'#mobileRouterSearchToggle':'#routerSearchToggle').click();
        await searchInput.fill('Советская');
        await page.waitForFunction(()=>document.querySelectorAll('.routerCardSlot').length===1);
        assert((await page.locator('.card .name').textContent()).includes('Советская'));
        await searchInput.fill('');
        await page.waitForFunction(expected=>document.querySelectorAll('.routerCardSlot').length===expected,routers.length);
        await page.keyboard.press('Escape');
        if(owner){
          await page.locator('[data-offline-toggle]').click();
          const popup=await page.locator('[data-offline-popover]').boundingBox();
          const parent=await page.locator('[data-offline-card]').boundingBox();
          assert(Math.abs(popup.x+popup.width/2-parent.x-parent.width/2)<1,'offline list is centered on its own statistic card');
          assert(Math.abs(popup.width-parent.width)<1,'offline list uses the statistic card width');
          assert(popup.x>=11&&popup.x+popup.width<=scenario.width-11,'offline list remains inside the viewport: '+JSON.stringify(popup));
          assert.equal(await page.evaluate(()=>scrollX),0,'offline details do not scroll the page sideways');
          await page.locator('[data-offline-toggle]').click();
        }

        await useNav('design');
        const designPanel=page.locator(scenario.mobile?'#mobileSeasonPanel':'#seasonPanel');
        await designPanel.locator('[data-hub-design="winter"]').click();
        assert.equal(await page.evaluate(()=>document.documentElement.dataset.hubDesign),'winter');
        await noOverflow(page,scenario.name+' winter');
        await designPanel.locator('[data-hub-design="lite"]').click();
        assert.equal(await page.evaluate(()=>document.documentElement.dataset.hubDesign),'lite');
        assert.equal(await page.evaluate(()=>seasonalFxFrame),0);
        await designPanel.locator('[data-hub-design="default"]').click();
        await page.keyboard.press('Escape');

        await useNav('access');
        assert(await page.locator('#authMenu').isVisible(),'access control remains usable');
        const accessClose=await page.locator('#authMenuClose').boundingBox();
        if(!owner)assert.equal(await page.locator('#routerUpdatesGroup').isVisible(),false,'delegated cannot see global update controls');
        await page.locator('#authMenuClose').click();
        assert.equal(await page.locator('#authMenu').isVisible(),false);
        if(owner){
          await useNav('vps-xray');
          const xrayClose=await page.locator('#hubShellDialogClose').boundingBox();
          assert(Math.abs(xrayClose.height-accessClose.height)<=1,'Xray Close matches Access Close height');
          assert(Math.abs(xrayClose.width-accessClose.width)<=3,'Xray Close matches Access Close width');
          const shape=await page.locator('#hubShellDialogClose').evaluate(el=>({radius:getComputedStyle(el).borderRadius,height:el.offsetHeight,width:el.offsetWidth}));
          assert(shape.radius==='999px'&&shape.height===28&&shape.width<90,'compact pill Close button: '+JSON.stringify(shape));
          await closeDialog();
        }

        assert.equal(await page.locator('[data-hub-action="logs"],[data-global-logs-link]').count(),0,'retired journal has no UI controls');
        if(owner){
          await useNav('groups');
          assert(await page.locator('#routerGroupsPanel').isVisible(),'group management is reachable');
          await page.locator('#hubGroupFilterHost select').selectOption('');
        }
        for(const state of ['online','offline']){
          await page.locator('#routerStatusFilter').selectOption(state);
          const expected=routers.filter(router=>Boolean(router.online)===(state==='online')).length;
          assert.equal(await page.locator('.routerCardSlot').count(),expected,'complete status list independent of groups');
          assert.equal(await page.locator('.routerGroupSection').count(),0,'status list has no collapsed group sections');
          assert.equal(await page.locator('#hubVisibleCount').textContent(),expected+' из '+routers.length);
          if(!expected)assert((await page.locator('.empty').innerText()).includes('фильтру'),'empty filter does not ask to add a first router');
        }
        await page.locator('#routerStatusFilter').selectOption('all');
        if(canAdd){
          await useNav('add');
          assert(await page.locator('#routerFormWrap').isVisible(),'add form opened');
          assert.equal(await page.locator('#routerForm input[name="id"]').count(),1,'original form reused');
        }
        await page.locator('[data-card-toggle]').first().click();await settled(page);
        assert.equal(await page.locator('[data-card-toggle]').first().getAttribute('aria-expanded'),'true');
        await noOverflow(page,scenario.name+' expanded');
        await page.locator('[data-card-toggle]').first().click();await settled(page);
        const beforeRefresh=apiCalls.filter(call=>call.path==='/api/routers').length;
        await page.evaluate(async()=>{
          window.HUB_AVAILABILITY_RECEIVED_AT=Date.now()-61000;
          await loadRouters();
          await loadRouters();
        });
        assert.equal(apiCalls.filter(call=>call.query==='?availability=1').length,1,'history rides one existing router poll per minute');
        await page.evaluate(()=>loadRouters());
        assert(apiCalls.filter(call=>call.path==='/api/routers').length>beforeRefresh,'existing background refresh fetches routers');
        if(!owner)assert.equal(apiCalls.filter(call=>call.path==='/api/hub-update').length,0,'delegated does not poll owner updater');
        assert.equal(apiCalls.filter(call=>call.path==='/api/xray/reload'||call.path==='/api/xray/restart').length,0,'navigation does not send service commands');
        assert.deepEqual(errors,[],scenario.name+' console errors');
        await page.screenshot({path:path.join(output,scenario.name+'.png'),fullPage:false});

        if(scenario.profile==='owner'){
          if(scenario.mobile){
            await openMenu();
            await page.setViewportSize({width:1366,height:960});
            await page.waitForFunction(()=>!mobileLayoutMq.matches);
            assert.equal(await page.locator('#hubSidebarBackdrop').isVisible(),false,'resizing to desktop clears backdrop');
            assert(!await page.locator('body').evaluate(el=>el.classList.contains('hubSidebarOpen')),'desktop body is scrollable');
          }else{
            await page.setViewportSize({width:390,height:844});
            await page.waitForFunction(()=>mobileLayoutMq.matches);
            assert(await menu.isVisible());
          }
          await noOverflow(page,scenario.name+' resized');
        }
        await page.locator('#hubWorkspaceSection>summary').evaluate(el=>el.parentElement.open=true);
        await page.locator('#hubSettingsSection>summary').evaluate(el=>el.parentElement.open=true);
        await page.waitForFunction(()=>{const s=JSON.parse(localStorage.getItem('owrtRemote:hubNavSections:v1:'+encodeURIComponent(authMeta.username))||'{}');return s.hubWorkspaceSection===true&&s.hubSettingsSection===true;});
        await page.locator('[data-availability-hours="6"]').click();
        await page.evaluate(()=>renderHubAvailability(window.ROUTERS));
        assert.equal(await page.locator('[data-availability-hours][aria-pressed="true"]').textContent(),'6 ч','live update preserves the selected period');
        await page.reload({waitUntil:'domcontentloaded'});
        assert(await page.locator('#hubWorkspaceSection').evaluate(el=>el.open),'workspace remembered after reload');
        assert(await page.locator('#hubSettingsSection').evaluate(el=>el.open),'settings remembered after reload');
        assert.equal(await page.locator('[data-availability-hours][aria-pressed="true"]').textContent(),'6 ч','availability period remembered after reload');
        assert.equal(await page.locator('#hubAvailabilityStart').textContent(),'6 ч назад');
        for(const selector of ['.hubNetworkTitle>svg','.hubAvailabilityPanel h2>svg']){
          const bounds=await page.locator(selector).boundingBox();
          assert(bounds&&bounds.width>=20&&bounds.height>=20,'heading icon is visible: '+selector);
        }
        for(const key of ['map','availability','disk']){
          const button=page.locator('[data-hub-panel-toggle="'+key+'"]');
          await button.click();
          assert.equal(await button.textContent(),'Открыть');
          assert.equal(await button.getAttribute('aria-expanded'),'false');
          const id=await button.getAttribute('aria-controls');
          assert.equal(await page.locator('#'+id).isVisible(),false,'panel body collapses: '+key);
        }
        assert.equal(await page.locator('.hubNetwork').getAttribute('data-paused'),'true','hidden map pauses motion');
        await page.evaluate(()=>{renderHubAvailability(window.ROUTERS);updateHubVantageDashboard(window.ROUTERS,filteredRouters(window.ROUTERS));});
        await page.reload({waitUntil:'domcontentloaded'});
        for(const key of ['map','availability','disk']){
          const button=page.locator('[data-hub-panel-toggle="'+key+'"]');
          assert.equal(await button.textContent(),'Открыть','collapse remembered after F5: '+key);
          assert.equal(await page.locator('#'+await button.getAttribute('aria-controls')).isVisible(),false);
          await button.click();
          assert.equal(await button.textContent(),'Скрыть');
          assert.equal(await page.locator('#'+await button.getAttribute('aria-controls')).isVisible(),true,'panel reopens: '+key);
        }
        await noOverflow(page,scenario.name+' panel toggles');
        await page.locator('.hubOverview').screenshot({path:path.join(output,'panel-controls-'+scenario.name+'.png')});
        if(scenario.name==='desktop-owner'){
          await page.evaluate(()=>{
            const id='node-1';
            wolStateByRouter.set(id,{open:false,sshPassword:'fixture-only',devices:[{mac:'old'}]});
            noteStateByRouter.set(id,{draft:'old',dirty:true});
            vpsHostDrafts.set(id,{host:'old.example',dirty:true});
            diagnosticDrafts.set(id,{old:true});saveWolPassword(id,'fixture-only');
          });
          page.once('dialog',dialog=>dialog.accept());
          await page.locator('.card[data-router-id="node-1"] [data-delete="node-1"]').evaluate(el=>el.click());
          await page.waitForFunction(()=>!window.ROUTERS.some(row=>row.id==='node-1'));
          assert(await page.evaluate(()=>[wolStateByRouter,noteStateByRouter,vpsHostDrafts,diagnosticDrafts].every(map=>!map.has('node-1'))),'delete clears card drafts');
          assert.equal(await page.evaluate(()=>loadWolPassword('node-1')),'','delete clears remembered SSH password');
          routers.unshift({...fixtures.routers[0],status:{},notes:'',notes_preview:'',has_notes:false,tachyon:{},online:false});
          await page.evaluate(()=>loadRouters());
          assert.equal(await page.locator('.card[data-router-id="node-1"] .cardNotesPreview').count(),0,'recreated node has no old note');
          assert.equal(await page.evaluate(()=>getWolState('node-1').sshPassword),'','recreated node does not inherit password');
          assert.deepEqual(errors,[],scenario.name+' delete/recreate browser errors');
        }
        count++;console.log('HUB_SIDEBAR_UI_OK',scenario.name);
      } catch(error) {
        if(page)await page.screenshot({path:path.join(output,scenario.name+'-failure.png'),fullPage:true}).catch(()=>{});
        throw new Error(scenario.name+': '+error.message,{cause:error});
      } finally {await context.close();}
    }
  } finally {await browser.close();}
  console.log('HUB_SIDEBAR_BROWSER_OK:',count,'cases; owner/delegated permissions, navigation, themes, search, mobile drawer and resize');
})().catch(error=>{console.error(error);process.exitCode=1;});
