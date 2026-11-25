const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve('.test-output/router-updates');
fs.mkdirSync(root, {recursive: true});
const defaultRepo = execFileSync(process.env.TEST_PYTHON || 'python', ['-c', String.raw`
import importlib.util, os, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as state:
    os.environ['OWRT_REMOTE_STATE_DIR']=state
    spec=importlib.util.spec_from_file_location('preview_updates','vps/owrt-remote-hub.py')
    hub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=hub;spec.loader.exec_module(hub)
    out=Path('.test-output/router-updates')
    out.joinpath('updates.html').write_text(hub.router_updates_html('test-csrf'),encoding='utf-8')
    out.joinpath('selection.html').write_bytes(hub.inject_hub_appearance('/router-updates/',hub.router_updates_html('test-csrf').encode('utf-8'),'text/html; charset=utf-8'))
    out.joinpath('dashboard.html').write_text(hub.dashboard_html([],'owner',auth_bootstrap={'username':'owner','is_owner':True}),encoding='utf-8')
    print(hub.RAW_REPO_BASE)
`], {env:{...process.env,PYTHONIOENCODING:'utf-8'},encoding:'utf-8'}).trim();
let job = null, routers = [], starts = [], reboots = [], polls = 0;
function data() {return {ok:true,job,routers};}
function send(res, data) {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));}
const server = http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/router-updates') {polls++;return send(res,data());}
  if(url.pathname==='/api/router-updates/refresh') {
    assert.equal(req.headers['x-router-update-csrf'],'test-csrf');
    let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{
      const payload=JSON.parse(body);
      if(job&&job.id===payload.id&&job.state!=='running')job=null;
      send(res,data());
    });return;
  }
  if(url.pathname==='/api/router-updates/start') {
    assert.equal(req.headers['x-router-update-csrf'],'test-csrf');
    let body='';req.on('data',data=>body+=data);req.on('end',()=>{
      const payload=JSON.parse(body);starts.push(payload);
      job={id:'test-task',request_id:payload.request_id,state:'running',cancel_requested:false,items:[{id:'node-5',name:'АНТОН ВПН',state:'running',message:'Установка выполняется',output:'one\ntwo'}]};
      send(res,data());
    });return;
  }
  if(url.pathname==='/api/router-updates/reboot') {
    assert.equal(req.headers['x-router-update-csrf'],'test-csrf');
    let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{
      const payload=JSON.parse(body);reboots.push(payload);
      job={id:'test-reboot',request_id:payload.request_id,operation:'reboot',state:'running',cancel_requested:false,items:[{id:'node-5',name:'АНТОН ВПН',state:'running',message:'Ожидаю новую загрузку и SSH',output:''}]};
      send(res,data());
    });return;
  }
  if(url.pathname==='/api/router-updates/cancel') {
    job.cancel_requested=true;job.state='finished';job.items[0].state='cancelled';
    job.items[0].message='Остановка на роутере подтверждена';return send(res,data());
  }
  if(url.pathname==='/api/auth/meta')return send(res,{username:'owner',is_owner:true,users:[],sessions:[],captcha:{mode:'digits'}});
  if(url.pathname.startsWith('/api/'))return send(res,{});
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end(fs.readFileSync(path.join(root,url.pathname==='/'?'dashboard.html':url.pathname==='/router-updates-selection/'?'selection.html':'updates.html')));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    for(const width of [390,1280]) {
      job=null;routers=['one','two','key','skip'].map((id,i)=>({id,name:'Роутер '+id,online:i!==3,last_seen:0,xray:'running'}));
      const page=await browser.newPage({viewport:{width,height:900},isMobile:width<680,hasTouch:width<680});
      await page.goto(`http://127.0.0.1:${server.address().port}/router-updates-selection/`);
      await page.waitForFunction(()=>!document.getElementById('start').disabled);
      const row=id=>page.locator('#routerSelection [data-router="'+id+'"]');
      await page.locator('#selectNone').click();assert.equal(await page.locator('#start').isDisabled(),true);
      await page.locator('#selectOnline').click();assert.equal(await row('skip').locator('input[type=checkbox]').isChecked(),false);
      assert.equal(await page.locator('#selectionCount').textContent(),'Выбрано 3 из 4');
      await row('one').locator('select').selectOption('individual');await row('one').locator('input[type=password]').fill('first-private');
      await row('two').locator('select').selectOption('individual');
      const before=starts.length;await page.locator('#start').click();assert.equal(starts.length,before);
      assert.match(await page.locator('#status').textContent(),/Укажи SSH-пароль/);
      await row('two').locator('input[type=password]').fill('second-private');
      await row('key').locator('select').selectOption('key');await page.locator('#sshPassword').fill('shared-private');
      await row('two').locator('input[type=password]').focus();
      routers[0].online=false;routers.push({id:'new',name:'Новый',online:true,last_seen:0,xray:'running'});
      await page.waitForFunction(()=>document.getElementById('selectionCount').textContent==='Выбрано 3 из 5');
      assert.equal(await row('new').locator('input[type=checkbox]').isChecked(),false);
      assert.equal(await row('two').locator('input[type=password]').evaluate(node=>document.activeElement===node),true);
      assert.equal(await row('one').locator('input[type=password]').inputValue(),'first-private');
      assert.equal(await row('two').locator('input[type=password]').inputValue(),'second-private');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(root,'selection-'+width+'.png'),fullPage:true});
      await page.locator('#start').click();await page.waitForFunction(()=>document.getElementById('cancel').disabled===false);
      assert.deepEqual(starts.at(-1).router_ids,['one','two','key']);
      assert.deepEqual(starts.at(-1).ssh_passwords,{one:'first-private',two:'second-private',key:''});
      assert.equal(starts.at(-1).ssh_password,'shared-private');
      assert.equal(await row('one').locator('input[type=password]').inputValue(),'');
      assert.equal(await row('two').locator('input[type=password]').inputValue(),'');
      assert.equal(await row('one').locator('select').isDisabled(),true);
      assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes('private')),false);
      await page.close();
    }
    for(const mobile of [false,true]) {
      job=null;
      routers=[{id:'node-5',name:'АНТОН ВПН',online:true,last_seen:Math.floor(Date.now()/1000),xray:'running'}];
      const page=await browser.newPage({viewport:mobile?{width:390,height:844}:{width:1280,height:900},isMobile:mobile,hasTouch:mobile});
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      const base=`http://127.0.0.1:${server.address().port}`;
      await page.goto(base+'/router-updates/');
      await page.waitForFunction(()=>!document.getElementById('start').disabled);
      assert.equal(await page.locator('.picker-device strong').textContent(),'АНТОН ВПН');
      assert.equal(await page.locator('.picker-device small').textContent(),'node-5 · Онлайн');
      assert.equal(await page.locator('#queueResults').isVisible(),false);
      assert.equal(await page.locator('#countTotal').textContent(),'1');
      assert.equal(await page.locator('#countSuccess').textContent(),'0');
      assert.equal(await page.locator('#queueOutcomePanel').isVisible(),false);
      assert.equal(await page.locator('.picker-device small').getAttribute('class'),'ok');
      assert.equal(await page.locator('#command').inputValue(),'wget -O - "'+defaultRepo+'/install.sh?v=$(date +%s)" | sh');
      const command=mobile?'https://another.example/updates/router.sh?v=2&from=hub':'wget -O - "https://repo.example/install.sh?v=$(date +%s)" | sh';
      await page.locator('#command').fill(command);
      await page.locator('#sshPassword').fill('test-password');
      await page.locator('#start').click();
      await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Обновление выполняется'));
      assert.equal(starts.at(-1).command,command);assert.equal(starts.at(-1).ssh_password,'test-password');
      assert.equal(await page.locator('#sshPassword').inputValue(),'');
      assert.equal(await page.locator('#start').isDisabled(),true);
      assert.equal(await page.locator('#cancel').isEnabled(),true);
      assert.equal(await page.locator('#countActive').textContent(),'1');
      await page.locator('summary').click();
      job.items[0].output+='\nupdated';
      await page.waitForFunction(()=>document.querySelector('pre').textContent.includes('updated'));
      assert.equal(await page.locator('details').evaluate(node=>node.open),true);
      await page.reload();
      await page.waitForFunction(()=>document.getElementById('cancel').disabled===false);
      const count=starts.length;
      assert.equal(await page.locator('#start').isDisabled(),true);
      routers[0].online=false;routers[0].xray='stopped';
      await page.locator('#refreshRouters').click();
      await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Статусы обновлены'));
      assert.equal(job.state,'running');
      assert.equal(starts.length,count);
      assert.equal(await page.locator('.live-badge').textContent(),'● Офлайн');
      assert.equal(await page.locator('#countActive').textContent(),'1');
      assert.equal(await page.locator('#start').isDisabled(),true);
      if(mobile) {
        await page.locator('#cancel').click();
        await page.waitForFunction(()=>document.querySelector('.router strong').textContent==='Остановлено — АНТОН ВПН');
        assert.equal(job.cancel_requested,true);
        assert.equal(await page.locator('#cancel').isDisabled(),true);
        assert.equal(await page.locator('#cancel').textContent(),'Остановить всё');
      }
      job.state='finished';job.items[0].state='success';job.items[0].message='Установка успешно завершена, rc=0';
      await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Очередь завершена'));
      await page.waitForFunction(()=>document.querySelector('.router strong').textContent==='Успешно — АНТОН ВПН');
      assert.equal(await page.locator('.router strong').textContent(),'Успешно — АНТОН ВПН');
      assert.equal(await page.locator('#countSuccess').textContent(),'1');
      assert.equal(await page.locator('#queueProgress').getAttribute('aria-valuenow'),'100');
      await page.locator('#command').fill(command);
      assert.equal(starts.length,count);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&innerWidth===visualViewport.width),true);
      assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes('test-password')),false);
      await page.screenshot({path:path.join(root,mobile?'mobile.png':'desktop.png'),fullPage:true});
      // Match the user's example exactly, including all finished outcomes.
      job.items=Array.from({length:35},(_,i)=>({id:'node-'+(i+1),name:i===21?'АНТОН ВПН':'Роутер '+(i+1),state:i<21?'success':i<25?'error':i<26?'unknown':i<27?'cancelled':'skipped',message:i===21?'Общий SSH-пароль не принят. Команда обновления не запускалась.':i===25?'SSH недоступен: результат и остановка не подтверждены.':'Итог установки: причина '+(i+1),output:i===21?'Permission denied\n'+Array.from({length:50},(_,i)=>'Диагностика '+i).join('\n'):''}));
      await page.waitForFunction(()=>document.getElementById('countTotal').textContent==='35');
      for(const [key,value] of Object.entries({Success:21,Error:4,Unknown:1,Cancelled:1,Skipped:8,Total:35,Active:0,Queued:0})) {
        assert.equal(await page.locator('#count'+key).textContent(),String(value));
      }
      assert.equal(await page.locator('#queueProgress').getAttribute('aria-valuenow'),'100');
      assert.equal(await page.locator('#queueOutcomePanel').isVisible(),true);
      assert.equal(await page.locator('#outcomeTitle').textContent(),'Проблемы обновления · 14');
      assert.equal(await page.locator('#outcomeList article').count(),14);
      assert.equal(await page.locator('#outcomeList strong').first().textContent(),'АНТОН ВПН');
      assert.equal(await page.locator('#outcomeList small').first().textContent(),'ID: node-22');
      assert.match(await page.locator('#outcomeList .outcome-reason').first().textContent(),/Общий SSH-пароль не принят/);
      assert.equal(await page.locator('#outcomeList article[data-router="node-1"]').count(),0);
      const outcomeButton=state=>page.locator('.outcome-toggle[data-state="'+state+'"]');
      await outcomeButton('error').click();
      assert.equal(await page.locator('#outcomeTitle').textContent(),'Ошибки · 4');
      assert.equal(await page.locator('#outcomeList article').count(),4);
      assert.equal(await outcomeButton('error').getAttribute('aria-pressed'),'true');
      assert.equal(await outcomeButton('unknown').getAttribute('aria-pressed'),'false');
      assert.equal(await page.locator('#showAllOutcomes').isVisible(),true);
      const errorRow=page.locator('#outcomeList article[data-router="node-22"]');
      await errorRow.locator('summary').click();
      await errorRow.locator('pre').evaluate(node=>{node.scrollTop=72;});
      await page.locator('#outcomeList').evaluate(node=>{node.scrollTop=55;});
      const savedScroll=await page.locator('#outcomeList').evaluate(node=>node.scrollTop);
      const pollCount=polls;
      job.items[21].output+='\nНовая строка';job.items[21].message+=' Ответ SSH сохранён.';
      await page.waitForFunction(()=>document.querySelector('#outcomeList pre').textContent.includes('Новая строка'));
      assert.ok(polls>pollCount);
      assert.equal(await outcomeButton('error').getAttribute('aria-pressed'),'true');
      assert.equal(await errorRow.locator('details').evaluate(node=>node.open),true);
      assert.equal(await errorRow.locator('pre').evaluate(node=>node.scrollTop),72);
      assert.equal(await page.locator('#outcomeList').evaluate(node=>node.scrollTop),savedScroll);
      for(const [state,title,total] of [['unknown','Не подтверждено',1],['cancelled','Остановлено',1],['skipped','Пропущено',8]]) {
        await outcomeButton(state).click();
        assert.equal(await page.locator('#outcomeTitle').textContent(),title+' · '+total);
        assert.equal(await page.locator('#outcomeList article').count(),total);
      }
      await page.locator('#showAllOutcomes').click();
      assert.equal(await page.locator('#outcomeList article').count(),14);
      assert.equal(await page.locator('#showAllOutcomes').isVisible(),false);
      assert.equal(starts.length,count,'Viewing outcome details must never execute an update');
      await page.evaluate(()=>document.querySelector('.queue-dashboard').scrollIntoView());
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.locator('.queue-dashboard').screenshot({path:path.join(root,mobile?'mobile-summary.png':'desktop-summary.png')});
      // Reproduce 5 errors and zero entries in the other result categories.
      job.items=job.items.slice(0,5).map((item,i)=>({...item,state:'error',name:i===0?'<img src=x onerror="window.outcomeInjected=true">':item.name,message:'Ошибка SSH <script>window.outcomeInjected=true</script>'}));
      await page.waitForFunction(()=>document.getElementById('countError').textContent==='5');
      assert.equal(await page.locator('#outcomeList article').count(),5);
      assert.equal(await page.evaluate(()=>window.outcomeInjected===true),false);
      assert.equal(await page.locator('#outcomeList img,#outcomeList script').count(),0);
      for(const state of ['unknown','cancelled','skipped']) {
        await outcomeButton(state).click();
        assert.equal(await page.locator('#outcomeList article').count(),0);
        assert.equal(await page.locator('#outcomeList .outcome-empty').textContent(),'Роутеров в этой категории нет.');
      }
      // A different queue resets the previous category instead of hiding failures.
      job={...job,id:'next-task'};
      await page.waitForFunction(()=>document.getElementById('outcomeTitle').textContent==='Проблемы обновления · 5');
      routers=[{id:'node-5',name:'АНТОН ВПН',online:true,last_seen:Math.floor(Date.now()/1000),xray:'running'},{id:'node-36',name:'Новый роутер',online:false,last_seen:0,xray:'unknown'}];
      await page.locator('#refreshRouters').click();
      await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Можно запустить новую очередь'));
      assert.equal(job,null);
      assert.equal(starts.length,count,'Refresh must never execute an update');
      assert.equal(await page.locator('.picker-row').count(),2);
      assert.equal(await page.locator('.picker-device strong').first().textContent(),'АНТОН ВПН');
      assert.equal(await page.locator('#queueResults').isVisible(),false);
      assert.equal(await page.locator('#countTotal').textContent(),'2');
      assert.equal(await page.locator('#countSuccess').textContent(),'0');
      assert.equal(await page.locator('#queueOutcomePanel').isVisible(),false);
      assert.equal(await page.locator('#outcomeList article').count(),0);
      assert.equal(await page.locator('#selectionCount').textContent(),'Выбрано 1 из 2');
      assert.equal(await page.locator('#routerSelection [data-router="node-36"] .router-check').isChecked(),false);
      await page.locator('#selectAll').click();
      assert.equal(await page.locator('#start').isEnabled(),true);
      assert.equal(await page.locator('#command').inputValue(),command);
      await page.reload();
      await page.waitForFunction(()=>document.getElementById('countTotal').textContent==='2');
      assert.equal(await page.locator('#countSuccess').textContent(),'0');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.deepEqual(errors,[]);
      await page.close();
    }
    // Long content must wrap without widening the mobile layout viewport.
    const longName='Советская_30_Удалённый_роутер_'.repeat(8),longId='node-'+ 'x'.repeat(95);
    for(const viewport of [{width:320,height:740},{width:360,height:800},{width:390,height:844},{width:430,height:932},{width:844,height:390}]) {
      const page=await browser.newPage({viewport,isMobile:true,hasTouch:true,deviceScaleFactor:2});
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      routers=[{id:longId,name:longName,online:true,last_seen:Math.floor(Date.now()/1000),xray:'running'}];
      job={id:'mobile-wrap',state:'finished',cancel_requested:false,items:[{id:longId,name:longName,state:'error',message:'Не удалось скачать https://updates.example/'+ 'abcdef'.repeat(100)+' — SSH не ответил.',output:'diagnostic='+ '0123456789'.repeat(150)}]};
      await page.goto(`http://127.0.0.1:${server.address().port}/router-updates/`);
      await page.waitForFunction(()=>document.getElementById('countError').textContent==='1');
      await page.locator('#command').fill('wget -O - "https://updates.example/'+ 'longpath'.repeat(100)+'" | sh');
      await page.locator('#sshPassword').fill('password');await page.locator('#sshPassword').focus();
      await page.locator('#outcomeList summary').click();
      await page.locator('#results summary').click();
      const layout=await page.evaluate(()=>({width:innerWidth,visual:visualViewport.width,scroll:document.documentElement.scrollWidth,fields:['command','sshPassword'].map(id=>parseFloat(getComputedStyle(document.getElementById(id)).fontSize)),buttons:[...document.querySelectorAll('.head-actions button,.head-actions .btn,.actions button')].map(node=>({height:node.getBoundingClientRect().height,width:node.getBoundingClientRect().width})),panels:[...document.querySelectorAll('.panel')].map(node=>node.getBoundingClientRect().width),overflow:[...document.querySelectorAll('.head,.head-actions,.actions,.note,textarea,input,.queue-heading,.metric,.router,.outcome-router,pre')].filter(node=>{const rect=node.getBoundingClientRect();return rect.width&& (rect.left<0||rect.right>innerWidth+1);}).map(node=>node.id||node.className)}));
      assert.equal(layout.width,viewport.width,JSON.stringify(layout));
      assert.ok(layout.scroll<=viewport.width,JSON.stringify(layout));
      assert.ok(Math.abs(layout.visual-viewport.width)<1,JSON.stringify(layout));
      assert.deepEqual(layout.overflow,[]);
      if(viewport.width<=680){assert.ok(layout.fields.every(size=>size>=16));assert.ok(layout.buttons.every(button=>button.height>=44));assert.equal(layout.buttons[0].width,layout.buttons[1].width);}
      await page.screenshot({path:path.join(root,'mobile-layout-'+viewport.width+'.png'),fullPage:true});
      assert.deepEqual(errors,[]);await page.close();
    }
    // Mobile wrapping rules must preserve the original centered desktop column.
    job=null;routers=[{id:'node-5',name:'АНТОН ВПН',online:true,last_seen:0,xray:'running'}];
    for(const width of [1280,1440,1920]) {
      const page=await browser.newPage({viewport:{width,height:900}});
      await page.goto(`http://127.0.0.1:${server.address().port}/router-updates/`);
      await page.waitForFunction(()=>!document.querySelector('#start').disabled);
      const layout=await page.evaluate(()=>{const wrap=document.querySelector('.wrap').getBoundingClientRect(),input=document.querySelector('#sshPassword').getBoundingClientRect(),start=document.querySelector('#start').getBoundingClientRect(),cancel=document.querySelector('#cancel').getBoundingClientRect(),refresh=document.querySelector('#refreshRouters').getBoundingClientRect(),back=document.querySelector('.head-actions a').getBoundingClientRect();return {left:wrap.left,right:wrap.right,width:wrap.width,viewport:innerWidth,inputWidth:input.width,inputFont:getComputedStyle(document.querySelector('#sshPassword')).fontSize,actionRow:Math.abs(start.top-cancel.top)<1,headerRow:Math.abs(refresh.top-back.top)<1,columns:getComputedStyle(document.querySelector('.metrics')).gridTemplateColumns.split(' ').length};});
      assert.equal(layout.width,1100,JSON.stringify(layout));
      assert.ok(Math.abs(layout.left-(width-layout.right))<1,JSON.stringify(layout));
      assert.equal(layout.inputWidth,480);
      assert.equal(layout.inputFont,'14px');
      assert.equal(layout.actionRow,true);
      assert.equal(layout.headerRow,true);
      assert.equal(layout.columns,4);
      await page.screenshot({path:path.join(root,'desktop-layout-'+width+'.png'),fullPage:true});
      await page.close();
    }
    for(const width of [320,390,1280]) {
      job=null;routers=[{id:'node-5',name:'АНТОН ВПН',online:true,last_seen:0,xray:'running'}];
      const page=await browser.newPage({viewport:{width,height:900},isMobile:width<680,hasTouch:width<680});
      await page.goto(`http://127.0.0.1:${server.address().port}/router-updates/`);
      await page.waitForFunction(()=>!document.querySelector('#rebootAll').disabled);
      assert.equal(await page.locator('#rebootAll').textContent(),'Перезапустить выбранные');
      assert.equal(await page.locator('#rebootAll').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(185, 28, 28)');
      // Reboot uses only the password, never the update command or its validation.
      await page.locator('#command').fill('');
      await page.locator('#sshPassword').fill('reboot-secret');
      const updateCount=starts.length;
      await page.locator('#rebootAll').click();
      await page.waitForFunction(()=>document.getElementById('status').textContent==='Перезапуск выполняется');
      assert.equal(reboots.at(-1).ssh_password,'reboot-secret');
      assert.equal(Object.hasOwn(reboots.at(-1),'command'),false);
      assert.equal(starts.length,updateCount);
      assert.equal(await page.locator('#sshPassword').inputValue(),'');
      assert.equal(await page.locator('#rebootAll').isDisabled(),true);
      assert.equal(await page.locator('#start').isDisabled(),true);
      assert.equal(await page.locator('#activeLabel').textContent(),'Перезапускаются');
      await page.reload();
      await page.waitForFunction(()=>document.getElementById('status').textContent==='Перезапуск выполняется');
      assert.equal(await page.locator('#rebootAll').isDisabled(),true);
      const rebootCount=reboots.length;
      await page.locator('#cancel').click();
      await page.waitForFunction(()=>document.getElementById('status').textContent==='Очередь перезапуска завершена');
      assert.equal(reboots.length,rebootCount);
      job.items[0].state='success';job.items[0].message='Роутер успешно перезагружен: новая загрузка и SSH подтверждены';
      await page.waitForFunction(()=>document.querySelector('.router strong').textContent==='Успешно — АНТОН ВПН');
      assert.equal(await page.locator('#countSuccess').textContent(),'1');
      assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes('reboot-secret')),false);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(root,'reboot-'+width+'.png'),fullPage:true});
      await page.close();
    }
    job=null;routers=[];
    const dashboard=await browser.newPage();
    await dashboard.goto(`http://127.0.0.1:${server.address().port}/`);
    const link=dashboard.locator('#routerUpdatesGroup a');
    assert.equal(await link.getAttribute('target'),'_blank');
    assert.equal(await link.getAttribute('href'),'/router-updates/');
    assert.match(await link.getAttribute('rel'),/noopener/);
    await dashboard.close();
    console.log('Router updates desktop/mobile dashboard, centered desktop widths, outcome lists, 320/360/390/430px and landscape layouts, live statuses, refresh, polling, reload, privacy and new-tab link: PASS');
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
