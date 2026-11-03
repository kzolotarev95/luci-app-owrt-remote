// Check request frequency and repaint work without relying on real-time sleeps.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf('async function loadRouters(options = {}) {');
const end = source.indexOf('\nif (routerGroupFilter)', start);
assert(start>=0 && end>start);
let clock = 10000, calls = 0, renders = 0, wol = false, finish;
const timers = new Map();
const fixture = {
  hubDesignMode:'lite', lastAutomaticRouterPoll:0, routerPollInFlight:false,
  AbortController, Date:{now:()=>clock}, document:{hidden:false}, window:{ROUTERS:[{id:'main',name:'Router'}]},
  hasOpenWolPanel:()=>wol, renderRouterGroups(){},syncRouterNotesState(){},shouldDeferRouterRender:()=>false,
  renderRouterView(){renders++;},fillRouterForm(){},refreshDiagnosticPanel(){},
  setTimeout(fn,delay){timers.set(1,{fn,delay});return 1;},clearTimeout(id){timers.delete(id);},
  fetch(url,options){calls++;return fixture.respond(url,options);},
  respond:async()=>({ok:true,json:async()=>({routers:fixture.window.ROUTERS})}),
};
vm.createContext(fixture);
vm.runInContext(source.slice(start,end), fixture);
(async()=>{
  await fixture.loadRouters({automatic:true});
  assert.equal(calls,1);assert.equal(renders,0,'identical lite data does not rebuild cards');
  clock=15000;await fixture.loadRouters({automatic:true});assert.equal(calls,1);
  clock=20000;
  fixture.respond=()=>new Promise(resolve=>{finish=resolve;});
  const first=fixture.loadRouters({automatic:true});
  await fixture.loadRouters({automatic:true});assert.equal(calls,2,'no parallel automatic router fetches');
  finish({ok:true,json:async()=>({routers:[{id:'main',name:'Renamed'}]})});await first;
  assert.equal(renders,1);assert.equal(timers.size,0);
  fixture.document.hidden=true;clock=30000;
  await fixture.loadRouters({automatic:true});assert.equal(calls,2);
  fixture.document.hidden=false;wol=true;
  await fixture.loadRouters({automatic:true});assert.equal(calls,2,'preserve open WOL picker');
  wol=false;
  fixture.respond=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('timeout'))));
  const pending=fixture.loadRouters({automatic:true});
  assert.equal(timers.get(1).delay,10000);timers.get(1).fn();
  await assert.rejects(pending,/timeout/);
  assert.equal(fixture.routerPollInFlight,false,'timeouts release the polling guard');
  fixture.respond=async()=>({ok:true,json:async()=>({routers:fixture.window.ROUTERS})});
  fixture.hubDesignMode='winter';clock=35000;
  await fixture.loadRouters({automatic:true});assert.equal(calls,4,'normal designs retain the 5-second interval');
  assert.equal(renders,2);
  console.log('HUB_DESIGN_POLLING_OK: lite 10s, normal 5s, no parallel fetches or idle repaints, timeout recovery, hidden tabs and WOL');
})().catch(error=>{console.error(error);process.exitCode=1;});
