const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/hub-update');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python', ['tests/test_vps_resources.py', '--export-preview', output],
  {env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const pageHtml = fs.readFileSync(path.join(output, 'dashboard.html'), 'utf8');
function state() {
  return {ok:true,checked:true,checking:false,error:'',source:'https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main',
    current_revision:'a'.repeat(64),current_version:'v123',remote_revision:'b'.repeat(64),remote_version:'v123',source_older:false,available:true,job:{},csrf:'test-csrf'};
}
async function createFixture(browser,width,height,mobile) {
  const page = await browser.newPage({viewport:{width,height},isMobile:mobile,hasTouch:mobile});
  const fixture = {data:state(),starts:0,checks:0,views:0,failures:0};
  await page.route('**/*',async route => {
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/api/hub-update/start') {
      fixture.starts++;
      assert.equal(req.headers()['x-hub-update-csrf'],'test-csrf');
      assert.equal(req.postDataJSON().revision,'b'.repeat(64));
      fixture.data.job={id:'job-one',status:'starting',message:'Запускается обновление панели…'};
      return route.fulfill({json:fixture.data});
    }
    if(url.pathname==='/api/hub-update/check') {fixture.checks++;return route.fulfill({json:fixture.data});}
    if(url.pathname==='/api/hub-update') {
      if(fixture.failures>0) {fixture.failures--;return route.abort();}
      return route.fulfill({json:fixture.data});
    }
    if(url.pathname==='/api/vps/resources')return route.fulfill({json:{ok:true,cpu:{percent:10},memory:{percent:40},disk:{percent:60}}});
    if(url.pathname==='/api/routers')return route.fulfill({json:{routers:[]}});
    if(url.pathname==='/api/auth/meta')return route.fulfill({json:{ok:true,auth:{username:'owner',is_owner:true,can_add_router:true}}});
    if(url.pathname==='/api/notifications/wait')return route.fulfill({status:429,headers:{'Retry-After':'5'},json:{busy:true}});
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{}});
    if(url.origin==='http://127.0.0.1:9999'&&url.pathname==='/') {fixture.views++;return route.fulfill({contentType:'text/html',body:pageHtml});}
    return route.abort();
  });
  await page.goto('http://127.0.0.1:9999/',{waitUntil:'domcontentloaded'});
  const button=page.locator('#hubShellUpdateHost [data-hub-update]');
  await button.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('[data-hub-update]').textContent==='Доступно обновление');
  return {...fixture, fixture,page,button};
}
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    for(const [width,height,mobile] of [[1280,900,false],[980,900,false],[320,740,true],[390,844,true],[430,932,true],[844,390,true],[932,430,true]]) {
      const {page,button,fixture}=await fixturePage(width,height,mobile);
      const search=page.locator(mobile?'#mobileRouterSearchToggle':'#routerSearchToggle');
      const a=await search.boundingBox(),b=await button.boundingBox();
      if(!mobile) {
        assert(Math.abs(a.y-b.y)<=1,'desktop search/update stay on the same row');
        assert(Math.abs(a.height-b.height)<=1,'matching desktop button heights: '+JSON.stringify({width,a,b}));
        assert(b.x>=a.x+a.width,'desktop update follows search');
      }
      assert.equal(await button.evaluate(el=>getComputedStyle(el).color),'rgb(228, 185, 104)','available update uses the shared amber color');
      assert(await button.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'button text fits');
      const notice=page.locator('#hubUpdateNotice');
      assert(await notice.evaluate(el=>el===el.parentElement.firstElementChild),'update notice is the first panel block');
      assert((await notice.boundingBox()).y<(await button.boundingBox()).y,'status sits above header controls');
      assert.equal(await notice.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)','status background is transparent on every screen');
      const noticeBounds=await notice.boundingBox();
      assert(noticeBounds.width<=680,'status is narrower than the desktop panel');
      const contentCenter=await notice.evaluate(el=>{
        const parent=el.parentElement,bounds=parent.getBoundingClientRect(),style=getComputedStyle(parent);
        const left=parseFloat(style.paddingLeft),right=parseFloat(style.paddingRight);
        return bounds.left+left+(bounds.width-left-right)/2;
      });
      assert(Math.abs(noticeBounds.x+noticeBounds.width/2-contentCenter)<=1,'status is centered in the workspace next to the sidebar');
      if(!mobile) assert(noticeBounds.y>=16,'desktop notice has top spacing');
      if(mobile) {
        const resources=await page.locator('.hubResourcesPanel').boundingBox();
        assert(Math.abs(a.y-b.y)<=1,'search and update share the mobile tools row');
        assert(b.x>=a.x+a.width,'mobile tools follow search / update order');
        assert(resources.y>=Math.max(a.y+a.height,b.y+b.height),'resource panel follows compact header tools');
        assert(Math.abs(a.width-b.width)<=1,'mobile controls have equal column widths');
        assert(a.height>=32&&b.height>=32,'compact mobile controls retain usable targets');
        for(const selector of ['#mobileRouterSearchToggle','#hubSidebarMenuButton','[data-hub-update]']) {
          assert(await page.locator(selector).evaluateAll(elements=>elements.every(el=>!el.getClientRects().length||el.scrollHeight<=el.clientHeight+1)),'control text is not vertically clipped: '+selector);
        }
      }
      await page.screenshot({path:path.join(output,'header-'+width+'.png'),fullPage:false});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal overflow: '+JSON.stringify(await page.evaluate(()=>Array.from(document.querySelectorAll('main,.top,.desktopHeader,.desktopHeaderTop,.desktopHeaderBottom,.links,.headerActions,.toolbar')).map(el=>({class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right,scroll:el.scrollWidth})))));
      fixture.data.available=false;
      fixture.data.remote_revision=fixture.data.current_revision;
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='current');
      assert.equal(await button.evaluate(el=>getComputedStyle(el).color),'rgb(70, 197, 139)','current build uses the shared green color');
      assert.equal(await button.textContent(),'Обновление не требуется');
      assert(await notice.isHidden(),'current build has no separate notice on desktop or mobile');
      assert.equal(await notice.textContent(),'','previous update notice is cleared');
      const checks=fixture.checks;
      await button.click();
      assert.equal(fixture.checks,checks+1,'current build button still checks for updates');
      assert(await notice.isHidden(),'manual check of current build leaves no banner');
      await page.screenshot({path:path.join(output,'current-'+width+'.png'),fullPage:false});
      fixture.data.source_older=true;
      fixture.data.remote_version='v'+(Number(fixture.data.current_version.slice(1))-1);
      fixture.data.remote_revision='d'.repeat(64);
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').title.includes('Более старая версия'));
      assert.equal(await button.textContent(),'Обновление не требуется');
      assert.equal(await button.getAttribute('data-update-state'),'current');
      assert(await notice.isHidden(),'older published version never produces an update banner');
      fixture.data.remote_version='v'+(Number(fixture.data.current_version.slice(1))-2);
      await button.click();
      await page.waitForFunction(version=>document.querySelector('[data-hub-update]').title.includes('в источнике '+version),fixture.data.remote_version);
      assert.equal(fixture.starts,0,'older source only permits checking, never installation');
      assert(await page.locator('#hubUpdateConfirm').isHidden(),'older source never asks to install');
      fixture.data.source_older=false;fixture.data.remote_version=fixture.data.current_version;
      fixture.data.source_incompatible=true;
      fixture.data.notice='Сборка в источнике обновлений пока не поддерживает приложение Android. Установленная панель и приложение продолжают работать; эта сборка пропущена.';
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='unavailable');
      assert.equal(await button.textContent(),'Проверить обновление');
      assert.equal(await notice.getAttribute('data-result'),'','incompatible published source is not an installation failure');
      assert((await notice.textContent()).includes('продолжают работать'));
      assert(await notice.evaluate(el=>el.scrollWidth<=el.clientWidth+1&&el.scrollHeight<=el.clientHeight+1),'compatibility notice fits');
      assert.equal(await button.getAttribute('title'),fixture.data.notice);
      await button.click();
      assert.equal(fixture.starts,0,'incompatible source only rechecks, never installs');
      assert(await page.locator('#hubUpdateConfirm').isHidden(),'incompatible source never opens install confirmation');
      await page.screenshot({path:path.join(output,'mobile-api-compatibility-'+width+'.png'),fullPage:false});
      fixture.data.source_incompatible=false;fixture.data.notice='';
      fixture.data.error='Не удалось проверить обновление: Сборка в источнике ещё не поддерживает проверяемое обновление из панели.';
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='error');
      const gap=await notice.evaluate(el=>{
        const bounds=el.getBoundingClientRect();
        let next=el.nextElementSibling;
        while(next&&!next.getClientRects().length)next=next.nextElementSibling;
        return {below:next.getBoundingClientRect().top-bounds.bottom,top:bounds.top,left:bounds.left,right:bounds.right,
          align:getComputedStyle(el).textAlign,wrapped:el.scrollWidth<=el.clientWidth+1,clipped:el.scrollHeight>el.clientHeight+1};
      });
      assert(gap.below>=(mobile?10:12),'notice has space before the header: '+JSON.stringify(gap));
      assert(gap.top>=0&&gap.left>=0&&gap.right<=width,'top notice stays inside the screen');
      assert.equal(gap.align,'center');assert(gap.wrapped&&!gap.clipped,'long error wraps without clipping');
      await page.screenshot({path:path.join(output,'notice-'+width+'.png'),fullPage:false});
      fixture.data.error='';fixture.data.available=true;fixture.data.remote_revision='b'.repeat(64);
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='available');
      await button.click();
      const confirmPanel=page.locator('#hubUpdateConfirm');
      await confirmPanel.waitFor({state:'visible'});
      assert((await page.locator('#hubUpdateConfirmLead').textContent()).includes('Действительно обновить'));
      assert((await page.locator('#hubUpdateConfirmSource').textContent()).includes('https://raw.githubusercontent.com'));
      assert(await confirmPanel.evaluate(el=>el.scrollWidth<=innerWidth+1),'confirmation has no horizontal overflow');
      await page.locator('#hubUpdateConfirmCancel').click();
      assert.equal(fixture.starts,0,'cancel must not start update');
      await button.click();
      await confirmPanel.waitFor({state:'visible'});
      await page.locator('#hubUpdateConfirmAccept').click();
      assert.equal(fixture.starts,1);
      await page.waitForFunction(() => document.querySelector('[data-hub-update]').disabled);
      assert(await button.isDisabled(),'cannot start another update');
      fixture.failures=1;
      fixture.data.current_revision=fixture.data.remote_revision;fixture.data.available=false;
      fixture.data.job={id:'job-one',status:'succeeded',revision:fixture.data.current_revision,message:'Панель успешно обновлена. Страница будет перезагружена.'};
      await page.waitForFunction(()=>document.getElementById('hubUpdateNotice').textContent.includes('Ожидается результат'),null,{timeout:6000});
      await page.waitForFunction(()=>document.getElementById('hubUpdateNotice').dataset.result==='succeeded',null,{timeout:6000});
      await page.waitForTimeout(1700);
      assert.equal(fixture.views,2,'exactly one automatic page reload');
      await page.waitForFunction(()=>document.querySelector('[data-hub-update]').textContent==='Обновление не требуется'&&document.getElementById('hubUpdateNotice').hidden);
      assert.equal(await button.getAttribute('data-update-state'),'current','updated build is current after reload');
      await page.waitForTimeout(1700);assert.equal(fixture.views,2,'successful old job must not cause reload loop');
      assert.equal(fixture.starts,1,'reconnect never resends install');
      fixture.data.checking=true;
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.getElementById('hubUpdateNotice').textContent==='Проверяется наличие обновлений панели…');
      fixture.data.checking=false;fixture.data.remote_revision='c'.repeat(64);fixture.data.available=true;
      await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(()=>document.getElementById('hubUpdateNotice').textContent.startsWith('Доступно обновление панели:'));
      assert.equal(await button.getAttribute('data-update-state'),'available','old success does not hide another update');
      assert.equal(fixture.views,2,'rechecking an old success never reloads the page');
      console.log('HUB_UPDATE_UI_OK',width,'layout, confirmation, restart reconnect, one reload and current/new-update notices');
      await page.close();
    }
    const {page,button,fixture}=await fixturePage(1280,900,false);
    await button.click();
    await page.locator('#hubUpdateConfirmAccept').click();
    fixture.data.job={id:'job-one',status:'failed',message:'Обновление не выполнено. Предыдущий файл восстановлен.'};
    await page.waitForFunction(()=>document.getElementById('hubUpdateNotice').dataset.result==='failed',null,{timeout:6000});
    assert((await page.locator('#hubUpdateNotice').textContent()).includes('восстановлен'));
    assert.equal(fixture.views,1,'failed update stays visible');
    console.log('HUB_UPDATE_UI_OK rollback error remains visible');await page.close();
    async function fixturePage(width,height,mobile) {return createFixture(browser,width,height,mobile);}
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
