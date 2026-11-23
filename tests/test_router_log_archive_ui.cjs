const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve('.test-output/global-logs');
execFileSync(process.env.TEST_PYTHON || 'python', ['-c', String.raw`
import importlib.util, os, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='router-log-preview-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR'] = state
    spec = importlib.util.spec_from_file_location('preview_hub', 'vps/owrt-remote-hub.py')
    hub = importlib.util.module_from_spec(spec); sys.modules[spec.name] = hub; spec.loader.exec_module(hub)
    app = hub.App(Path(state)/'hub.db', 'preview-session', 'preview-agent', '')
    with app.conn() as conn:
        hub.upsert_router(conn, {'id':'main', 'name':'Test offline router', 'vps_host':'hub.example'})
        router = hub.router_with_access(hub.row_to_router(hub.get_router(conn,'main')), {'is_owner':True})
    out = Path('.test-output/global-logs'); out.mkdir(parents=True,exist_ok=True)
    out.joinpath('vps-dashboard.html').write_text(hub.dashboard_html([router],'owner',auth_bootstrap={'username':'owner','is_owner':True}),encoding='utf-8')
    out.joinpath('vps-logs.html').write_text(hub.router_logs_html(router),encoding='utf-8')
    out.joinpath('vps-terminal.html').write_text(hub.ssh_terminal_html_v2(hub.vps_terminal_row(),'preview-only'),encoding='utf-8')
`], {cwd:path.resolve(__dirname,'..')});
const dashboard = fs.readFileSync(path.join(root, 'vps-dashboard.html'), 'utf8');
const routers = JSON.parse(dashboard.match(/window\.ROUTERS = (\[.*\]);/)[1]);
let reads = 0, clears = 0;
function initialText() {
  return '[ERROR] WAN connection refused\n[WARN] Hub: Heartbeat timeout\n[OK] Xray started\n'
    + Array.from({length: 120}, (_, i) => `[OK] Event ${i}`).join('\n');
}
let text = initialText();
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/router/main/logs') {
    reads++;
    res.setHeader('Content-Type', 'application/json');
    return res.end(JSON.stringify({ok:true, online:false, text, bytes:Buffer.byteLength(text), last_sync:1791954000,
      storage:{path:'/var/lib/owrt-remote/hub.db',filesystem:'ext4',persistent:true},
      diagnosis:{level:'warn',text:'Remote выключен на роутере. Туннель и Heartbeat остановлены.',at:1791954000}}));
  }
  if (url.pathname === '/api/router/main/logs/clear') {
    assert.equal(req.method, 'POST'); clears++; text='';
    res.setHeader('Content-Type', 'application/json');
    return res.end(JSON.stringify({ok:true, pending_clear:true, message:'Архив VPS очищен. Очистка роутера ожидает связи.'}));
  }
  if (url.pathname === '/api/routers') {res.setHeader('Content-Type','application/json'); return res.end(JSON.stringify({routers}));}
  if (url.pathname === '/api/auth/meta') {res.setHeader('Content-Type','application/json'); return res.end(JSON.stringify({ok:true,auth:{username:'owner',is_owner:true,can_add_router:true}}));}
  if (url.pathname === '/api/notifications/wait') {res.writeHead(429,{'Content-Type':'application/json','Retry-After':'5'}); return res.end(JSON.stringify({busy:true}));}
  if (url.pathname.startsWith('/api/')) {res.setHeader('Content-Type','application/json'); return res.end('{}');}
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  const file = url.pathname === '/router/main/logs' ? 'vps-logs.html' : url.pathname === '/vps-terminal/' ? 'vps-terminal.html' : 'vps-dashboard.html';
  res.end(fs.readFileSync(path.join(root, file)));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    for (const mobile of [false,true]) {
      const page=await browser.newPage({viewport:mobile?{width:390,height:844}:{width:1280,height:900}});
      // Compare the actual rendered backgrounds without starting a terminal.
      const reference=await browser.newPage({viewport:page.viewportSize(),javascriptEnabled:false});
      await reference.route('https://cdn.jsdelivr.net/**',route=>route.abort());
      await reference.goto(`http://127.0.0.1:${server.address().port}/vps-terminal/`);
      const terminalBackground=await reference.locator('body').evaluate(el=>{
        const s=getComputedStyle(el);return {image:s.backgroundImage,color:s.backgroundColor,attachment:s.backgroundAttachment};
      });
      await reference.close();
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      const link=page.locator('[data-global-logs-link]');
      await link.waitFor({state:'attached'});
      assert.equal(await page.locator('#routerStats').isVisible(),true,'statistics are expanded at entry');
      assert.equal(await page.locator('#routerStats .statCard').count(),4);
      if(mobile){
        const toggle=page.locator('[data-hub-action="stats"]');
        await page.locator('#hubSidebarMenuButton').click();
        await page.locator('#hubSettingsSection>summary').click();
        assert.equal(await toggle.textContent(),'Скрыть статистику');
        assert.equal(await toggle.getAttribute('aria-expanded'),'true');
        await toggle.click();
        await page.evaluate(()=>loadRouters());
        assert.equal(await page.locator('#routerStats').isVisible(),false,'refresh preserves manual collapse');
        await page.locator('#hubSidebarMenuButton').click();
        await toggle.click();
        assert.equal(await page.locator('#routerStats').isVisible(),true);
      }
      assert.equal(await link.count(),1);
      assert.equal(await link.getAttribute('target'),'_blank');
      assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
      assert.equal(await link.getAttribute('href'),'/router/main/logs');
      assert.equal(await link.evaluate(el=>el.classList.contains('disabled')),false);
      await page.locator('[data-card-toggle]').count() && await page.locator('[data-card-toggle]').click();
      if (mobile) await page.locator('[data-actions-toggle]').click();
      assert.equal(await link.isVisible(),true,'offline card has enabled log link');
      await page.screenshot({path:path.join(root,mobile?'vps-card-mobile.png':'vps-card-desktop.png'),fullPage:true});
      const [popup] = await Promise.all([page.waitForEvent('popup'), link.click()]);
      await popup.waitForLoadState('domcontentloaded');
      assert.equal(new URL(popup.url()).pathname,'/router/main/logs');
      assert.equal(new URL(page.url()).pathname,'/');
      await popup.close();
      text=initialText();const before=reads;
      await page.goto(`http://127.0.0.1:${server.address().port}/router/main/logs`);
      await page.waitForFunction(()=>document.querySelector('#logsText').textContent.includes('Event 119'));
      assert((await page.locator('#logsStorage').textContent()).includes('Архив VPS: постоянный диск.'));
      assert((await page.locator('#logsStorage').textContent()).includes('/var/lib/owrt-remote/hub.db'));
      assert.deepEqual(await page.locator('body').evaluate(el=>{
        const s=getComputedStyle(el);return {image:s.backgroundImage,color:s.backgroundColor,attachment:s.backgroundAttachment};
      }),terminalBackground,'log page uses the VPS terminal background');
      if(mobile){
        const legend=page.locator('.legend');
        assert.equal(await legend.evaluate(el=>getComputedStyle(el).justifyContent),'center');
        const bounds=await legend.evaluate(el=>{
          const box=el.getBoundingClientRect(), first=el.firstElementChild.getBoundingClientRect(), last=el.lastElementChild.getBoundingClientRect();
          return {center:(box.left+box.right)/2,contentCenter:(first.left+last.right)/2,rows:[first.top,last.top]};
        });
        assert.equal(bounds.rows[0],bounds.rows[1],'all three labels fit in one row');
        assert(Math.abs(bounds.center-bounds.contentCenter)<2,'legend labels are centered');
      }
      assert.equal(reads,before+1);
      assert((await page.locator('#logsStatus').textContent()).includes('Роутер оффлайн'));
      assert((await page.locator('#logsDiagnosis').textContent()).includes('Remote выключен'));
      assert.equal(await page.locator('#logsDiagnosis').evaluate(el=>getComputedStyle(el).color),'rgb(253, 230, 138)');
      const lines=page.locator('#logsText .line');
      assert.equal(await lines.nth(0).evaluate(el=>getComputedStyle(el).color),'rgb(248, 113, 113)');
      assert.equal(await lines.nth(1).evaluate(el=>getComputedStyle(el).color),'rgb(253, 230, 138)');
      assert.equal(await lines.nth(2).evaluate(el=>getComputedStyle(el).color),'rgb(134, 239, 172)');
      await page.waitForTimeout(3200);
      assert.equal(reads,before+1,'snapshot does not poll');
      await page.locator('#logsLive').click();
      await page.locator('#logsText').evaluate(el=>el.scrollTop=0);
      await page.screenshot({path:path.join(root,mobile?'vps-logs-mobile.png':'vps-logs-desktop.png'),fullPage:true});
      await page.locator('#logsText').evaluate(el=>el.scrollTop=40);
      text+='\n[ERROR] <script>window.injected=1</script>\n[OK] Latest event';
      await page.waitForFunction(()=>document.querySelector('#logsText').textContent.includes('Latest event'));
      assert.equal(await page.locator('#logsText').evaluate(el=>el.scrollTop),40);
      assert.equal(await page.evaluate(()=>window.injected),undefined);
      assert.equal(await page.locator('#logsText script').count(),0);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.locator('#logsLive').click();
      const stopped=reads;await page.waitForTimeout(3200);assert.equal(reads,stopped);
      const beforeClear=clears;await page.locator('#logsClear').click();
      await page.waitForFunction(()=>document.querySelector('#logsStatus').textContent.includes('очищен'));
      assert.equal(clears,beforeClear+1);
      assert.deepEqual(errors,[]);
      await page.close();console.log('ROUTER_LOG_ARCHIVE_UI_OK',mobile?'mobile':'desktop');
    }
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
