const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');

const output = path.resolve('.test-output/hub-update-commit-notes');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python', ['tests/test_vps_resources.py', '--export-preview', output],
  {env:{...process.env,PYTHONIOENCODING:'utf-8'}});
const pageHtml = fs.readFileSync(path.join(output, 'dashboard.html'), 'utf8');
const state = {
  ok:true, checked:true, checking:false, error:'',
  source:'https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main',
  current_revision:'a'.repeat(64), current_version:'v125',
  remote_revision:'c'.repeat(64), remote_version:'v125',
  remote_notes:'Исправили обслуживание журналов.\nДобавили описание обновления.',
  source_older:false, available:true, job:{}, csrf:'test-csrf'
};

(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    await page.route('**/*', async route => {
      const url=new URL(route.request().url());
      if(url.origin==='http://127.0.0.1:9999' && url.pathname==='/')
        return route.fulfill({contentType:'text/html',body:pageHtml});
      if(url.pathname==='/api/hub-update') return route.fulfill({json:state});
      if(url.pathname==='/api/auth/meta') return route.fulfill({json:{ok:true,auth:{username:'owner',is_owner:true,can_add_router:true}}});
      if(url.pathname==='/api/routers') return route.fulfill({json:{routers:[]}});
      if(url.pathname==='/api/vps/resources') return route.fulfill({json:{ok:true,cpu:{percent:1},memory:{percent:2},disk:{percent:3}}});
      if(url.pathname==='/api/notifications/wait') return route.fulfill({status:429,json:{busy:true}});
      if(url.pathname.startsWith('/api/')) return route.fulfill({json:{}});
      return route.abort();
    });
    await page.goto('http://127.0.0.1:9999/',{waitUntil:'domcontentloaded'});
    const button=page.locator('[data-hub-update]').first();
    await button.waitFor({state:'visible'});
    await page.waitForFunction(()=>document.querySelector('[data-hub-update]').textContent==='Доступно обновление');
    await button.click();
    await page.locator('#hubUpdateConfirm').waitFor({state:'visible'});
    assert.equal(await page.locator('#hubUpdateConfirmChanges').textContent(),state.remote_notes);
    console.log('HUB_UPDATE_COMMIT_NOTES_OK');
  } finally { await browser.close(); }
})().catch(error=>{ console.error(error); process.exitCode=1; });
