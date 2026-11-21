// Browser regression: an absent version must not move the action into the header.
const {chromium} = require('playwright');
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('vps/owrt-remote-hub.py', 'utf8');
const start = source.indexOf('function renderTachyon(router) {{');
const end = source.indexOf('\nfunction render(list)', start);
const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context = {escapeHtml:escape, escapeAttr:escape};
vm.createContext(context);
vm.runInContext(source.slice(start,end).replaceAll('{{','{').replaceAll('}}','}'), context);
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.OWRT_TEST_BROWSER_BIN ? {executablePath:process.env.OWRT_TEST_BROWSER_BIN} : {})});
  try {
    for (const [width,height] of [[1280,900],[390,844],[844,390]]) {
      const page = await browser.newPage({viewport:{width,height}});
      const cards = ['2.0.0-beta.15','', '2.0.0~beta.15-r1'].map(version =>
        '<section class="card">'+context.renderTachyon({permissions:{admin:true},tachyon:{installed:true,online:true,version,url:'/tachyon/test/'}})+'</section>').join('');
      await page.setContent('<style>*{box-sizing:border-box}body{margin:12px;font:13px Arial;background:#21192e;color:#fff}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{min-width:0}.btn{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:8px 12px;border:1px solid #736387;border-radius:7px;background:#3b3049;color:white;text-decoration:none;font-weight:bold}@media(max-width:600px){.cards{grid-template-columns:1fr}}</style><div class="cards">'+cards+'</div>');
      const boxes = await page.locator('.tachyonIntegration').evaluateAll(items => items.map(el => {
        const box=el.getBoundingClientRect(), meta=el.querySelector('.tachyonMeta').getBoundingClientRect(), action=el.querySelector('.tachyonAction').getBoundingClientRect();
        return {height:box.height,actionTop:action.top-box.top,metaBottom:meta.bottom-box.top,overflow:el.scrollWidth>el.clientWidth,center:Math.abs((action.left+action.width/2)-(box.left+box.width/2))};
      }));
      for (const box of boxes) {
        assert(!box.overflow,`${width}: overflowing card`);
        assert(box.actionTop>=box.metaBottom+7,`${width}: action must be below metadata`);
        assert(box.center<1,`${width}: centered button`);
        assert(Math.abs(box.actionTop-boxes[0].actionTop)<1,`${width}: version changed action placement`);
        assert(Math.abs(box.height-boxes[0].height)<1,`${width}: version changed block height`);
      }
      if (process.env.OWRT_TEST_SCREENSHOT_DIR) await page.screenshot({path:process.env.OWRT_TEST_SCREENSHOT_DIR+`/tachyon-cards-${width}.png`,fullPage:true});
      await page.close();
    }
    console.log('TACHYON_CARD_LAYOUT_OK: desktop, portrait and landscape');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
