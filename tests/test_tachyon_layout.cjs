// Requires Playwright; see docs/TACHYON_INTEGRATION.md for the layout scenario.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.OWRT_TEST_BROWSER_BIN ? {executablePath: process.env.OWRT_TEST_BROWSER_BIN} : {})
  });
  let count = 0;
  try {
    for (const [fixture, body] of Object.entries(fixtures)) {
      for (const [width, height] of [[1280,900], [390,844], [844,390]]) {
        for (const design of ['default','winter','lite']) {
          const context = await browser.newContext({viewport: {width,height}});
          try {
            await context.addInitScript(mode => {
              localStorage.setItem('owrtRemote:hubDesign', mode);
              localStorage.setItem('owrtRemote:seasonEffectMode', 'snow');
            }, design);
            await context.route('**/*', route => {
              if (route.request().isNavigationRequest()) return route.fulfill({status:200, contentType:'text/html', body});
              return route.abort(); // Layout check only: no RPC or router writes.
            });
            const page = await context.newPage();
            await page.goto('http://hub.test/tachyon/node-10/', {waitUntil:'domcontentloaded'});
            await page.waitForTimeout(80);
            const geometry = await page.evaluate(() => ({
              backgroundPosition: getComputedStyle(document.querySelector('.tachyon-bg-layer')).position,
              contentTop: document.querySelector('#maincontent').getBoundingClientRect().top,
              scrollY: window.scrollY,
              extraCanvas: Boolean(document.getElementById('seasonalFx'))
            }));
            const label = [fixture,width,height,design].join('/');
            assert.equal(geometry.backgroundPosition,'fixed',label);
            assert(geometry.contentTop >= -1 && geometry.contentTop <= 1, label + ': content pushed down: ' + geometry.contentTop);
            assert.equal(geometry.scrollY,0,label);
            assert.equal(geometry.extraCanvas,false,label);
            count++;
          } finally { await context.close(); }
        }
      }
    }
  } finally { await browser.close(); }
  console.log('TACHYON_LAYOUT_OK: ' + count + ' browser cases; content starts at the top');
})().catch(error => {console.error(error);process.exitCode=1;});
