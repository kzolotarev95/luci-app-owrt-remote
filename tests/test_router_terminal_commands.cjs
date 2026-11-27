// Actual generated terminal, isolated transport; never connects to a router/VPS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const output = path.resolve('.test-output/router-terminal-commands');
fs.mkdirSync(output,{recursive:true});
execFileSync(process.env.TEST_PYTHON || 'python',['-c',String.raw`
import importlib.util, json, os, sys, tempfile
from pathlib import Path
from unittest import mock
with tempfile.TemporaryDirectory() as state:
    os.environ['OWRT_REMOTE_STATE_DIR']=state
    spec=importlib.util.spec_from_file_location('terminal_commands_fixture','vps/owrt-remote-hub.py')
    hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
    cases={'25':{'release':'OpenWrt 25.12.5'},'24':{'release':'OpenWrt 24.10.4'},'23':{'release':'OpenWrt 23.05.6'},'unknown':{},'reported':{'release':'OpenWrt SNAPSHOT','package_manager':'apk'}}
    for key,status in cases.items():
        row={'id':'main','name':'Роутер <Дом>', 'status_json':json.dumps(status)}
        presets=hub.router_terminal_command_presets(row)
        assert presets['manager']==('apk' if key in ('25','reported') else 'opkg' if key in ('23','24') else '')
        page=hub.ssh_terminal_html_v2(row,'fixture-token')
        page=hub.inject_hub_appearance('/ssh/main/',page.encode('utf-8'),'text/html').decode('utf-8')
        Path('.test-output/router-terminal-commands/'+key+'.html').write_text(page,encoding='utf-8')
    page=hub.ssh_terminal_html_v2(hub.vps_terminal_row(),'fixture-token')
    assert 'id="routerTerminalCommands"' not in page
    Path('.test-output/router-terminal-commands/vps.html').write_text(page,encoding='utf-8')
`],{env:{...process.env,PYTHONIOENCODING:'utf-8'}});

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  for(const width of [320,390,1366]){
   const context=await browser.newContext({viewport:{width,height:900}});
   const page=await context.newPage(),errors=[],writes=[];
   page.on('pageerror',error=>errors.push(error.message));
   await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/api/ssh-session-write'){
     writes.push(new URLSearchParams(route.request().postData()).get('data'));
     return route.fulfill({json:{ok:true}});
    }
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,alive:true,data:''}});
    if(url.pathname.endsWith('.html'))return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(output,path.basename(url.pathname)),'utf8')});
    return route.fulfill({contentType:'text/javascript',body:''});
   });
   const ready=async()=>page.evaluate(()=>{
    term={focus(){},write(){},clear(){}};terminalMode='http';httpSid='fixture';passwordPromptActive=false;
   });
   await page.goto('http://hub.test/25.html');await ready();
   const buttons=page.locator('[data-router-command]');
   assert.deepEqual(await buttons.allTextContents(),['apk update','apk upgrade','apk info']);
   await buttons.nth(1).click();assert.equal(writes.at(-1),'apk upgrade','shortcut does not submit Enter');
   await page.evaluate(()=>passwordPromptActive=true);
   const before=writes.length;await buttons.first().click();assert.equal(writes.length,before,'do not insert into SSH password prompt');
   await ready();
   await page.locator('.routerCommandEditor summary').click();
   await page.locator('[data-router-command-input="0"]').fill('echo <img src=x onerror=alert(1)>');
   await page.locator('[data-router-command-save]').click();
   assert.equal(await buttons.first().textContent(),'echo <img src=x onerror=alert(1)>');
   assert.equal(await page.locator('#routerTerminalCommands img').count(),0,'custom text is escaped');
   await page.reload();await ready();
   assert.equal(await buttons.first().textContent(),'echo <img src=x onerror=alert(1)>','saved per-router commands survive reload');
   await page.goto('http://hub.test/24.html');await ready();
   assert.deepEqual(await buttons.allTextContents(),['opkg update','opkg list-upgradable','opkg list-installed'],'package manager change uses matching commands');
   await page.locator('.routerCommandEditor summary').click();
   await page.locator('[data-router-command-input="1"]').fill('uptime');
   await page.locator('[data-router-command-save]').click();await buttons.nth(1).click();
   assert.equal(writes.at(-1),'uptime');
   await page.locator('.routerCommandEditor summary').click();await page.locator('[data-router-command-reset]').click();
   assert.equal(await buttons.nth(1).textContent(),'opkg list-upgradable');
   await page.goto('http://hub.test/unknown.html');
   assert.deepEqual(await buttons.allTextContents(),['Команда 1','Команда 2','Команда 3']);
   await buttons.first().click();assert.equal(await page.locator('.routerCommandEditor').evaluate(el=>el.open),true);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile editor fits viewport');
   await page.goto('http://hub.test/25.html');await ready();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'toolbar fits viewport');
   await page.screenshot({path:path.join(output,'terminal-'+width+'.png'),fullPage:true});
   await page.goto('http://hub.test/vps.html');
   assert.equal(await page.locator('#routerTerminalCommands').count(),0,'router package commands never appear on VPS');
   assert.deepEqual(errors,[]);await context.close();console.log('ROUTER_TERMINAL_COMMANDS_OK',width);
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
