// Render the real ash CGI with fixture state, then exercise actual browser forms.
// No SSH, router, VPS, UCI mutation or command execution is performed by this test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.test-output/router-sidebar');
fs.mkdirSync(output, {recursive:true});
const shell = process.env.OWRT_TEST_SH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles || 'C:/Program Files', 'Git/usr/bin/sh.exe') : 'sh');
const source = fs.readFileSync(path.join(root,'files/www/cgi-bin/owrt-remote'),'utf8').split('\nread_request\n')[0];
const runtime = fs.readFileSync(path.join(root,'files/www/luci-static/resources/owrt_remote_effects.js'),'utf8');
assert(source.includes('render_page()') && !source.includes('\nread_request\n'));
const routerName = 'Советская 30 <Дом> & Офис';
const draft = "uci set owrtremote.main.router_name='Дом & Офис'\n# <script>window.draftExecuted=true</script>\nowrt-remote heartbeat";

function renderPage(showDraft) {
  const mocks = String.raw`
param() { [ "$1" != key ] || printf '%s' fixture-key; }
uci_get() {
  case "$1" in
    router_id) printf '%s' node-12 ;;
    router_name) printf '%s' 'Советская 30 <Дом> & Офис' ;;
    hub_url) printf '%s' 'https://hub.example.test' ;;
    enabled|wan_direct) printf '%s' 1 ;;
    wan_interface) printf '%s' 'wan wwan' ;;
    role) printf '%s' node ;;
    heartbeat_interval) printf '%s' 30 ;;
    vps_host) printf '%s' 'hub.example.test' ;;
    vps_host_mode) printf '%s' manual ;;
    xray_bin) printf '%s' /tmp/owrt-xray/xray ;;
    xray_config) printf '%s' /etc/xray/owrt-remote-client.json ;;
    vps_port) printf '%s' 8443 ;;
    admin_port) printf '%s' 80 ;;
    ssh_port) printf '%s' 22 ;;
    *) printf '%s' "$2" ;;
  esac
}
service_enabled() { return 0; }
xray_running() { return 0; }
wan_interface_field() {
  cat <<'WAN'
<div class="field wanField"><span>WAN интерфейс</span><input type="hidden" name="wan_interface_picker" value="1"><details class="wanPicker" data-wan-picker><summary>Выбрать интерфейсы (<b data-wan-count>2</b>)</summary><div class="wanPickerList"><label class="wanChoice"><input type="checkbox" name="wan_interface" value="wan" checked>wan</label><label class="wanChoice"><input type="checkbox" name="wan_interface" value="wwan" checked>wwan</label></div></details><div class="wanPicked" data-wan-picked>wan, wwan</div></div>
WAN
}
`;
  const draftState = showDraft ? String.raw`
SHOW_XRAY_SETTINGS=1
RESULT_TITLE='Ошибка проверки <Xray>'
RESULT_BODY='Рабочая конфигурация сохранена. <script>window.resultExecuted=true</script>'
RESULT_KIND=bad
XRAY_SETTINGS_DRAFT="$(cat <<'DRAFT'
uci set owrtremote.main.router_name='Дом & Офис'
# <script>window.draftExecuted=true</script>
owrt-remote heartbeat
DRAFT
)"
` : '';
  const html = execFileSync(shell, [], {input:source+'\n'+mocks+'\n'+draftState+'\nrender_page\n',
    cwd:root, encoding:'utf8', timeout:15000, env:{...process.env,
      PATH:path.dirname(shell)+path.delimiter+process.env.PATH}});
  assert(html.includes('Content-Type: text/html; charset=utf-8'));
  assert(html.includes('id="routerSidebar"'));
  assert(!html.includes('$(h '),'all dynamic shell expressions were rendered');
  const page = html.slice(html.indexOf('<!doctype html>'));
  fs.writeFileSync(path.join(output,showDraft?'draft.html':'dashboard.html'),page);
  return page;
}
const html = renderPage(false), draftHtml = renderPage(true);
// Exercise the real authenticated GET dispatcher, not a copied donation fixture.
const keyFile=path.join(output,'web-key.fixture');
fs.writeFileSync(keyFile,'fixture-key\n');
function renderDonation(key) {
  return execFileSync(shell,[path.join(root,'files/www/cgi-bin/owrt-remote')],{
    cwd:root,encoding:'utf8',timeout:15000,env:{...process.env,
      PATH:path.dirname(shell)+path.delimiter+process.env.PATH,
      KEY_FILE:keyFile.replace(/\\/g,'/'),REQUEST_METHOD:'GET',CONTENT_TYPE:'',
      QUERY_STRING:'key='+key+'&action=donate',
      AGENT:path.join(output,'no-agent').replace(/\\/g,'/'),
      TMPDIR:output.replace(/\\/g,'/')}});
}
const donateResponse=renderDonation('fixture-key');
assert(donateResponse.includes('Content-Type: text/html; charset=utf-8'));
assert(donateResponse.includes('class="panel donateCard"'));
assert(!renderDonation('wrong-key').includes('class="panel donateCard"'),'donation route respects router authentication');
const donateHtml=donateResponse.slice(donateResponse.indexOf('<!doctype html>'));
fs.writeFileSync(path.join(output,'donate.html'),donateHtml);
const requiredFields = ['enabled','router_id','router_name','role','public_url','hub_url','hub_token',
  'heartbeat_interval','xray_bin','xray_config','vps_host','vps_host_auto','wan_direct','wan_interface_picker',
  'wan_interface','wan_dns','vps_port','vless_uuid','vless_encryption','vless_flow','reverse_tag',
  'ssh_vless_uuid','ssh_reverse_tag','admin_host','admin_port','ssh_host','ssh_port'];
const scenarios = [
  {name:'desktop-1920',width:1920,height:1080},
  {name:'desktop-2560',width:2560,height:1440},
  {name:'desktop-3840-proxy',width:3840,height:2160,proxy:true},
  {name:'portrait-390',width:390,height:844,mobile:true},
  {name:'narrow-320-proxy',width:320,height:700,mobile:true,proxy:true},
  {name:'landscape-844-proxy',width:844,height:390,mobile:true,proxy:true},
  {name:'draft-result-proxy',width:1920,height:1080,proxy:true,draft:true},
];

async function noOverflow(page,label) {
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),label+' horizontal overflow');
}

(async()=>{
  const browser=await chromium.launch({headless:true,
    ...(process.env.OWRT_TEST_BROWSER_BIN ? {executablePath:process.env.OWRT_TEST_BROWSER_BIN} : {channel:'chrome'})});
  const report=[];
  try {for (const scenario of scenarios) {
    const context=await browser.newContext({viewport:{width:scenario.width,height:scenario.height},
      hasTouch:!!scenario.mobile,isMobile:!!scenario.mobile});
    let page;
    try {
      const requests=[];
      const pagePath=(scenario.proxy?'/access/node-12':'')+'/cgi-bin/owrt-remote';
      const url='http://router.test'+pagePath+'?key=fixture-key';
      await context.addInitScript(()=>{
        localStorage.setItem('owrtRemote:hubDesign','default');
        localStorage.setItem('owrtRemote:seasonEffectMode','off');
        window.testSubmissions=[];
        document.addEventListener('submit',event=>{
          event.preventDefault();
          const data=new FormData(event.target,event.submitter);
          window.testSubmissions.push({method:event.target.method,action:event.target.getAttribute('action') || location.href,
            fields:Array.from(data.entries()),submitter:event.submitter?.value});
        },true);
      });
      await context.route('**/*',async route=>{
        const request=route.request(),u=new URL(request.url());
        requests.push({path:u.pathname,method:request.method(),action:u.searchParams.get('action')});
        if(u.pathname.endsWith('/luci-static/resources/owrt_remote_effects.js'))return route.fulfill({contentType:'text/javascript',body:runtime});
        if(u.pathname===pagePath && u.searchParams.get('action')==='donate')return route.fulfill({contentType:'text/html; charset=utf-8',body:donateHtml});
        if(u.pathname===pagePath && u.searchParams.get('action')==='logs_clear'){
          assert.equal(request.method(),'POST','log clear remains POST');
          return route.fulfill({contentType:'text/plain; charset=utf-8',body:'Логи Remote очищены.'});
        }
        if(u.pathname===pagePath)return route.fulfill({contentType:'text/html; charset=utf-8',body:scenario.draft?draftHtml:html});
        return route.abort();
      });
      page=await context.newPage();page.setDefaultTimeout(7000);
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.goto(url,{waitUntil:'domcontentloaded'});
      await page.waitForFunction(()=>hubAppearanceInitialized && seasonEffectsInitialized);
      const sidebar=page.locator('#routerSidebar'),menu=page.locator('#routerSidebarMenu');
      const workspace=page.locator('#routerWorkspace');
      const openMenu=async()=>{
        if(scenario.mobile && !await sidebar.evaluate(el=>el.classList.contains('is-open'))){
          await menu.click();
          await page.waitForFunction(()=>{
            const s=document.getElementById('routerSidebar');
            return s.classList.contains('is-open')&&Math.abs(s.getBoundingClientRect().left-12)<1;
          });
        }
      };
      const openSection=async locator=>{
        await openMenu();
        const section=locator.locator('xpath=ancestor::details[contains(@class,"routerNavSection")]');
        if(await section.count()&&!await section.evaluate(el=>el.open))await section.locator(':scope>summary').click();
      };
      const useNav=async locator=>{await openSection(locator);await locator.click();};
      const closeMenu=async()=>{
        if(scenario.mobile && await sidebar.evaluate(el=>el.classList.contains('is-open'))){
          await page.locator('#routerSidebarClose').click();
          await page.waitForFunction(()=>!document.getElementById('routerSidebar').classList.contains('is-open'));
        }
      };
      const drawerSettled=async()=>{
        if(scenario.mobile)await page.waitForFunction(()=>{
          const sidebar=document.getElementById('routerSidebar');
          return !sidebar.classList.contains('is-open')&&getComputedStyle(sidebar).visibility==='hidden';
        });
      };
      assert.equal(await menu.isVisible(),!!scenario.mobile,'menu follows mobile breakpoint');
      assert.equal(await sidebar.locator('details[open]').count(),0,'sidebar sections start collapsed');
      assert.equal(await page.title(),'OWRT Hub v124');
      assert((await page.locator('.routerBrand').textContent()).includes('OWRT Hub v124'));
      assert.equal(await page.locator('.routerPageHead h1').textContent(),routerName,'router name is safely escaped');
      for(const field of requiredFields)assert(await page.locator('#routerSettings [name="'+field+'"]').count(),'settings field retained: '+field);
      assert.equal(await page.locator('form form').count(),0,'forms are never nested');
      assert.equal(await page.locator('canvas.seasonalFx').count(),1,'one effects canvas');
      const size=await page.evaluate(()=>{
        const header=document.querySelector('.routerTopbar').getBoundingClientRect();
        const settings=document.getElementById('routerSettings').getBoundingClientRect();
        const sidebar=document.getElementById('routerSidebar').getBoundingClientRect();
        const wrap=document.querySelector('.wrap').getBoundingClientRect();
        return {leftGap:sidebar.left,wrapWidth:wrap.width,rightGap:document.documentElement.clientWidth-header.right,settingsRight:settings.right,headerRight:header.right,
          font:getComputedStyle(document.querySelector('#routerStatus h2')).fontSize,bg:getComputedStyle(document.body).backgroundColor};
      });
      if(scenario.mobile)assert(Math.abs(size.rightGap-12)<1,'mobile workspace keeps its side padding: '+JSON.stringify(size));
      else {
        assert(Math.abs(size.leftGap-size.rightGap)<1,'sidebar and workspace share equal outer margins: '+JSON.stringify(size));
        assert(size.wrapWidth<=1360.1,'desktop shell has a bounded readable width');
      }
      assert(Math.abs(size.settingsRight-size.headerRight)<1,'settings and header share the right edge');
      assert.equal(size.font,'12px','VPS typography retained');
      assert.equal(size.bg,'rgb(11, 11, 12)','default graphite palette');
      await noOverflow(page,scenario.name+' initial');
      await page.screenshot({path:path.join(output,scenario.name+'-initial.png'),fullPage:false});
      if(scenario.mobile){
        assert(await sidebar.evaluate(el=>el.inert),'closed drawer is inert');
        await openMenu();assert(await workspace.evaluate(el=>el.inert),'workspace inert while drawer is open');
        await page.screenshot({path:path.join(output,scenario.name+'-menu.png'),fullPage:false});
        await page.keyboard.press('Escape');
        await page.waitForFunction(()=>!document.getElementById('routerSidebar').classList.contains('is-open'));
        assert.equal(await menu.getAttribute('aria-expanded'),'false');
        await openMenu();
        await page.locator('#routerSidebarBackdrop').click({position:{x:scenario.width-4,y:30}});
        await page.waitForFunction(()=>!document.getElementById('routerSidebar').classList.contains('is-open'));
      }
      const countBeforeNav=requests.length;
      await useNav(sidebar.locator('[data-router-section="routerSettings"]').first());
      if(scenario.mobile)assert(!await workspace.evaluate(el=>el.inert),'settings navigation restores workspace');
      assert.equal(requests.length,countBeforeNav,'navigation alone does not execute commands');
      await useNav(sidebar.locator('[data-router-section="routerStatus"]').first());

      // An external submitter still targets the original controls form and key.
      await page.locator('.routerQuickActions [value="restart"]').click();
      let last=await page.evaluate(()=>testSubmissions[testSubmissions.length-1]);
      assert.equal(last.method,'post');assert.equal(last.submitter,'restart');
      assert.deepEqual(last.fields,[['key','fixture-key'],['action','restart']]);
      assert.equal(new URL(last.action).pathname,pagePath,'native form keeps proxy prefix');
      await useNav(sidebar.locator('#routerControlForm [value="doctor"]'));
      last=await page.evaluate(()=>testSubmissions[testSubmissions.length-1]);
      assert.deepEqual(last.fields,[['key','fixture-key'],['action','doctor']]);
      await closeMenu();
      await page.locator('#routerSettings input[name="router_name"]').fill('Новый дом');
      await page.locator('#routerSettings [value="save"]').click();
      last=await page.evaluate(()=>testSubmissions[testSubmissions.length-1]);
      assert.equal(last.submitter,'save');
      assert(last.fields.some(([key,value])=>key==='key'&&value==='fixture-key'));
      assert(last.fields.some(([key,value])=>key==='router_name'&&value==='Новый дом'));
      assert.deepEqual(last.fields.filter(([key])=>key==='wan_interface'),[['wan_interface','wan'],['wan_interface','wwan']]);

      await useNav(page.locator('#routerEffectsToggle'));
      const effects=page.locator('#routerEffectsPanel');
      assert(await effects.isVisible());
      if(scenario.mobile)assert(!await workspace.evaluate(el=>el.inert),'effects closes drawer');
      for(const design of ['winter','lite','default']){
        await effects.locator('[data-hub-design="'+design+'"]').click();
        assert.equal(await page.evaluate(()=>document.documentElement.dataset.hubDesign),design);
        if(design==='lite')assert.equal(await page.evaluate(()=>seasonalFxFrame),0,'lite mode has no effects loop');
        await noOverflow(page,scenario.name+' '+design);
      }
      await effects.locator('[data-season-mode="prism"]').click();
      assert.equal(await page.evaluate(()=>seasonEffectMode),'prism');
      await effects.locator('[data-season-mode="off"]').click();
      await page.locator('#routerEffectsClose').click();
      assert.equal(await effects.isVisible(),false);

      assert.equal(await page.locator('#globalLogsToggle,#globalLogsPanel').count(),0,'router journal removed');
      if(scenario.draft){
        assert.equal(await page.locator('[name="xray_settings_script"]').inputValue(),draft,'Xray draft survives rendering');
        assert((await page.locator('.notice.bad').textContent()).includes('<script>'));
        assert.equal(await page.evaluate(()=>Boolean(window.draftExecuted||window.resultExecuted)),false,'draft/result HTML is not executed');
        await page.locator('#routerXraySettings [value="apply_xray_settings"]').click();
        last=await page.evaluate(()=>testSubmissions[testSubmissions.length-1]);
        assert.equal(last.submitter,'apply_xray_settings');
        assert(last.fields.some(([key,value])=>key==='xray_settings_script'&&value===draft));
      }
      const support=sidebar.locator('.routerDonate');
      await openMenu();
      const [donation]=await Promise.all([context.waitForEvent('page'),support.click()]);
      await donation.waitForLoadState('domcontentloaded');
      await donation.waitForFunction(()=>hubAppearanceInitialized);
      assert.equal(new URL(donation.url()).pathname,pagePath,'donation stays on the selected router, including its proxy path');
      assert.equal(new URL(donation.url()).searchParams.get('action'),'donate');
      assert((await donation.locator('.donateMessage').innerText()).includes('Буду искренне благодарен за любой донат!'));
      assert.equal(await donation.locator('.donatePaymentBrand').count(),5,'same bank marks as VPS');
      const links=await donation.locator('.donateLinkCard').evaluateAll(items=>items.map(item=>({href:item.getAttribute('href'),rel:item.rel,target:item.target})));
      assert.deepEqual(links.map(item=>item.href),['https://donate.zks95.ugnss.ru','https://donate.zks95.ugnss.ru:8443']);
      assert(links.every(item=>item.rel==='noopener noreferrer'&&item.target==='_blank'));
      const back=new URL(await donation.locator('.donateBack').getAttribute('href'),donation.url());
      assert.equal(back.pathname,pagePath);assert.equal(back.searchParams.get('key'),'fixture-key');assert.equal(back.searchParams.has('action'),false);
      const banner=await donation.locator('.donateCard').boundingBox();
      assert(Math.abs(banner.x+banner.width/2-scenario.width/2)<1,'banner is centered');
      await noOverflow(donation,scenario.name+' donation');
      await donation.screenshot({path:path.join(output,'donate-'+scenario.name+'.png'),fullPage:false});
      await donation.close();
      await closeMenu();
      await page.evaluate(()=>scrollTo(0,0));
      await drawerSettled();
      await noOverflow(page,scenario.name+' final');
      assert.deepEqual(errors,[],scenario.name+' browser errors');
      await page.screenshot({path:path.join(output,scenario.name+'.png'),fullPage:false});
      if(!scenario.mobile){
        await page.setViewportSize({width:390,height:844});
        await page.waitForFunction(()=>document.getElementById('routerSidebar').inert);
        await noOverflow(page,scenario.name+' resize');
      }
      report.push({name:scenario.name,width:scenario.width,height:scenario.height,proxy:!!scenario.proxy,size,
        forms:await page.evaluate(()=>testSubmissions.length),requests:requests.length});
      console.log('ROUTER_SIDEBAR_OK',scenario.name);
    }catch(error){
      if(page)await page.screenshot({path:path.join(output,scenario.name+'-failure.png'),fullPage:true}).catch(()=>{});
      throw new Error(scenario.name+': '+error.message,{cause:error});
    }finally{await context.close();}
  }
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  console.log('ROUTER_SIDEBAR_BROWSER_OK:',report.length,'cases; shell rendering, local/proxy forms, mobile drawer, themes and logs lifecycle');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
