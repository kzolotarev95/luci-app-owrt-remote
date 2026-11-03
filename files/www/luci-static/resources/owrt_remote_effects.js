/* Shared Hub effects and design for the OpenWrt router panel. */
let seasonalFxCanvas = null;
let seasonButtons = [];
let seasonSwitches = [];
let hubDesignInitialized = false;
let seasonEffectsInitialized = false;
let hubAppearanceInitialized = false;
const SEASON_EFFECT_KEY = 'owrtRemote:seasonEffectMode';
const SEASON_EFFECT_MODES = new Set(['off', 'snow', 'rain', 'cosmos', 'embers', 'orbit', 'prism', 'pulse', 'laser']);
const HUB_DESIGN_KEY = 'owrtRemote:hubDesign';
const HUB_DESIGN_MODES = new Set(['default', 'winter', 'lite']);
let hubDesignMode = document.documentElement.dataset.hubDesign || 'default';
let seasonEffectMode = 'off';
let seasonalFxCtx = null;
let seasonalFxFrame = 0;
let seasonalFxResizeTimer = 0;
let seasonalFxLastTs = 0;
let seasonalFxParticles = [];
let seasonalFxSize = {width: 0, height: 0, dpr: 1};
let seasonalFxPointer = {x: 0, y: 0, lastX: 0, lastY: 0, targetWind: 0, wind: 0, lastMoveTs: 0, active: false};
function setHubDesign(nextMode, options = {}) {
  hubDesignMode = HUB_DESIGN_MODES.has(nextMode) ? nextMode : 'default';
  document.documentElement.dataset.hubDesign = hubDesignMode;
  if (options.persist !== false) {
    try { localStorage.setItem(HUB_DESIGN_KEY, hubDesignMode); } catch (error) {}
  }
  document.querySelectorAll('button[data-hub-design]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.hubDesign === hubDesignMode));
  });
  document.querySelectorAll('[data-hub-design-hint]').forEach(hint => {
    hint.textContent = hubDesignMode === 'lite'
      ? 'Эффекты отключены. Статистика VPS — раз в 5 с, роутеры — раз в 10 с. Выбор сохранён в этом браузере.'
      : hubDesignMode === 'winter'
        ? 'Зимние цвета и снежные детали. Падающий снег можно включить отдельно выше. Выбор сохранён в этом браузере.'
        : 'Обычный дизайн Hub. Выбор сохранён в этом браузере.';
  });
  syncSeasonButtons();
  if (hubDesignMode === 'lite') {
    seasonFxStop();
    seasonalFxParticles = [];
    if (seasonalFxCanvas) {
      seasonalFxCanvas.hidden = true;
      seasonalFxCanvas.width = 1;
      seasonalFxCanvas.height = 1;
    }
  } else {
    setSeasonEffectMode(seasonEffectMode, {persist: false});
  }
  window.dispatchEvent(new Event('hubdesignchange'));
}

function initHubDesign() {
  if (hubDesignInitialized) return;
  hubDesignInitialized = true;
  document.querySelectorAll('button[data-hub-design]').forEach(button => {
    button.addEventListener('click', () => setHubDesign(button.dataset.hubDesign));
  });
  window.addEventListener('storage', event => {
    if (event.key === HUB_DESIGN_KEY || event.key === null) {
      let mode = 'default';
      try { mode = localStorage.getItem(HUB_DESIGN_KEY); } catch (error) {}
      setHubDesign(mode, {persist: false});
    }
    if (event.key === SEASON_EFFECT_KEY || event.key === null) {
      setSeasonEffectMode(loadSeasonEffectMode(), {persist: false});
    }
  });
  let savedMode = hubDesignMode;
  try { savedMode = localStorage.getItem(HUB_DESIGN_KEY) || savedMode; } catch (error) {}
  setHubDesign(savedMode, {persist: false});
}

function loadSeasonEffectMode() {
  try {
    const value = localStorage.getItem(SEASON_EFFECT_KEY);
    return SEASON_EFFECT_MODES.has(value) ? value : 'off';
  } catch (e) {
    return 'off';
  }
}

function saveSeasonEffectMode(mode) {
  try {
    localStorage.setItem(SEASON_EFFECT_KEY, mode);
  } catch (e) {}
}

function syncSeasonButtons() {
  seasonButtons.forEach((button) => {
    const active = button.dataset.seasonMode === (hubDesignMode === 'lite' ? 'off' : seasonEffectMode);
    button.disabled = hubDesignMode === 'lite' && button.dataset.seasonMode !== 'off';
    button.dataset.active = active ? 'true' : 'false';
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function seasonFxResetCanvas() {
  if (!seasonalFxCanvas || hubDesignMode === 'lite' || document.hidden) return;
  seasonalFxCtx = seasonalFxCanvas.getContext('2d');
  if (!seasonalFxCtx) return;
  const rect = seasonalFxCanvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width || window.innerWidth || 1));
  const height = Math.max(1, Math.round(rect.height || window.innerHeight || 1));
  seasonalFxCanvas.width = Math.max(1, Math.round(width * dpr));
  seasonalFxCanvas.height = Math.max(1, Math.round(height * dpr));
  seasonalFxCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  seasonalFxSize = {width, height, dpr};
  if (!seasonalFxPointer.active) {
    seasonalFxPointer.x = width * .5;
    seasonalFxPointer.y = height * .44;
    seasonalFxPointer.lastX = seasonalFxPointer.x;
    seasonalFxPointer.lastY = seasonalFxPointer.y;
  }
}

function seasonFxSpawn(mode, randomY = false) {
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  if (mode === 'snow') {
    return {
      x: Math.random() * width,
      y: randomY ? Math.random() * height : -Math.random() * height,
      r: .8 + Math.random() * 1.1,
      vy: .014 + Math.random() * .022,
      vx: (Math.random() - .5) * .02,
      drift: Math.random() * Math.PI * 2,
      driftSpeed: .00045 + Math.random() * .0009,
      alpha: .52 + Math.random() * .26
    };
  }
  if (mode === 'cosmos') {
    const angle = Math.random() * Math.PI * 2;
    const distance = 20 + Math.random() * Math.max(width, height) * .28;
    const speed = .10 + Math.random() * .18;
    return {
      x: width * .5 + Math.cos(angle) * distance,
      y: height * .5 + Math.sin(angle) * distance,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      size: .7 + Math.random() * 1.8,
      tail: 10 + Math.random() * 18,
      twinkle: Math.random() * Math.PI * 2,
      twinkleSpeed: .006 + Math.random() * .01,
      alpha: .45 + Math.random() * .42
    };
  }
  if (mode === 'embers') {
    return {
      x: Math.random() * width,
      y: randomY ? Math.random() * height : height + Math.random() * height * .18,
      vx: (Math.random() - .5) * .045,
      vy: -(.022 + Math.random() * .05),
      size: 1.1 + Math.random() * 2.4,
      hue: 18 + Math.random() * 26,
      alpha: .22 + Math.random() * .26,
      sway: Math.random() * Math.PI * 2,
      swaySpeed: .0015 + Math.random() * .0024
    };
  }
  if (mode === 'orbit') {
    const centerX = width * .5;
    const centerY = height * .42;
    return {
      angle: Math.random() * Math.PI * 2,
      radius: 20 + Math.random() * Math.min(width, height) * .22,
      radiusSwing: 6 + Math.random() * 16,
      radiusDrift: Math.random() * Math.PI * 2,
      radiusSpeed: .0014 + Math.random() * .0022,
      speed: (Math.random() > .5 ? 1 : -1) * (.0012 + Math.random() * .0024),
      size: .9 + Math.random() * 2.2,
      alpha: .22 + Math.random() * .34,
      hue: 182 + Math.random() * 110,
      lane: .72 + Math.random() * .7,
      x: centerX,
      y: centerY
    };
  }
  if (mode === 'prism') {
    return {
      x: Math.random() * width,
      y: randomY ? Math.random() * height : (Math.random() * height * .2),
      vx: (Math.random() - .5) * .075,
      vy: (Math.random() - .5) * .075,
      size: 3.8 + Math.random() * 8.8,
      rot: Math.random() * Math.PI * 2,
      rotSpeed: (Math.random() - .5) * .006,
      hue: Math.random() * 360,
      alpha: .26 + Math.random() * .28
    };
  }
  if (mode === 'pulse') {
    return {
      angle: Math.random() * Math.PI * 2,
      radius: Math.random() * 26,
      radiusSpeed: .035 + Math.random() * .065,
      orbit: Math.random() * Math.PI * 2,
      orbitSpeed: (Math.random() - .5) * .0034,
      size: 4 + Math.random() * 16,
      hue: 182 + Math.random() * 150,
      alpha: .18 + Math.random() * .26,
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: .003 + Math.random() * .0045
    };
  }
  if (mode === 'laser') {
    const side = Math.random() > .5 ? 1 : -1;
    return {
      x: side > 0 ? -40 - Math.random() * width * .18 : width + 40 + Math.random() * width * .18,
      y: Math.random() * height,
      vx: side > 0 ? (.24 + Math.random() * .26) : -(.24 + Math.random() * .26),
      vy: (Math.random() - .5) * .065,
      len: 20 + Math.random() * 42,
      width: .8 + Math.random() * 1.5,
      hue: 190 + Math.random() * 140,
      alpha: .16 + Math.random() * .26,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: .003 + Math.random() * .004
    };
  }
  if (mode === 'aurora') {
    return {
      x: Math.random() * width,
      y: height * (.08 + Math.random() * .34),
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: .0008 + Math.random() * .0016,
      wave: 24 + Math.random() * 88,
      waveSpeed: .0002 + Math.random() * .0005,
      sway: Math.random() * Math.PI * 2,
      swaySpeed: .0004 + Math.random() * .001,
      thickness: 10 + Math.random() * 18,
      hue: 155 + Math.random() * 70,
      alpha: .12 + Math.random() * .14,
      vx: (Math.random() - .5) * .02,
      vy: (Math.random() - .5) * .01
    };
  }
  if (mode === 'matrix') {
    const columns = Math.max(2, Math.floor(width / 16));
    const column = Math.floor(Math.random() * columns);
    const columnWidth = width / columns;
    return {
      x: column * columnWidth + columnWidth * .5,
      y: randomY ? Math.random() * height : -Math.random() * height,
      vy: 4.6 + Math.random() * 6.8,
      size: 11 + Math.random() * 9,
      alpha: .38 + Math.random() * .42,
      glyph: seasonFxRandomGlyph(),
      glyphSpeed: .003 + Math.random() * .006,
      trail: 18 + Math.random() * 30,
      trailAlpha: .06 + Math.random() * .08,
      hue: 120 + Math.random() * 40
    };
  }
  if (mode === 'nebula') {
    const angle = Math.random() * Math.PI * 2;
    const distance = Math.random() * Math.min(width, height) * .34;
    return {
      x: width * .5 + Math.cos(angle) * distance,
      y: height * .5 + Math.sin(angle) * distance * .72,
      vx: (Math.random() - .5) * .075,
      vy: (Math.random() - .5) * .065,
      size: 70 + Math.random() * 130,
      hue: 225 + Math.random() * 90,
      alpha: .06 + Math.random() * .08,
      drift: Math.random() * Math.PI * 2,
      driftSpeed: .0003 + Math.random() * .0008,
      pulse: Math.random() * Math.PI * 2
    };
  }
  if (mode === 'fireworks') {
    const fromLeft = Math.random() > .5;
    const startX = fromLeft ? -60 - Math.random() * width * .2 : width + 60 + Math.random() * width * .2;
    const startY = Math.random() * height * .82;
    const speed = 1.8 + Math.random() * 3.4;
    const angle = fromLeft ? (-.26 + Math.random() * .38) : (Math.PI - .26 + Math.random() * .38);
    return {
      x: startX,
      y: startY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      gravity: .022 + Math.random() * .028,
      drag: .985 + Math.random() * .009,
      life: .4 + Math.random() * 1.2,
      size: 1.1 + Math.random() * 2.4,
      hue: Math.random() * 360,
      alpha: .34 + Math.random() * .42,
      sparkle: Math.random() * Math.PI * 2,
      sparkleSpeed: .03 + Math.random() * .06,
      trail: 8 + Math.random() * 18
    };
  }
  if (mode === 'vortex') {
    const angle = Math.random() * Math.PI * 2;
    return {
      x: width * .5,
      y: height * .5,
      angle,
      radius: 18 + Math.random() * Math.min(width, height) * .42,
      radiusSpeed: .012 + Math.random() * .018,
      spin: (Math.random() > .5 ? 1 : -1) * (.002 + Math.random() * .0035),
      wobble: Math.random() * Math.PI * 2,
      wobbleSpeed: .002 + Math.random() * .004,
      lane: .38 + Math.random() * .76,
      size: 1.2 + Math.random() * 3.4,
      hue: 190 + Math.random() * 130,
      alpha: .22 + Math.random() * .28
    };
  }
  if (mode === 'comet') {
    const fromLeft = Math.random() > .5;
    const startX = fromLeft ? -60 - Math.random() * width * .2 : width + 60 + Math.random() * width * .2;
    const startY = Math.random() * height * .82;
    const speed = 1.5 + Math.random() * 2.6;
    const angle = fromLeft ? (-.25 + Math.random() * .35) : (Math.PI - .25 + Math.random() * .35);
    return {
      x: startX,
      y: startY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      len: 44 + Math.random() * 88,
      width: 1 + Math.random() * 2.8,
      hue: 190 + Math.random() * 120,
      alpha: .2 + Math.random() * .24,
      wobble: Math.random() * Math.PI * 2,
      wobbleSpeed: .006 + Math.random() * .01
    };
  }
  return {
    x: Math.random() * width,
    y: randomY ? Math.random() * height : -Math.random() * height,
    len: 8 + Math.random() * 10,
    vy: .12 + Math.random() * .12,
    vx: -(0.028 + Math.random() * .055),
    width: .55 + Math.random() * .55,
    alpha: .14 + Math.random() * .16
  };
}

function seasonFxRandomGlyph() {
  const glyphs = '01<>[]{}#@*+=%$';
  return glyphs[Math.floor(Math.random() * glyphs.length)];
}

function seasonFxParticleCountForMode(mode, area, width, height) {
  if (mode === 'snow') return Math.min(560, Math.max(180, Math.round(area / 2100)));
  if (mode === 'cosmos') return Math.min(420, Math.max(160, Math.round(area / 2600)));
  if (mode === 'embers') return Math.min(280, Math.max(90, Math.round(area / 6200)));
  if (mode === 'orbit') return Math.min(160, Math.max(54, Math.round(area / 10800)));
  if (mode === 'prism') return Math.min(240, Math.max(84, Math.round(area / 7200)));
  if (mode === 'pulse') return Math.min(110, Math.max(36, Math.round(area / 16000)));
  if (mode === 'laser') return Math.min(180, Math.max(68, Math.round(area / 9200)));
  if (mode === 'aurora') return Math.min(12, Math.max(6, Math.round(width / 180)));
  if (mode === 'matrix') return Math.min(96, Math.max(54, Math.round(width / 18)));
  if (mode === 'nebula') return Math.min(24, Math.max(14, Math.round(area / 120000)));
  if (mode === 'fireworks') return Math.min(220, Math.max(120, Math.round(area / 5200)));
  if (mode === 'vortex') return Math.min(180, Math.max(80, Math.round(area / 9000)));
  if (mode === 'comet') return Math.min(150, Math.max(54, Math.round(area / 11000)));
  return Math.min(360, Math.max(140, Math.round(area / 3200)));
}

function seasonFxBuildParticles() {
  if (seasonEffectMode === 'off') {
    seasonalFxParticles = [];
    return;
  }
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  const area = Math.max(1, width * height);
  const count = seasonFxParticleCountForMode(seasonEffectMode, area, width, height);
  seasonalFxParticles = Array.from({length: count}, () => seasonFxSpawn(seasonEffectMode, true));
}

function seasonFxStop() {
  if (seasonalFxFrame) {
    cancelAnimationFrame(seasonalFxFrame);
    seasonalFxFrame = 0;
  }
  seasonalFxLastTs = 0;
  seasonalFxPointer.targetWind = 0;
  seasonalFxPointer.wind = 0;
  if (seasonalFxCtx && seasonalFxCanvas) {
    seasonalFxCtx.clearRect(0, 0, seasonalFxSize.width, seasonalFxSize.height);
  }
}

function seasonFxHandlePointerMove(ev) {
  if (!seasonalFxCanvas || hubDesignMode === 'lite' || document.hidden || seasonEffectMode === 'off') return;
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  const x = Math.max(0, Math.min(width, Number(ev.clientX) || 0));
  const y = Math.max(0, Math.min(height, Number(ev.clientY) || 0));
  const deltaX = seasonalFxPointer.lastMoveTs ? (x - seasonalFxPointer.lastX) : 0;
  const centerBias = ((x / width) - .5) * .03;
  const motionBias = Math.max(-.05, Math.min(.05, deltaX * .0016));
  seasonalFxPointer.x = x;
  seasonalFxPointer.y = y;
  seasonalFxPointer.lastX = x;
  seasonalFxPointer.lastY = y;
  seasonalFxPointer.lastMoveTs = Date.now();
  seasonalFxPointer.active = true;
  seasonalFxPointer.targetWind = centerBias + motionBias;
}

function seasonFxHandlePointerLeave() {
  seasonalFxPointer.active = false;
  seasonalFxPointer.targetWind = 0;
}

function seasonFxStep(ts) {
  if (!seasonalFxCanvas || !seasonalFxCtx || seasonEffectMode === 'off' || hubDesignMode === 'lite' || document.hidden) {
    seasonalFxFrame = 0;
    return;
  }
  if (!seasonalFxLastTs) seasonalFxLastTs = ts;
  const dt = Math.min(40, Math.max(1, ts - seasonalFxLastTs));
  seasonalFxLastTs = ts;
  const {width, height} = seasonalFxSize;
  if (!seasonalFxPointer.active || (Date.now() - seasonalFxPointer.lastMoveTs) > 160) {
    seasonalFxPointer.targetWind *= .92;
    if (Math.abs(seasonalFxPointer.targetWind) < .0008) seasonalFxPointer.targetWind = 0;
  }
  seasonalFxPointer.wind += (seasonalFxPointer.targetWind - seasonalFxPointer.wind) * Math.min(.18, dt / 140);
  seasonalFxCtx.clearRect(0, 0, width, height);
  seasonalFxCtx.globalCompositeOperation = 'source-over';
  seasonalFxCtx.shadowBlur = 0;
  seasonalFxCtx.shadowColor = 'rgba(0,0,0,0)';
  if (seasonEffectMode === 'snow') {
    seasonalFxCtx.fillStyle = 'rgba(255,255,255,.8)';
    for (const p of seasonalFxParticles) {
      p.drift += p.driftSpeed * dt;
      p.y += p.vy * dt;
      p.x += (p.vx + seasonalFxPointer.wind * (.75 + p.r * .22)) * dt + Math.sin(p.drift) * .013 * dt;
      if (p.y > height + 14) {
        Object.assign(p, seasonFxSpawn('snow'));
        p.y = -12 - Math.random() * 40;
      }
      if (p.x < -20) p.x = width + 20;
      if (p.x > width + 20) p.x = -20;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.fillStyle = `rgba(255,255,255,${p.alpha})`;
      seasonalFxCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'rain') {
    seasonalFxCtx.lineCap = 'round';
    for (const p of seasonalFxParticles) {
      p.y += p.vy * dt;
      const rainWind = seasonalFxPointer.wind * .42;
      p.x += (p.vx + rainWind) * dt;
      if (p.y > height + p.len + 20) {
        Object.assign(p, seasonFxSpawn('rain'));
        p.y = -20 - Math.random() * height * .2;
      }
      if (p.x < -40) p.x = width + 40;
      if (p.x > width + 40) p.x = -40;
      seasonalFxCtx.strokeStyle = `rgba(191,229,255,${p.alpha})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(p.x + (p.vx + rainWind * .7) * 18, p.y + p.len);
      seasonalFxCtx.stroke();
    }
  } else if (seasonEffectMode === 'cosmos') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    seasonalFxCtx.fillStyle = 'rgba(255,255,255,.9)';
    const centerX = width * .5;
    const centerY = height * .5;
    for (const p of seasonalFxParticles) {
      p.twinkle += p.twinkleSpeed * dt;
      const driftX = seasonalFxPointer.wind * (18 + p.size * 6);
      const driftY = Math.sin(p.twinkle) * .15;
      p.x += (p.vx + driftX) * dt;
      p.y += (p.vy + driftY) * dt;
      const dx = p.x - centerX;
      const dy = p.y - centerY;
      const dist = Math.hypot(dx, dy);
      if (dist > Math.max(width, height) * .8) {
        Object.assign(p, seasonFxSpawn('cosmos'));
      }
      const alpha = Math.max(.12, Math.min(.95, p.alpha + Math.sin(p.twinkle) * .12));
      seasonalFxCtx.strokeStyle = `rgba(171,205,255,${alpha * .34})`;
      seasonalFxCtx.lineWidth = Math.max(.4, p.size * .55);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(p.x - (p.vx * p.tail + seasonalFxPointer.wind * 4), p.y - (p.vy * p.tail));
      seasonalFxCtx.stroke();
      seasonalFxCtx.beginPath();
      seasonalFxCtx.fillStyle = `rgba(255,255,255,${alpha})`;
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'embers') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .74;
    const influenceRadius = Math.min(width, height) * .34 + 70;
    for (const p of seasonalFxParticles) {
      const prevX = p.x;
      const prevY = p.y;
      p.sway += p.swaySpeed * dt;
      if (seasonalFxPointer.active) {
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / influenceRadius);
        p.vx += ((dx / dist) * pull * .0058 + seasonalFxPointer.wind * .04) * dt;
        p.vy += ((dy / dist) * pull * .0041 - .0002) * dt;
      } else {
        p.vx += seasonalFxPointer.wind * .015 * dt;
        p.vy -= .00038 * dt;
      }
      p.vx *= .988;
      p.vy *= .988;
      p.x += (p.vx + Math.sin(p.sway) * .018) * dt;
      p.y += p.vy * dt;
      if (p.y < -34 || p.x < -80 || p.x > width + 80 || p.y > height + 80) {
        Object.assign(p, seasonFxSpawn('embers'));
        p.y = height + Math.random() * 30;
        p.x = seasonalFxPointer.active ? focusX + (Math.random() - .5) * 120 : Math.random() * width;
      }
      const glow = seasonalFxPointer.active
        ? Math.max(0, 1 - Math.hypot(focusX - p.x, focusY - p.y) / 150)
        : 0;
      const alpha = Math.min(.96, p.alpha + glow * .4 + Math.abs(Math.sin(p.sway)) * .08);
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .56})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .58);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 8},100%,66%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'orbit') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = (seasonalFxPointer.active ? seasonalFxPointer.x : width * .5) + seasonalFxPointer.wind * width * .06;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .42;
    const core = seasonalFxCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, Math.min(width, height) * .16);
    core.addColorStop(0, 'rgba(34,211,238,.14)');
    core.addColorStop(.45, 'rgba(168,85,247,.08)');
    core.addColorStop(1, 'rgba(15,23,42,0)');
    seasonalFxCtx.fillStyle = core;
    seasonalFxCtx.beginPath();
    seasonalFxCtx.arc(centerX, centerY, Math.min(width, height) * .16, 0, Math.PI * 2);
    seasonalFxCtx.fill();
    for (const p of seasonalFxParticles) {
      const prevX = p.x || centerX;
      const prevY = p.y || centerY;
      p.radiusDrift += p.radiusSpeed * dt;
      p.angle += p.speed * dt * (1 + Math.min(.8, Math.abs(seasonalFxPointer.wind) * 10));
      const radius = p.radius + Math.sin(p.radiusDrift) * p.radiusSwing;
      p.x = centerX + Math.cos(p.angle) * radius;
      p.y = centerY + Math.sin(p.angle * p.lane) * radius * (.42 + p.lane * .22);
      const alpha = Math.max(.14, Math.min(.92, p.alpha + Math.sin(p.radiusDrift) * .1));
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .42})`;
      seasonalFxCtx.lineWidth = Math.max(.6, p.size * .52);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      if (p.size > 1.7) {
        seasonalFxCtx.strokeStyle = `hsla(${p.hue + 18},100%,74%,${alpha * .18})`;
        seasonalFxCtx.lineWidth = .55;
        seasonalFxCtx.beginPath();
        seasonalFxCtx.moveTo(centerX, centerY);
        seasonalFxCtx.lineTo(p.x, p.y);
        seasonalFxCtx.stroke();
      }
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 12},100%,76%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'prism') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .46;
    const pullRadius = Math.min(width, height) * .38 + 90;
    for (const p of seasonalFxParticles) {
      const prevX = p.x;
      const prevY = p.y;
      p.rot += p.rotSpeed * dt;
      if (seasonalFxPointer.active) {
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / pullRadius);
        p.vx += ((dx / dist) * pull * .0052 + seasonalFxPointer.wind * .022) * dt;
        p.vy += ((dy / dist) * pull * .0044) * dt;
      } else {
        p.vx += seasonalFxPointer.wind * .01 * dt;
        p.vy += Math.sin(p.rot) * .0009 * dt;
      }
      p.vx *= .992;
      p.vy *= .992;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.x < -90 || p.x > width + 90 || p.y < -90 || p.y > height + 90) {
        Object.assign(p, seasonFxSpawn('prism', true));
        if (seasonalFxPointer.active) {
          p.x = focusX + (Math.random() - .5) * 140;
          p.y = focusY + (Math.random() - .5) * 140;
        }
      }
      const alpha = Math.min(.96, p.alpha + Math.abs(Math.sin(p.rot * 1.6)) * .16);
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,76%,${alpha * .34})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .16);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.save();
      seasonalFxCtx.translate(p.x, p.y);
      seasonalFxCtx.rotate(p.rot);
      seasonalFxCtx.fillStyle = `hsla(${p.hue},100%,64%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(0, -p.size);
      seasonalFxCtx.lineTo(p.size * .8, 0);
      seasonalFxCtx.lineTo(0, p.size);
      seasonalFxCtx.lineTo(-p.size * .8, 0);
      seasonalFxCtx.closePath();
      seasonalFxCtx.fill();
      seasonalFxCtx.restore();
    }
  } else if (seasonEffectMode === 'pulse') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .48;
    const maxRadius = Math.min(width, height) * .34 + 50;
    for (const p of seasonalFxParticles) {
      p.phase += p.phaseSpeed * dt;
      p.orbit += p.orbitSpeed * dt * (1 + Math.min(1.2, Math.abs(seasonalFxPointer.wind) * 12));
      p.radius += p.radiusSpeed * dt;
      if (p.radius > maxRadius) {
        Object.assign(p, seasonFxSpawn('pulse', true));
        p.radius = Math.random() * 18;
      }
      const offsetX = Math.cos(p.orbit) * p.radius;
      const offsetY = Math.sin(p.orbit * 1.18) * p.radius * .7;
      const x = centerX + offsetX;
      const y = centerY + offsetY;
      const life = 1 - Math.min(1, p.radius / maxRadius);
      const pulse = .45 + .55 * Math.abs(Math.sin(p.phase));
      const alpha = Math.max(.05, Math.min(.92, p.alpha * life + pulse * .08));
      const ring = Math.max(2, p.size * (.45 + (1 - life) * 1.6));
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,74%,${alpha * .82})`;
      seasonalFxCtx.lineWidth = Math.max(.7, 1.1 + (1 - life) * 2.2);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(x, y, ring, 0, Math.PI * 2);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 24},100%,76%,${alpha * .72})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(x, y, Math.max(.8, ring * .18), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'aurora') {
    seasonalFxCtx.globalCompositeOperation = 'screen';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const baseY = height * .16;
    for (let i = 0; i < seasonalFxParticles.length; i++) {
      const p = seasonalFxParticles[i];
      p.phase += p.phaseSpeed * dt;
      p.sway += p.swaySpeed * dt;
      p.x += seasonalFxPointer.wind * dt * 36 + p.vx * dt * 10;
      p.y += p.vy * dt * 10;
      p.wave += p.waveSpeed * dt;
      if (p.x < -160) p.x = width + 160;
      if (p.x > width + 160) p.x = -160;
      const amp = p.wave * (1 + Math.sin(p.sway) * .3);
      const yBase = baseY + (i % 3) * height * .09 + Math.sin(p.phase * .7 + i * .42) * amp * .12;
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,70%,${p.alpha * .28})`;
      seasonalFxCtx.lineWidth = p.thickness * 3.2;
      seasonalFxCtx.beginPath();
      for (let s = 0; s <= 16; s++) {
        const x = width * s / 16;
        const y = yBase + Math.sin(p.phase + s * .58 + p.sway) * amp + Math.cos(p.phase * .6 + s * .2) * amp * .18;
        if (s === 0) seasonalFxCtx.moveTo(x, y);
        else seasonalFxCtx.lineTo(x, y);
      }
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${p.hue + 18},100%,82%,${p.alpha * .66})`;
      seasonalFxCtx.lineWidth = Math.max(1, p.thickness * .52);
      seasonalFxCtx.beginPath();
      for (let s = 0; s <= 16; s++) {
        const x = width * s / 16;
        const y = yBase + Math.sin(p.phase + s * .58 + p.sway) * amp + Math.cos(p.phase * .6 + s * .2) * amp * .18;
        if (s === 0) seasonalFxCtx.moveTo(x, y);
        else seasonalFxCtx.lineTo(x, y);
      }
      seasonalFxCtx.stroke();
    }
  } else if (seasonEffectMode === 'matrix') {
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.shadowColor = 'rgba(80,255,120,.55)';
    seasonalFxCtx.shadowBlur = 10;
    seasonalFxCtx.textAlign = 'center';
    seasonalFxCtx.textBaseline = 'middle';
    for (const p of seasonalFxParticles) {
      if (Math.random() < .08) p.glyph = seasonFxRandomGlyph();
      p.y += p.vy * dt;
      p.x += seasonalFxPointer.wind * dt * 8;
      if (p.y > height + p.trail) {
        Object.assign(p, seasonFxSpawn('matrix'));
        p.y = -Math.random() * height * .34;
      }
      if (p.x < -40) p.x = width + 40;
      if (p.x > width + 40) p.x = -40;
      seasonalFxCtx.fillStyle = `rgba(70,255,130,${p.trailAlpha})`;
      seasonalFxCtx.fillRect(p.x - .8, p.y - p.trail, 1.6, p.trail + 6);
      seasonalFxCtx.font = `900 ${Math.max(12, Math.round(p.size))}px ui-monospace, SFMono-Regular, Consolas, monospace`;
      seasonalFxCtx.fillStyle = `rgba(170,255,190,${p.alpha})`;
      seasonalFxCtx.fillText(p.glyph, p.x, p.y);
    }
  } else if (seasonEffectMode === 'nebula') {
    seasonalFxCtx.globalCompositeOperation = 'screen';
    for (const p of seasonalFxParticles) {
      p.drift += p.driftSpeed * dt;
      p.pulse += .0006 * dt;
      p.x += (p.vx + seasonalFxPointer.wind * .024) * dt;
      p.y += p.vy * dt;
      if (p.x < -p.size) p.x = width + p.size;
      if (p.x > width + p.size) p.x = -p.size;
      if (p.y < -p.size) p.y = height + p.size;
      if (p.y > height + p.size) p.y = -p.size;
      const alpha = Math.min(.22, p.alpha + Math.sin(p.drift) * .03 + Math.abs(Math.sin(p.pulse)) * .02);
      const glow = seasonalFxCtx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size);
      glow.addColorStop(0, `hsla(${p.hue},100%,72%,${alpha})`);
      glow.addColorStop(.35, `hsla(${p.hue + 18},100%,60%,${alpha * .44})`);
      glow.addColorStop(1, 'rgba(0,0,0,0)');
      seasonalFxCtx.fillStyle = glow;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'fireworks') {
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    for (const p of seasonalFxParticles) {
      const prevX = p.x;
      const prevY = p.y;
      p.sparkle += p.sparkleSpeed * dt;
      p.vx *= Math.pow(p.drag, dt / 16);
      p.vy = p.vy * Math.pow(p.drag, dt / 16) + p.gravity * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= dt / 1200;
      if (p.life <= 0 || p.x < -80 || p.x > width + 80 || p.y > height + 120) {
        Object.assign(p, seasonFxSpawn('fireworks'));
        continue;
      }
      const spark = .5 + .5 * Math.sin(p.sparkle);
      const alpha = Math.min(.96, p.alpha * Math.max(.2, p.life) + Math.abs(spark) * .18);
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .84})`;
      seasonalFxCtx.lineWidth = Math.max(.6, p.size * .6);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${p.hue + 22},100%,86%,${alpha * .34})`;
      seasonalFxCtx.lineWidth = Math.max(1.2, p.size * 2.8);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 14},100%,88%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(.8, p.size * .95), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'vortex') {
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .5;
    for (const p of seasonalFxParticles) {
      const prevX = p.x || centerX;
      const prevY = p.y || centerY;
      p.radius += p.radiusSpeed * dt;
      p.angle += p.spin * dt * (1 + Math.min(1.4, Math.abs(seasonalFxPointer.wind) * 10));
      p.wobble += p.wobbleSpeed * dt;
      const radius = p.radius + Math.sin(p.wobble) * 20;
      p.x = centerX + Math.cos(p.angle) * radius;
      p.y = centerY + Math.sin(p.angle * p.lane) * radius * .84;
      if (radius > Math.max(width, height) * .6) {
        Object.assign(p, seasonFxSpawn('vortex'));
      }
      const alpha = Math.min(.92, p.alpha + Math.abs(Math.sin(p.wobble)) * .14);
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .22})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .74);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${p.hue + 22},100%,84%,${alpha * .14})`;
      seasonalFxCtx.lineWidth = Math.max(1.4, p.size * 2.8);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(centerX, centerY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 12},100%,86%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(.8, p.size), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'comet') {
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    for (const p of seasonalFxParticles) {
      const prevX = p.x;
      const prevY = p.y;
      p.wobble += p.wobbleSpeed * dt;
      p.x += (p.vx + seasonalFxPointer.wind * .03) * dt;
      p.y += (p.vy + Math.sin(p.wobble) * .012) * dt;
      if (p.x < -width * .4 || p.x > width * 1.4 || p.y < -120 || p.y > height + 120) {
        Object.assign(p, seasonFxSpawn('comet'));
        continue;
      }
      const tailX = p.x - p.vx * p.len * .62;
      const tailY = p.y - p.vy * p.len * .62;
      const alpha = Math.min(.95, p.alpha + Math.abs(Math.sin(p.wobble)) * .14);
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .84})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${p.hue + 18},100%,86%,${alpha * .22})`;
      seasonalFxCtx.lineWidth = p.width * 3.6;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${p.hue + 12},100%,88%,${alpha})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(1, p.width * 1.2), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }
  } else if (seasonEffectMode === 'laser') {
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .52;
    const influence = Math.min(width, height) * .42 + 120;
    for (const p of seasonalFxParticles) {
      p.pulse += p.pulseSpeed * dt;
      if (seasonalFxPointer.active) {
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / influence);
        p.vx += (dx / dist) * pull * .008 * dt;
        p.vy += (dy / dist) * pull * .006 * dt;
      } else {
        p.vx += seasonalFxPointer.wind * .02 * dt;
      }
      p.vx *= .996;
      p.vy *= .995;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.x < -width * .35 || p.x > width * 1.35 || p.y < -120 || p.y > height + 120) {
        Object.assign(p, seasonFxSpawn('laser', true));
      }
      const alpha = Math.min(.94, p.alpha + Math.abs(Math.sin(p.pulse)) * .18);
      const tailX = p.x - p.vx * p.len * .55;
      const tailY = p.y - p.vy * p.len * .55;
      seasonalFxCtx.strokeStyle = `hsla(${p.hue},100%,72%,${alpha * .9})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${p.hue + 28},100%,86%,${alpha * .34})`;
      seasonalFxCtx.lineWidth = p.width * 3.2;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
    }
  }
  seasonalFxFrame = requestAnimationFrame(seasonFxStep);
}

function seasonFxStart() {
  if (!seasonalFxCanvas || seasonEffectMode === 'off' || hubDesignMode === 'lite' || document.hidden) return;
  seasonalFxCanvas.hidden = false;
  seasonFxResetCanvas();
  seasonFxBuildParticles();
  seasonFxStop();
  seasonalFxFrame = requestAnimationFrame(seasonFxStep);
}

function setSeasonEffectMode(nextMode, options = {}) {
  const mode = SEASON_EFFECT_MODES.has(nextMode) ? nextMode : 'off';
  seasonEffectMode = mode;
  document.documentElement.dataset.seasonEffect = mode;
  if (options.persist !== false) saveSeasonEffectMode(mode);
  syncSeasonButtons();
  if (!seasonalFxCanvas) return;
  if (mode === 'off' || hubDesignMode === 'lite') {
    seasonalFxCanvas.hidden = true;
    seasonFxStop();
    return;
  }
  seasonalFxCanvas.hidden = false;
  seasonFxStart();
}

function initSeasonEffects() {
  if (!seasonalFxCanvas || seasonEffectsInitialized) return;
  seasonEffectsInitialized = true;
  seasonSwitches.forEach((el) => {
    const columns = Math.min(3, Math.max(2, el.querySelectorAll('[data-season-mode]').length));
    el.style.gridTemplateColumns = `repeat(${columns},minmax(0,1fr))`;
  });
  window.addEventListener('resize', () => {
    clearTimeout(seasonalFxResizeTimer);
    seasonalFxResizeTimer = setTimeout(() => {
      seasonalFxResizeTimer = 0;
      if (seasonEffectMode === 'off' || hubDesignMode === 'lite' || document.hidden) return;
      seasonFxResetCanvas();
      seasonFxBuildParticles();
    }, 120);
  });
  window.addEventListener('pointermove', seasonFxHandlePointerMove, {passive: true});
  window.addEventListener('pointerleave', seasonFxHandlePointerLeave);
  window.addEventListener('blur', seasonFxHandlePointerLeave);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) seasonFxStop();
    else seasonFxStart();
  });
  window.addEventListener('pagehide', seasonFxStop);
  window.addEventListener('pageshow', () => {
    if (!seasonalFxFrame) seasonFxStart();
  });
  seasonEffectMode = loadSeasonEffectMode();
  syncSeasonButtons();
  setSeasonEffectMode(seasonEffectMode, {persist: false});
}


function initHubAppearance() {
  if (hubAppearanceInitialized || !document.body) return;
  hubAppearanceInitialized = true;
  seasonalFxCanvas = document.getElementById('seasonalFx');
  if (!seasonalFxCanvas) {
    document.documentElement.dataset.hubSharedEffects = '1';
    seasonalFxCanvas = document.createElement('canvas');
    seasonalFxCanvas.id = 'seasonalFx';
    seasonalFxCanvas.className = 'seasonalFx';
    seasonalFxCanvas.width = seasonalFxCanvas.height = 1;
    seasonalFxCanvas.hidden = true;
    seasonalFxCanvas.setAttribute('aria-hidden', 'true');
    document.body.prepend(seasonalFxCanvas);
  }
  seasonButtons = Array.from(document.querySelectorAll('[data-season-mode]'));
  seasonSwitches = Array.from(document.querySelectorAll('.seasonSwitch'));
  seasonButtons.forEach(button => button.addEventListener('click', () => {
    setSeasonEffectMode(button.dataset.seasonMode || 'off');
  }));
  initHubDesign();
  initSeasonEffects();
}
function bootHubAppearance() {
  if (hubAppearanceInitialized) return;
  // Wait for the parsed body so the dashboard's own canvas is reused. The
  // interactive state precedes completion of deferred terminal libraries.
  if (!document.body || document.readyState === 'loading') {
    setTimeout(bootHubAppearance, 25);
    return;
  }
  initHubAppearance();
}
bootHubAppearance();
