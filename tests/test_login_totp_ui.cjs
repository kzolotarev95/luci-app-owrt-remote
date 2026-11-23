const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const out=path.resolve('.test-output/totp-login');
execFileSync('python',['-c',String.raw`
import importlib.util,json,os,sys,tempfile
from pathlib import Path
from unittest import mock
out=Path('.test-output/totp-login')
out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='totp-ui-') as state:
    os.environ['OWRT_REMOTE_STATE_DIR']=state
    spec=importlib.util.spec_from_file_location('totp_ui','vps/owrt-remote-hub.py')
    hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
    for enabled in [False,True]:
        auth=hub.normalize_auth_state({'username':'owner','captcha':{'mode':'digits'},'totp':{'enabled':enabled,'secret':'JBSWY3DPEHPK3PXP'}})
        with mock.patch.object(hub,'load_auth',return_value=auth):
            out.joinpath(('on' if enabled else 'off')+'.html').write_text(hub.login_html(),encoding='utf-8')
            out.joinpath(('on' if enabled else 'off')+'.json').write_text(json.dumps(hub.public_auth_meta(auth)),encoding='utf-8')
`]);
(async()=>{const b=await chromium.launch({headless:true,channel:'chrome'});let count=0;try{
for(const scenario of[{name:'desktop-off',w:1280,h:900,on:false},{name:'desktop-on',w:1280,h:900,on:true},{name:'phone-off',w:390,h:844,mobile:true,on:false},{name:'phone-on',w:390,h:844,mobile:true,on:true},{name:'narrow-off',w:320,h:740,mobile:true,on:false},{name:'on-meta-failure',w:1280,h:900,on:true,fail:true},{name:'off-no-js',w:390,h:844,on:false,nojs:true}]){
 const c=await b.newContext({viewport:{width:scenario.w,height:scenario.h},isMobile:!!scenario.mobile,hasTouch:!!scenario.mobile,javaScriptEnabled:!scenario.nojs});
 try{
  let meta=JSON.parse(fs.readFileSync(path.join(out,scenario.on?'on.json':'off.json'),'utf8')),fail=!!scenario.fail,posts=[];
  const html=fs.readFileSync(path.join(out,scenario.on?'on.html':'off.html'),'utf8');
  await c.route('**/*',r=>{const u=new URL(r.request().url());if(u.pathname==='/api/auth/meta')return fail?r.abort():r.fulfill({json:{ok:true,auth:meta}});if(r.request().method()==='POST'&&u.pathname==='/login'){const fields=new URLSearchParams();for(const match of r.request().postData().matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)(?=\r\n--)/g))fields.append(match[1],match[2]);posts.push(fields);return r.fulfill({status:401,contentType:'text/html',body:'Неверный логин или пароль'});}if(u.pathname==='/login')return r.fulfill({contentType:'text/html',body:html});return r.fulfill({status:404,body:''});});
  const p=await c.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto('http://hub.test/login');
  const card=p.locator('#loginOtpCard'),otp=p.locator('#hubOtp');
  assert.equal(await card.isVisible(),scenario.on,scenario.name+' visibility');
  assert.equal(await otp.isDisabled(),!scenario.on);
  assert.equal(await otp.getAttribute('required')!==null,scenario.on);
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await p.screenshot({path:path.join(out,scenario.name+'.png')});
  if(!scenario.nojs){
   await p.locator('#hubUsername').fill('owner');await p.locator('#hubPassword').fill('fixture-password');await p.locator('[name="captcha_answer"]').fill('1234');
   if(scenario.on){
    await p.locator('.primaryBtn').click();assert.equal(posts.length,0,'enabled 2FA cannot submit blank code');
    await otp.fill('123456');
   }
   await p.locator('.primaryBtn').click();await p.waitForFunction(()=>!loginSubmitInFlight);
   assert.equal(posts.length,1);assert.equal(posts[0].has('otp'),scenario.on,'hidden field is excluded from form data');
   if(scenario.on){
    await p.locator('#hubUsername').fill('viewer');assert.equal(await card.isVisible(),false,'delegated login does not inherit owner 2FA');assert.equal(await otp.inputValue(),'');
    await p.locator('.primaryBtn').click();await p.waitForFunction(()=>!loginSubmitInFlight);assert.equal(posts.length,2);assert.equal(posts[1].has('otp'),false);
    await p.locator('#hubUsername').fill(' owner ');assert.equal(await card.isVisible(),true,'trimmed owner login requires code');
   }
   fail=false;meta.totp_enabled=!scenario.on;await p.evaluate(()=>loadLoginMeta());
   assert.equal(await card.isVisible(),!scenario.on,'API enabled/disabled state is applied');
  }
  assert.deepEqual(errors,[],scenario.name+' browser errors');console.log('LOGIN_TOTP_UI_OK',scenario.name);count++;
 }finally{await c.close();}
}
}finally{await b.close();}console.log('LOGIN_TOTP_BROWSER_OK:',count,'scenarios');})().catch(e=>{console.error(e);process.exitCode=1;});
