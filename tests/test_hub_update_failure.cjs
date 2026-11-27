// Actual dashboard/update JS with a failed current build and a verified retry.
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require('playwright');
const prefix=fs.readFileSync(path.join(__dirname,'test_hub_update_ui.cjs'),'utf8').split('(async()=>{')[0];
const {createFixture}=new Function('require',prefix+'\nreturn {createFixture};')(require);
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{
 for(const width of [1280,390]){
  const {page,button,fixture}=await createFixture(browser,width,900,width<500);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const notice=page.locator('#hubUpdateNotice');
  const checkAgain=async()=>{
   const response=page.waitForResponse(r=>r.url().endsWith('/api/hub-update/check')&&r.request().method()==='POST');
   await button.click();await (await response).finished();
   await page.evaluate(()=>new Promise(requestAnimationFrame));
  };
  const failure='Обновление не выполнено: Обслуживание журналов: rc=1; ERROR: Custom /etc/logrotate.d/owrt-remote found; manual review required';
  fixture.data.remote_revision=fixture.data.current_revision;
  fixture.data.available=false;
  fixture.data.job={id:'job-one',status:'failed',revision:fixture.data.current_revision,message:failure};
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='failed');
  assert.equal(await button.textContent(),'Повторить проверку');
  assert.equal(await notice.textContent(),failure);
  assert.equal(await button.evaluate(el=>getComputedStyle(el).color),'rgb(255, 48, 48)');
  assert(!await button.textContent().then(x=>x.includes('последняя версия')));
  await checkAgain();assert.equal(fixture.starts,0,'checking a failure cannot install without confirmation');
  fixture.data.available=true;fixture.data.retry_available=true;
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  await page.getByRole('button',{name:'Повторить обновление',exact:true}).waitFor();
  await button.click();
  assert((await page.locator('#hubUpdateConfirmLead').textContent()).includes('Повторить незавершённое'));
  await page.locator('#hubUpdateConfirmAccept').click();
  await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='busy');
  assert.equal(fixture.starts,1);
  fixture.data.available=false;fixture.data.retry_available=false;
  fixture.data.job={id:'job-one',status:'succeeded',revision:fixture.data.current_revision,message:'Обслуживание журналов и таймер проверены.'};
  await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='current');
  await page.waitForFunction(()=>performance.getEntriesByType('navigation')[0].type==='reload');
  assert.equal(await button.textContent(),'Установлена последняя версия');
  assert(await notice.isHidden());
  assert.equal(fixture.views,2,'one reload after confirmed recovery');
  // Local repair can archive the obsolete failed job without an active worker.
  fixture.data.job={};
  await page.evaluate(()=>sessionStorage.setItem('hub-update-pending','archived-job'));
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('[data-hub-update]').dataset.updateState==='current');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('hub-update-pending')),null);
  // An older published revision of v125 must not undo a local r2 repair.
  fixture.data.current_release=2;fixture.data.remote_release=1;
  fixture.data.source_older=true;fixture.data.remote_revision='c'.repeat(64);
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  await page.getByRole('button',{name:'Установлена локальная версия',exact:true}).waitFor();
  assert((await button.getAttribute('title')).includes('-r2'));
  await checkAgain();
  assert.equal(fixture.starts,1,'older published build cannot start from the repaired dashboard');
  assert(await page.locator('#hubUpdateConfirm').isHidden());
  // Publishing the revision restores ordinary update availability.
  fixture.data.source_older=false;fixture.data.remote_release=2;fixture.data.available=true;
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  await page.getByRole('button',{name:'Доступно обновление',exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  await page.close();console.log('HUB_FAILED_CURRENT_RETRY_OK',width);
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
