// Real HTTP/cookies and a persistent mobile browser profile; no router or VPS.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {chromium,webkit}=require('playwright');
const engine=process.env.OWRT_TEST_ENGINE==='webkit'?webkit:chromium;
const https=process.env.OWRT_TEST_HTTPS==='1';
const out=path.resolve('.test-output/ios-session-resume');fs.mkdirSync(out,{recursive:true});
const state=fs.mkdtempSync(path.join(out,'state-')),profile=fs.mkdtempSync(path.join(out,'profile-'));
let server,context;
async function startServer(port=0){
 const child=spawn(process.env.TEST_PYTHON||'python',['-u','-c',String.raw`
import importlib.util,json,os,sys
from pathlib import Path
state=Path(sys.argv[1]);os.environ['OWRT_REMOTE_STATE_DIR']=str(state)
spec=importlib.util.spec_from_file_location('session_browser_hub','vps/owrt-remote-hub.py')
hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
hub.add_notification=lambda *args,**kwargs:None
if not hub.AUTH_FILE.exists():
 hub.save_auth_state({'username':'fixture-owner','password':hub.password_digest('fixture-password',iterations=1000),'captcha':{'mode':'digits'}})
class Handler(hub.Handler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.maybe_serve_ios_install(self.parsed().path):return
  if self.path=='/' and self.admin_ok():
   self.send_bytes(200,b'<!doctype html><title>Authenticated Hub fixture</title><h1 id="connected">Hub connected</h1>','text/html; charset=utf-8');return
  super().do_GET()
server=hub.HubHTTPServer(('127.0.0.1',int(sys.argv[2])),Handler)
server.app=hub.App(state/'hub.db',hub.session_token(),'fixture-agent','');server.is_tls=sys.argv[3]=='1'
if server.is_tls:
 import datetime,ipaddress,ssl
 from cryptography import x509
 from cryptography.x509.oid import NameOID
 from cryptography.hazmat.primitives import hashes,serialization
 from cryptography.hazmat.primitives.asymmetric import ec
 key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
 now=datetime.datetime.now(datetime.timezone.utc)
 cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
  .not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1))
  .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256()))
 (state/'tls-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 (state/'tls-cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
 tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(str(state/'tls-cert.pem'),str(state/'tls-key.pem'))
 server.socket=tls.wrap_socket(server.socket,server_side=True)
print(json.dumps({'port':server.server_port}),flush=True)
server.serve_forever()
`,state,String(port),https?'1':'0'],{cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['ignore','pipe','pipe']});
 return await new Promise((resolve,reject)=>{
  let stdout='',stderr='';const timer=setTimeout(()=>{child.kill();reject(new Error('Fixture startup timed out: '+stderr));},10000);
  child.stderr.on('data',part=>stderr+=part);
  child.once('exit',code=>{clearTimeout(timer);if(!stdout.includes('\n'))reject(new Error('Fixture exited '+code+': '+stderr));});
  child.stdout.on('data',part=>{stdout+=part;if(stdout.includes('\n')){clearTimeout(timer);resolve({child,port:JSON.parse(stdout.split('\n')[0]).port});}});
 });
}
async function stopServer(){if(server){const child=server.child;server=null;if(child.exitCode===null){child.kill();await once(child,'exit');}}}
async function openBrowser(){
 context=await engine.launchPersistentContext(profile,{...(engine===chromium?{channel:'chrome'}:{}),headless:true,ignoreHTTPSErrors:https,viewport:{width:390,height:844},isMobile:true,hasTouch:true,
  userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'});
 context.setDefaultTimeout(10000);
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true}));
 return context.pages()[0]||await context.newPage();
}
(async()=>{try{
 server=await startServer();const port=server.port,base=(https?'https':'http')+'://127.0.0.1:'+port;
 let page=await openBrowser();const errors=[];
 const watch=p=>p.on('pageerror',error=>errors.push(error.message));watch(page);
 await page.goto(base+'/login');await page.locator('#passwordLoginForm').waitFor();const cachedLogin=await page.content();
 const loginRequests=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/login')loginRequests.push(request.isNavigationRequest());});
 await page.waitForFunction(()=>!loginSessionProbeInFlight);
 await page.locator('#hubUsername').fill('fixture-owner');
 await page.locator('#hubPassword').fill('fixture-password');
 const digits=(await page.locator('#hubCaptchaCode').innerText()).replace(/\D/g,'');
 await page.locator('[name="captcha_answer"]').fill(digits);
 await page.locator('#passwordLoginForm .primaryBtn').click();await page.locator('#connected').waitFor();
 assert.deepEqual(loginRequests,[true],'standalone password login is a document navigation, not fetch');
 const cookie=(await context.cookies(base)).find(item=>item.name==='owrt_remote_session');
 assert(cookie?.httpOnly&&cookie.expires>Date.now()/1000+86400,'browser retains a persistent HttpOnly cookie: '+JSON.stringify(cookie?{httpOnly:cookie.httpOnly,expires:cookie.expires,now:Date.now()/1000}:null));
 assert.equal(cookie.secure,https,'HTTPS retains the Secure cookie flag');
 console.log('IOS_SESSION_OK real_login_persistent_cookie');
 await context.close();context=null;await stopServer();
 server=await startServer(port);page=await openBrowser();watch(page);
 await page.goto(base+'/login');await page.locator('#connected').waitFor();
 assert.equal(page.url(),base+'/','reopen after browser and Hub restart resumes at the dashboard');
 console.log('IOS_SESSION_OK cold_browser_and_server_restart');
 for(let reopen=0;reopen<3;reopen++){
  await context.close();context=null;page=await openBrowser();watch(page);
  await page.goto(base+'/login');await page.locator('#connected').waitFor();
  assert.equal((await context.cookies(base)).find(item=>item.name==='owrt_remote_session')?.value,cookie.value,'repeated app closes retain the same session');
 }
 console.log('IOS_SESSION_OK three_more_cold_reopens');

 // Restore the old login document without a new GET, like a stale app snapshot.
 await page.route('**/login',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:cachedLogin}));
 let probes=0,release;
 const gate=new Promise(resolve=>release=resolve);
 await page.route('**/api/session/status',async route=>{probes++;await gate;await route.continue();});
 await page.goto(base+'/login',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>loginSessionProbeInFlight);
 await page.evaluate(()=>{for(let n=0;n<20;n++){
  window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
  window.dispatchEvent(new Event('online'));document.dispatchEvent(new Event('visibilitychange'));
 }});
 assert.equal(probes,1,'resume events share a single in-flight session check');
 release();await page.locator('#connected').waitFor();
 await page.unroute('**/api/session/status');
 console.log('IOS_SESSION_OK cached_login_and_single_resume_probe');

 await context.setOffline(true);
 await page.goto(base+'/login',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>!loginSessionProbeInFlight);
 assert(await page.locator('#passwordLoginForm').isVisible());
 assert.equal((await context.cookies(base)).find(item=>item.name==='owrt_remote_session')?.value,cookie.value,'network failure does not clear the cookie');
 await page.waitForTimeout(1600);
 await context.setOffline(false);await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 await page.locator('#connected').waitFor();
 console.log('IOS_SESSION_OK offline_then_reconnect');
 await page.unroute('**/login');
 await page.goto(base+'/logout');await page.locator('#passwordLoginForm').waitFor();
 await page.waitForFunction(()=>!loginSessionProbeInFlight);
 assert(!(await context.cookies(base)).some(item=>item.name==='owrt_remote_session'),'explicit logout clears the cookie');
 assert.equal((await context.request.get(base+'/api/session/status')).status(),401,'revoked session cannot resume');
 assert.deepEqual(errors,[],'login resume has no browser script errors');
 await page.screenshot({path:path.join(out,'logged-out.png')});
 console.log('IOS_SESSION_OK explicit_logout_stays_logged_out');
}finally{if(context)await context.close();await stopServer();}})().catch(error=>{console.error(error);process.exitCode=1;});
