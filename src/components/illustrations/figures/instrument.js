import * as THREE from 'three'
// three r186 keeps the r128 look: colours are used as given (no sRGB decode) and written without an sRGB encode.
THREE.ColorManagement.enabled = false
import { createFigureRuntime } from './figureRuntime.js'

/**
 * Live instrument: a three.js bar chart drawn as an axonometric line drawing
 * (tonal faces + screen-space ink edges). 1,000,000 example orders are generated
 * as typed column arrays and re-aggregated in the browser on every control
 * change; drag to turn, click a bar to cross-filter the totals.
 * mount(root) returns a cleanup that stops the loop and frees WebGL.
 */
export function mount(root) {
  const cleanups = []
  let alive = true
  const setTimeout = (f, ms) => { const id = window.setTimeout(() => alive && f(), ms); cleanups.push(() => window.clearTimeout(id)); return id }
  const ResizeObserver = class extends window.ResizeObserver { constructor(cb) { super(cb); cleanups.push(() => this.disconnect()) } }
  const IntersectionObserver = class extends window.IntersectionObserver { constructor(cb, o) { super(cb, o); cleanups.push(() => this.disconnect()) } }
  const MutationObserver = class extends window.MutationObserver { constructor(cb) { super(cb); cleanups.push(() => this.disconnect()) } }
  root.querySelectorAll('#heroStage canvas').forEach(c => c.remove())
  // Live query: toggling the OS setting mid-session takes effect on the next frame.
  const RMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));

  /* ───────── Palette from CSS tokens ───────── */
  const PAL = { face: new THREE.Color(), line: new THREE.Color(), acc: new THREE.Color(), soft: new THREE.Color(), fill: new THREE.Color(), tone: new THREE.Vector3() };
  const COL = ['face', 'acc', 'soft', 'fill'];
  const stages = [];
  function readPalette() {
    const cs = getComputedStyle(root.querySelector('.inst')), g = n => cs.getPropertyValue(n).trim();
    PAL.face.set(g('--g-face')); PAL.line.set(g('--g-line')); PAL.acc.set(g('--g-acc')); PAL.soft.set(g('--g-soft')); PAL.fill.set(g('--g-fill'));
    const t = g('--g-tone').split(',').map(Number); PAL.tone.set(t[0], t[1], t[2]);
    stages.forEach(s => s.recolor());
  }

  /* ───────── Axonometric line-drawing material: tonal faces + screen-space ink edges ───────── */
  const VS = `
    attribute vec3 aColor; varying vec2 vUv; varying vec3 vN; varying vec3 vC;
    void main() {
      vUv = uv; vC = aColor;
      mat4 m = modelMatrix;
      #ifdef USE_INSTANCING
        m = m * instanceMatrix;
      #endif
      vN = normalize(mat3(m) * normal);
      gl_Position = projectionMatrix * viewMatrix * m * vec4(position, 1.0);
    }`;
  const FS = `
    uniform vec3 uLine; uniform vec3 uTone; uniform float uW;
    varying vec2 vUv; varying vec3 vN; varying vec3 vC;
    void main() {
      float t = vN.y > .5 ? uTone.x : (abs(vN.x) > .5 ? uTone.y : uTone.z);
      vec3 c = clamp(vC * t, 0., 1.);
      vec2 d = min(vUv, 1. - vUv), w = max(fwidth(vUv), vec2(1e-5));
      float px = min(d.x / w.x, d.y / w.y);
      float e = 1. - smoothstep(uW - .5, uW + .5, px);
      gl_FragColor = vec4(mix(c, uLine, e), 1.);
    }`;
  const makeMat = w => new THREE.ShaderMaterial({ vertexShader: VS, fragmentShader: FS, 
    uniforms: { uLine: { value: PAL.line }, uTone: { value: PAL.tone }, uW: { value: w } } });

  /* ───────── Stage: one canvas, one instanced box set, eased per-instance targets, drag to turn ───────── */
  class Stage {
    constructor(el, o) {
      this.el = el; this.o = o; this.n = o.count;
      this.canvas = document.createElement('canvas'); el.prepend(this.canvas);
      this.lblBox = el.querySelector('.lbls');
      this.r = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true });
      this.r.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
      this.r.outputColorSpace = THREE.LinearSRGBColorSpace;
      this.scene = new THREE.Scene();
      this.cam = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, 500);
      const geo = new THREE.BoxGeometry(1, 1, 1);
      this.color = new Float32Array(this.n * 3);
      geo.setAttribute('aColor', new THREE.InstancedBufferAttribute(this.color, 3));
      this.mesh = new THREE.InstancedMesh(geo, makeMat(o.lineW || 1.1), this.n);
      this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.scene.add(this.mesh);
      const n = this.n;
      this.cur = new Float32Array(n * 6); this.tgt = new Float32Array(n * 6);
      this.cc = new Float32Array(n * 3); this.ct = new Float32Array(n * 3);
      this.cidx = new Uint8Array(n); this.cmix = new Float32Array(n); this.start = new Float64Array(n);
      for (let i = 0; i < n; i++) { this.cur[i * 6 + 3] = this.cur[i * 6 + 4] = this.cur[i * 6 + 5] = .001; }
      this.az = this.taz = o.az; this.el_ = this.tel = o.el; this.labels = [];
      this.dirty = true; this.dummy = new THREE.Object3D();
      this.bindDrag(); this.resize();
      // Render on demand: the runtime only calls tick() while something moves or is dirty,
      // pauses offscreen / on hidden tabs, follows size + DPR changes and recovers a lost context.
      this.rt = createFigureRuntime({
        el, canvas: this.canvas,
        frame: now => this.tick(now),
        resize: (w, h, dpr) => { this.r.setPixelRatio(dpr); this.resize() },
        restore: () => { this.mesh.instanceMatrix.needsUpdate = true; this.mesh.geometry.attributes.aColor.needsUpdate = true; this.dirty = true },
      });
      cleanups.push(() => this.rt.dispose());
      stages.push(this);
    }
    get dirty() { return this._dirty }
    set dirty(v) { this._dirty = v; if (v && this.rt) this.rt.invalidate() }
    resize() {
      const w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
      this.w = w; this.h = h; this.r.setSize(w, h, false);
      const fh = this.o.frame * (w / h < 1.1 ? 1.25 : 1), a = w / h;
      Object.assign(this.cam, { left: -fh * a / 2, right: fh * a / 2, top: fh / 2, bottom: -fh / 2 });
      this.cam.updateProjectionMatrix(); this.dirty = true;
    }
    bindDrag() {
      const el = this.el; let down = null;
      el.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY, az: this.taz, el: this.tel, moved: false }; el.setPointerCapture(e.pointerId); });
      el.addEventListener('pointermove', e => {
        if (down) { const dx = e.clientX - down.x, dy = e.clientY - down.y;
          if (Math.abs(dx) + Math.abs(dy) > 4) { down.moved = true; el.classList.add('drag'); }
          this.taz = down.az - dx * .008; this.tel = clamp(down.el + dy * .006, .12, 1.25); this.dirty = true; }
        this.onHover && this.onHover(e, !!(down && down.moved));
      });
      const up = e => { if (down && !down.moved && this.onClick) this.onClick(e); down = null; el.classList.remove('drag'); };
      el.addEventListener('pointerup', up); el.addEventListener('pointercancel', () => { down = null; el.classList.remove('drag'); });
      el.addEventListener('pointerleave', () => this.onLeave && this.onLeave());
    }
    set(i, x, y, z, sx, sy, sz, c = 0, mix = 0, delay = 0) {
      const t = this.tgt, k = i * 6; t[k] = x; t[k + 1] = y; t[k + 2] = z; t[k + 3] = sx; t[k + 4] = sy; t[k + 5] = sz;
      this.cidx[i] = c; this.cmix[i] = mix; this.start[i] = performance.now() + (RMQ.matches ? 0 : delay); this.resolve(i); this.dirty = true;
    }
    hide(i, delay = 0) { const k = i * 6; this.set(i, this.tgt[k], 0, this.tgt[k + 2], .001, .001, .001, this.cidx[i], 0, delay); }
    resolve(i) { const base = PAL[COL[this.cidx[i]]], m = this.cmix[i]; const c = base.clone(); if (m) c.lerp(PAL.acc, m); this.ct[i * 3] = c.r; this.ct[i * 3 + 1] = c.g; this.ct[i * 3 + 2] = c.b; }
    recolor() { for (let i = 0; i < this.n; i++) this.resolve(i); this.dirty = true; }
    snap() { this.cur.set(this.tgt); this.cc.set(this.ct); this.dirty = true; }
    label(html, pos, cls = '') { const el = document.createElement('div'); el.className = 'lbl ' + cls; el.innerHTML = html; this.lblBox.appendChild(el); const L = { el, pos, show: true }; this.labels.push(L); return L; }
    clearLabels() { this.labels.forEach(L => L.el.remove()); this.labels = []; }
    tick(now) {
      this.az += (this.taz - this.az) * .16; this.el_ += (this.tel - this.el_) * .16;
      let moving = Math.abs(this.taz - this.az) + Math.abs(this.tel - this.el_) > 1e-4;
      const { cur, tgt, cc, ct, n } = this, k = RMQ.matches ? 1 : .14, kc = RMQ.matches ? 1 : .12;
      for (let i = 0; i < n; i++) {
        if (now < this.start[i]) { moving = true; continue; }
        for (let j = i * 6, e = j + 6; j < e; j++) { const d = tgt[j] - cur[j]; if (d > 1e-4 || d < -1e-4) { cur[j] += d * k; moving = true; } else cur[j] = tgt[j]; }
        for (let j = i * 3, e = j + 3; j < e; j++) { const d = ct[j] - cc[j]; if (d > 1e-4 || d < -1e-4) { cc[j] += d * kc; moving = true; } else cc[j] = ct[j]; }
      }
      if (this.o.ambient) moving = this.o.ambient(now) || moving;
      if (!moving && !this.dirty) return false;
      this.dirty = false;
      const tg = this.o.target, dir = new THREE.Vector3(Math.cos(this.el_) * Math.sin(this.az), Math.sin(this.el_), Math.cos(this.el_) * Math.cos(this.az));
      this.cam.position.set(tg[0], tg[1], tg[2]).addScaledVector(dir, 120); this.cam.lookAt(tg[0], tg[1], tg[2]); this.cam.updateMatrixWorld();
      const d = this.dummy;
      for (let i = 0; i < n; i++) { const j = i * 6; d.position.set(cur[j], cur[j + 1], cur[j + 2]); d.scale.set(cur[j + 3] || .001, cur[j + 4] || .001, cur[j + 5] || .001); d.updateMatrix(); this.mesh.setMatrixAt(i, d.matrix); }
      this.color.set(cc); this.mesh.geometry.attributes.aColor.needsUpdate = true; this.mesh.instanceMatrix.needsUpdate = true;
      this.r.render(this.scene, this.cam);
      const v = new THREE.Vector3();
      this.labels.forEach(L => { if (!L.show) { L.el.style.opacity = 0; return; } const p = typeof L.pos === 'function' ? L.pos() : L.pos; v.set(p[0], p[1], p[2]).project(this.cam);
        L.el.style.opacity = 1; L.el.style.left = ((v.x + 1) / 2 * this.w) + 'px'; L.el.style.top = ((1 - v.y) / 2 * this.h) + 'px'; });
      return moving;
    }
  }

  /* ───────── 1,000,000 example orders as typed column arrays ───────── */
  // Neutral sample data: generic retail, USD, four sales regions.
  const STORES = ['AUS','DEN','CHI','TOR','SEA','BOS','LON','BER','MAD','SIN','SYD','TYO','SAO','BOG'];
  const STORE_NAMES = ['Austin','Denver','Chicago','Toronto','Seattle','Boston','London','Berlin','Madrid','Singapore','Sydney','Tokyo','São Paulo','Bogotá'];
  const REGION_OF = new Uint8Array([0,0,0,0,0,0,1,1,1,2,2,2,3,3]);
  const REGIONS = ['AMER','EMEA','APAC','LATAM'], REG_SHORT = ['AMER','EMEA','APAC','LATAM'];
  const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const HOURS = Array.from({ length: 18 }, (_, i) => i + 6);
  const NR = 1000000;
  const cStore = new Uint8Array(NR), cDay = new Uint8Array(NR), cHour = new Uint8Array(NR), cChan = new Uint8Array(NR), cRev = new Float32Array(NR), cWait = new Uint16Array(NR);
  function gen() {
    let s = 2463534242; const r = () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; };
    const cdf = w => { const t = w.reduce((a, b) => a + b, 0); let acc = 0; return w.map(v => (acc += v / t)); };
    const pick = c => { const u = r(); let i = 0; while (i < c.length - 1 && u > c[i]) i++; return i; };
    const sC = cdf([13, 10, 8, 9, 9, 11, 8, 7, 6, 7, 6, 4, 5, 4]), dC = cdf([.82, .85, .9, .95, 1.22, 1.32, 1.05]);
    const hW = [2, 4, 5, 4, 5, 9, 10, 7, 4, 3, 4, 7, 10, 9, 6, 4, 3, 2], hC = cdf(hW), hMax = 10;
    const slow = [95, 45, 10, 0, 15, 30, 0, 10, 0, 20, 25, 0, 5, 0];
    for (let i = 0; i < NR; i++) {
      const st = pick(sC), d = pick(dC), h = pick(hC);
      const dt = .33 + (h < 4 ? .2 : 0) - (h >= 12 && h <= 15 ? .08 : 0), dl = .18 + (h >= 12 && h <= 15 ? .14 : 0) + (d >= 4 ? .04 : 0);
      const u = r(), ch = u < dt ? 1 : u < dt + dl ? 2 : 0;
      cStore[i] = st; cDay[i] = d; cHour[i] = h; cChan[i] = ch;
      cRev[i] = ([66, 54, 82][ch]) * (d >= 4 ? 1.07 : 1) * (.62 + r() * .76);
      if (ch === 1) cWait[i] = 120 + hW[h] / hMax * 110 + slow[st] * (hW[h] / hMax) + r() * 70;
    }
  }

  /* ───────── Hero instrument ───────── */
  const GROUPS = {
    hd: { nx: 18, nz: 7, x: i => cHour[i], z: i => cDay[i], xl: HOURS.map(h => String(h).padStart(2, '0')), zl: DAYS, every: 3, name: (x, z) => `${DAYS[z]} · ${String(HOURS[x]).padStart(2, '0')}:00` },
    hr: { nx: 18, nz: 4, x: i => cHour[i], z: i => REGION_OF[cStore[i]], xl: HOURS.map(h => String(h).padStart(2, '0')), zl: REG_SHORT, every: 3, name: (x, z) => `${REGIONS[z]} · ${String(HOURS[x]).padStart(2, '0')}:00` },
    sd: { nx: 14, nz: 7, x: i => cStore[i], z: i => cDay[i], xl: STORES, zl: DAYS, every: 1, name: (x, z) => `${STORE_NAMES[x]} · ${DAYS[z]}` },
  };
  const state = { measure: 'rev', group: 'hd', chan: -1, sel: null };
  let agg = null;
  const SX = 1.3, SZ = 1.7, HMAX = 7.5;
  const hero = new Stage(root.querySelector('#heroStage'), { count: 18 * 7 + 1, frame: 19, az: .72, el: .52, target: [0, 1.2, 0], lineW: 1.0 });
  const fmtR = v => v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? '$' + (v / 1e3).toFixed(1) + 'k' : '$' + v.toFixed(0);
  const fmtN = v => Math.round(v).toLocaleString("en-US");
  const fmtW = s => s > 0 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '—';

  function aggregate() {
    const G = GROUPS[state.group], nc = G.nx * G.nz, ch = state.chan;
    const cnt = new Float64Array(nc), rev = new Float64Array(nc), ws = new Float64Array(nc), wc = new Float64Array(nc);
    const t0 = performance.now();
    if (state.group === 'hd') { for (let i = 0; i < NR; i++) { if (ch >= 0 && cChan[i] !== ch) continue; const k = cDay[i] * 18 + cHour[i]; cnt[k]++; rev[k] += cRev[i]; const w = cWait[i]; if (w) { ws[k] += w; wc[k]++; } } }
    else if (state.group === 'hr') { for (let i = 0; i < NR; i++) { if (ch >= 0 && cChan[i] !== ch) continue; const k = REGION_OF[cStore[i]] * 18 + cHour[i]; cnt[k]++; rev[k] += cRev[i]; const w = cWait[i]; if (w) { ws[k] += w; wc[k]++; } } }
    else { for (let i = 0; i < NR; i++) { if (ch >= 0 && cChan[i] !== ch) continue; const k = cDay[i] * 14 + cStore[i]; cnt[k]++; rev[k] += cRev[i]; const w = cWait[i]; if (w) { ws[k] += w; wc[k]++; } } }
    const ms = performance.now() - t0;
    agg = { G, cnt, rev, ws, wc, ms };
  }
  function cellVal(k) { const m = state.measure; return m === 'rev' ? agg.rev[k] : m === 'cnt' ? agg.cnt[k] : (agg.wc[k] ? agg.ws[k] / agg.wc[k] : 0); }
  function layoutHero(first) {
    const { G } = agg, nc = G.nx * G.nz; let max = 0;
    for (let k = 0; k < nc; k++) max = Math.max(max, cellVal(k));
    const ox = (G.nx - 1) / 2, oz = (G.nz - 1) / 2;
    for (let k = 0; k < hero.n - 1; k++) {
      if (k >= nc) { hero.hide(k, 0); continue; }
      const x = k % G.nx, z = Math.floor(k / G.nx), v = cellVal(k), h = Math.max(.06, max ? v / max * HMAX : .06);
      const sel = state.sel && state.sel.x === x && state.sel.z === z;
      hero.set(k, (x - ox) * SX, h / 2, (z - oz) * SZ, .86, h, .86 * (SZ / SX) * .78, sel ? 1 : 0, 0, first ? 300 + x * 34 + z * 60 : x * 14 + z * 22);
    }
    const P = hero.n - 1;
    hero.set(P, 0, -.14, 0, G.nx * SX + .7, .28, G.nz * SZ + .5, 0, 0, 0);
    hero.clearLabels();
    G.xl.forEach((t, x) => { if (x % G.every === 0) hero.label(t, [(x - ox) * SX, -.3, (oz + .5) * SZ + .7]); });
    G.zl.forEach((t, z) => hero.label(t, [(ox + .5) * SX + 1.1, -.3, (z - oz) * SZ]));
    hero.label(state.measure === 'rev' ? 'Revenue' : state.measure === 'cnt' ? 'Orders' : 'Avg wait', [-(ox + .5) * SX - .2, HMAX * 1.02, -(oz + .5) * SZ - .2], 'tag');
  }
  function updateKPIs() {
    const { G } = agg; let c = 0, rv = 0, ws = 0, wc = 0;
    const one = state.sel ? [state.sel.z * G.nx + state.sel.x] : null;
    const ks = one || Array.from({ length: G.nx * G.nz }, (_, k) => k);
    ks.forEach(k => { c += agg.cnt[k]; rv += agg.rev[k]; ws += agg.ws[k]; wc += agg.wc[k]; });
    root.querySelector('#kRev').textContent = fmtR(rv);
    root.querySelector('#kCnt').textContent = fmtN(c);
    root.querySelector('#kTkt').textContent = c ? '$' + (rv / c).toFixed(2) : '—';
    root.querySelector('#kWait').textContent = fmtW(wc ? ws / wc : 0);
    root.querySelector('#rScan').textContent = fmtN(NR);
    root.querySelector('#rMs').textContent = agg.ms.toFixed(1) + ' ms';
    let chip = root.querySelector('.sel');
    if (state.sel) {
      if (!chip) { chip = document.createElement('div'); chip.className = 'sel'; root.querySelector('#heroStage').appendChild(chip); }
      chip.innerHTML = `<span>${G.name(state.sel.x, state.sel.z)}</span><button aria-label="Clear filter">×</button>`;
      chip.querySelector('button').onclick = e => { e.stopPropagation(); state.sel = null; layoutHero(); updateKPIs(); };
    } else if (chip) chip.remove();
  }
  function run(first) { aggregate(); layoutHero(first); updateKPIs(); }

  root.querySelectorAll('#inst .seg').forEach(seg => seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b));
    const k = seg.dataset.k; state[k] = k === 'chan' ? +b.dataset.v : b.dataset.v;
    if (k === 'group') state.sel = null;
    if (k === 'measure') { layoutHero(); updateKPIs(); } else run();
  }));

  // Hover and click on bars
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let tip = null, hoverK = -1;
  function pickBar(e) {
    const r = hero.el.getBoundingClientRect(); ndc.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, hero.cam); const hit = ray.intersectObject(hero.mesh)[0];
    const nc = agg ? agg.G.nx * agg.G.nz : 0;
    return hit && hit.instanceId < nc ? hit.instanceId : -1;
  }
  hero.onHover = (e, dragging) => {
    if (!agg) return; const k = dragging ? -1 : pickBar(e);
    if (k !== hoverK) {
      if (hoverK >= 0) { const s = state.sel, x = hoverK % agg.G.nx, z = Math.floor(hoverK / agg.G.nx); hero.cidx[hoverK] = s && s.x === x && s.z === z ? 1 : 0; hero.cmix[hoverK] = 0; hero.resolve(hoverK); }
      if (k >= 0 && hero.cidx[k] !== 1) { hero.cmix[k] = .35; hero.resolve(k); }
      hoverK = k; hero.dirty = true;
    }
    if (k < 0) { if (tip) tip.hidden = true; return; }
    if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; hero.el.appendChild(tip); }
    const r = hero.el.getBoundingClientRect(), x = k % agg.G.nx, z = Math.floor(k / agg.G.nx);
    tip.hidden = false; tip.style.left = (e.clientX - r.left) + 'px'; tip.style.top = (e.clientY - r.top) + 'px';
    tip.innerHTML = `<b>${agg.G.name(x, z)}</b>${fmtR(agg.rev[k])} · ${fmtN(agg.cnt[k])} orders${agg.wc[k] ? ' · wait ' + fmtW(agg.ws[k] / agg.wc[k]) : ''}`;
  };
  hero.onLeave = () => { if (tip) tip.hidden = true; if (hoverK >= 0 && agg) { hero.cmix[hoverK] = 0; hero.resolve(hoverK); hoverK = -1; hero.dirty = true; } };
  hero.onClick = e => {
    if (!agg) return; const k = pickBar(e);
    if (k < 0) { state.sel = null; } else { const x = k % agg.G.nx, z = Math.floor(k / agg.G.nx); state.sel = state.sel && state.sel.x === x && state.sel.z === z ? null : { x, z }; }
    hoverK = -1; layoutHero(); updateKPIs();
  };
  /* ───────── Boot ───────── */
  readPalette();
  new MutationObserver(readPalette).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
  // Generate rows after first paint so the figure appears immediately
  setTimeout(() => {
    const t0 = performance.now(); gen();
    root.querySelector('#rowsLbl').textContent = `1,000,000 ROWS · BUILT IN ${Math.round(performance.now() - t0)} MS`;
    run(true);
  }, 60);

  return () => { alive = false; cleanups.forEach(f => { try { f() } catch { /* already gone */ } }); stages.forEach(s => { s.r.dispose(); s.r.forceContextLoss(); s.canvas.remove() }) }
}
