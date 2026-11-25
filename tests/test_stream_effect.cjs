// Execute the real shared renderer with a recording canvas, without a browser
// or network. Check movement, bounded work and the existing pause lifecycle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('files/www/luci-static/resources/owrt_remote_effects.js','utf8');
const hub = fs.readFileSync('vps/owrt-remote-hub.py','utf8');
const shared = hub.split("def hub_design_runtime_script():",2)[1]
  .split("return r'''<script>\n",2)[1].split("</script>'''",1)[0];
assert.equal(source.replace(/\r\n/g,'\n'), shared.replace(/\r\n/g,'\n'), 'VPS and router use exactly the same renderer');
let reduced = false, nextFrame = 0;
const frames = new Map(), strokes = [], storage = new Map();
const ctx = {
  clearRect(){strokes.length=0;},save(){},restore(){},translate(){},scale(){},setTransform(){},
  beginPath(){},moveTo(){},bezierCurveTo(...points){assert.equal(points.length,6);},setLineDash(dash){this.dash=dash;},
  stroke(){strokes.push({dash:[...this.dash],offset:this.lineDashOffset,color:this.strokeStyle});}
};
const canvas = {hidden:true,width:1,height:1,style:{},getContext:()=>ctx,getBoundingClientRect:()=>({width:0,height:0})};
const env = vm.createContext({
  console,Math,Date,Set,Array,Number,String,JSON,canvas,ctx,frames,Event,
  document:{hidden:false,documentElement:{dataset:{}},querySelectorAll:()=>[]},
  window:{innerWidth:1600,innerHeight:900,devicePixelRatio:1,matchMedia:()=>({matches:reduced}),dispatchEvent(){}},
  localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)},
  requestAnimationFrame:fn=>{frames.set(++nextFrame,fn);return nextFrame;},
  cancelAnimationFrame:id=>frames.delete(id),setTimeout:()=>0,clearTimeout:()=>{}
});
vm.runInContext(source.replace(/\nbootHubAppearance\(\);\s*$/,''),env);
const run = code => vm.runInContext(code,env);
const frame = ts => {const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn(ts));};
run('seasonalFxCanvas=canvas;seasonalFxCtx=ctx;setSeasonEffectMode("streams");');
assert.equal(run('seasonalFxParticles.length'),7);
assert.equal(frames.size,1);
frame(16);
assert.equal(strokes.length,14,'one faint track and one moving stroke per curve');
const offsets=strokes.filter(s=>s.dash.length).map(s=>s.offset);
frame(32);
assert.notDeepEqual(strokes.filter(s=>s.dash.length).map(s=>s.offset),offsets,'strokes advance in real time');
for(let i=3;i<2400;i++)frame(i*16);
assert.equal(run('seasonalFxParticles.length'),7,'long running animation does not accumulate particles');
assert.equal(frames.size,1,'one frame loop after extended rendering');
run('window.innerWidth=3840;window.innerHeight=2160;seasonFxResetCanvas();seasonFxBuildParticles();');
assert.equal(run('seasonalFxParticles.length'),7,'4K does not increase track count');
run('setHubDesign("lite");');
assert.equal(frames.size,0);
assert.equal(canvas.hidden,true);
assert.equal(canvas.width,1);
run('setHubDesign("default");');
assert.equal(frames.size,1);
run('document.hidden=true;');frame(40000);
assert.equal(frames.size,0,'hidden document stops drawing');
run('document.hidden=false;seasonFxStart();');
reduced=true;frame(40016);
assert.equal(strokes.length,14,'reduced motion still displays static curves');
assert.equal(frames.size,0,'reduced motion has no animation loop');
reduced=false;run('seasonFxStart();');
assert.equal(frames.size,1);
run('setSeasonEffectMode("off");');
assert.equal(frames.size,0);
assert.equal(canvas.hidden,true);
console.log('STREAM_EFFECT_OK: identical shared renderer, 7 moving curves, bounded work, lite/hidden/reduced-motion/off controls');
