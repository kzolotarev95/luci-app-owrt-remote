// Actual Hub HTML in WebKit/Chromium. Every network response is an isolated fixture.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process'),{webkit,chromium}=require('playwright');
const out=path.resolve('.test-output/ios-install-gate');fs.mkdirSync(out,{recursive:true});
execFileSync(process.env.TEST_PYTHON||'python',['-m','tests.test_ios_install_gate','--export-preview',path.join(out,'pages.json')]);
const pages=JSON.parse(fs.readFileSync(path.join(out,'pages.json'),'utf8'));
const iphone='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const mac='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15';
(async()=>{for(const engine of [webkit,chromium]){
 const browser=await engine.launch({headless:true,...(engine===chromium?{channel:'chrome'}:{})});
 try{for(const scenario of [
  {name:'iphone-safari',ua:iphone,gate:true,width:390},
  {name:'iphone-chrome',ua:iphone.replace('Version/18.0','CriOS/130.0'),gate:true,width:320},
  {name:'iphone-firefox',ua:iphone.replace('Version/18.0','FxiOS/130.0'),gate:true,width:390},
  {name:'ipad-desktop-ua',ua:mac,touch:5,gate:true,width:1024},
  {name:'copied-app-hint',ua:iphone,hint:true,gate:true,width:390},
  {name:'home-screen',ua:iphone,app:true,width:390},
  {name:'display-mode-standalone',ua:iphone,display:true,width:390},
  {name:'mac-desktop',ua:mac,touch:0,width:1366},
  {name:'windows-desktop',ua:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130',width:1366},
  {name:'android-browser',ua:'Mozilla/5.0 (Linux; Android 15) Chrome/130 Mobile Safari/537.36',width:390},
  {name:'no-js-ios',ua:iphone,gate:true,nojs:true,width:390},
 ]){
  const context=await browser.newContext({userAgent:scenario.ua,viewport:{width:scenario.width,height:950},hasTouch:!!scenario.gate||!!scenario.app,isMobile:scenario.width<700,javaScriptEnabled:!scenario.nojs});
  const errors=[],requests=[];
  try{
   await context.addInitScript(({app,display,touch})=>{
    Object.defineProperty(navigator,'standalone',{value:!!app});
    if(touch!==undefined)Object.defineProperty(navigator,'maxTouchPoints',{value:touch});
    if(display){const media=window.matchMedia.bind(window);window.matchMedia=query=>{const result=media(query);if(query==='(display-mode: standalone)')Object.defineProperty(result,'matches',{value:true});return result;};}
    Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedInstallUrl=text;}}});
   },scenario);
   if(scenario.hint)await context.addCookies([{name:'owrt_remote_ios_app',value:'standalone',url:'https://hub.test'}]);
   await context.route('**/*',route=>{
    const url=new URL(route.request().url());requests.push(url.pathname);
    if(url.pathname==='/api/session/status')return route.fulfill({status:401,json:{ok:false,authenticated:false}});
    if(url.pathname==='/api/auth/meta')return route.fulfill({json:{ok:true,auth:{is_owner:false,totp_enabled:false}}});
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,routers:[],groups:[],sessions:[],notifications:[]}});
    const page=scenario.nojs&&url.pathname==='/login'?pages['/ios-install/']:pages[url.pathname];
    return page?route.fulfill({contentType:'text/html; charset=utf-8',body:page}):route.fulfill({status:404,body:''});
   });
   const page=await context.newPage();page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
   await page.goto('https://hub.test/login',{waitUntil:'domcontentloaded'});
   if(scenario.gate){
    await page.locator('.iosInstall').waitFor();
    assert.equal(await page.locator('#passwordLoginForm').count(),0,'no login form in browser');
    assert.equal(await page.locator('#cards,#terminal').count(),0,'no panel or terminal');
    assert.equal(requests.filter(url=>url.startsWith('/api/')).length,0,'blocked pages never start panel/auth polls');
    assert.equal(await page.locator('.iosInstallSteps li').count(),3);
    if(!scenario.nojs){await page.locator('#iosInstallCopy').click();assert.equal(await page.evaluate(()=>window.copiedInstallUrl),'https://hub.test/');}
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'installation card fits the screen');
    await page.screenshot({path:path.join(out,engine.name()+'-'+scenario.name+'.png'),fullPage:true});
    if(!scenario.nojs){
     await page.goto('https://hub.test/',{waitUntil:'domcontentloaded'});
     await page.locator('.iosInstall').waitFor();
     assert.equal(await page.locator('#cards').count(),0,'a direct dashboard link also stays behind installation');
    }
   }else{
    await page.locator('#passwordLoginForm').waitFor();
    assert.equal(await page.locator('.iosInstall').count(),0,'permitted browser/app retains login');
    if(scenario.app||scenario.display){
     await page.evaluate(()=>{const link=document.createElement('a');link.href='/login?inside=1';link.target='_blank';link.id='insideAppLink';link.textContent='Внутренний переход';document.body.append(link);});
     await page.locator('#insideAppLink').click();
     await page.waitForURL(url=>url.searchParams.get('inside')==='1');
     await page.locator('#passwordLoginForm').waitFor();
     assert.equal(context.pages().length,1,'internal links stay inside the installed app');
    }
   }
   assert.deepEqual(errors,[],engine.name()+'/'+scenario.name+' script errors');
   console.log('IOS_INSTALL_GATE_OK',engine.name(),scenario.name);
  }finally{await context.close();}
 }}finally{await browser.close();}
}})().catch(error=>{console.error(error);process.exitCode=1;});
