import * as THREE from 'three'
// three r186 keeps the r128 look: colours are used as given (no sRGB decode) and written without an sRGB encode.
THREE.ColorManagement.enabled = false
import { createFigureRuntime } from './figureRuntime.js'

/**
 * View anatomy: an orbitable wireframe diorama of one dashboard view, from the warehouse
 * through the edge cache into six viewer tabs, told in five steps (query, cache, compute,
 * secure, author). The tabs really aggregate 1,048,576 sample rows in the browser.
 * mount(root) wires the figure to its markup inside `root` and returns a cleanup
 * that stops every loop, listener and observer and frees the WebGL context.
 */
export function mount(root) {
  /* eslint-disable no-unused-vars -- shims shared by every figure module; each uses a subset */
  const cleanups = []
  let alive = true
  const addEventListener = (t, f, o) => { window.addEventListener(t, f, o); cleanups.push(() => window.removeEventListener(t, f, o)) }
  const requestAnimationFrame = f => (alive ? window.requestAnimationFrame(t => { if (alive) f(t) }) : 0)
  const setTimeout = (f, ms) => { const id = window.setTimeout(f, ms); cleanups.push(() => window.clearTimeout(id)); return id }
  const clearTimeout = id => window.clearTimeout(id)
  const setInterval = (f, ms) => { const id = window.setInterval(f, ms); cleanups.push(() => window.clearInterval(id)); return id }
  const ResizeObserver = class extends window.ResizeObserver { constructor(cb) { super(cb); cleanups.push(() => this.disconnect()) } }
  const IntersectionObserver = class extends window.IntersectionObserver { constructor(cb, o) { super(cb, o); cleanups.push(() => this.disconnect()) } }
  const MutationObserver = class extends window.MutationObserver { constructor(cb) { super(cb); cleanups.push(() => this.disconnect()) } }
  const matchMedia = q => { const mq = window.matchMedia(q); return { get matches() { return mq.matches }, addEventListener: (t, f) => { mq.addEventListener(t, f); cleanups.push(() => mq.removeEventListener(t, f)) } } }
  const document = window.document
  /* eslint-enable no-unused-vars */
  { const old = root.querySelector('canvas#gl'); if (old) old.replaceWith(old.cloneNode(false)) }
const $ = s => root.querySelector(s);
const RM = matchMedia('(prefers-reduced-motion: reduce)');
const fmtInt = n => Math.round(n).toLocaleString('en-US');
const css = n => getComputedStyle(root).getPropertyValue(n).trim();
function toast(msg){ const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 2200); }

/* ================= data: 1,048,576 synthetic rows in typed arrays ================= */
const DIMS = {
  prov: [['AMER','Americas'],['EMEA','Europe, Middle East, Africa'],['APAC','Asia Pacific'],['LATAM','Latin America']],
  chan: [['STORE','Store'],['ONLINE','Online'],['WHSL','Wholesale'],['MKT','Marketplace']],
  cat:  [['GROC','Grocery'],['HOME','Home'],['APPRL','Apparel'],['ELEC','Electronics'],['HLTH','Health'],['OUTD','Outdoor']]
};
const W = { prov:[.38,.27,.22,.13], chan:[.46,.28,.17,.09], cat:[.31,.19,.16,.15,.11,.08] };
const CAT_MEAN = [190, 640, 520, 2350, 310, 880];
const N = 1 << 20, BATCH = 4096, NB = N / BATCH;
let seed = 1042;
const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const cum = a => { let s = 0; return a.map(x => s += x); };
const pick = (c, r) => { let i = 0; while (i < c.length - 1 && r > c[i]) i++; return i; };
const col = { prov: new Uint8Array(N), chan: new Uint8Array(N), cat: new Uint8Array(N) }, rev = new Float32Array(N);
(() => { const cp = cum(W.prov), cc = cum(W.chan), ck = cum(W.cat);
  for (let i = 0; i < N; i++) {
    const p = pick(cp, rnd()), c = pick(cc, rnd()), k = pick(ck, rnd());
    col.prov[i] = p; col.chan[i] = c; col.cat[i] = k;
    const u = rnd() + rnd() + rnd();
    rev[i] = CAT_MEAN[k] * (0.25 + u * 0.5) * (c === 2 ? 2.6 : 1);
  }})();
const BYTES_PER_ROW = 3 + 4;
/* ================= shared helpers ================= */
const wait = ms => new Promise(r => setTimeout(r, RM.matches ? Math.min(ms, 60) : ms));
const canon = o => Array.isArray(o) ? '[' + o.map(canon).join(',') + ']' : o && typeof o === 'object' ? '{' + Object.keys(o).sort().map(k => JSON.stringify(k) + ':' + canon(o[k])).join(',') + '}' : JSON.stringify(o);
async function sha(s){
  try { const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)); return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join(''); }
  catch { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16).padStart(8, '0').repeat(8); }
}

/* ================= aggregation over the 1M rows ================= */
const provCount = new Int32Array(DIMS.prov.length); for (let i = 0; i < N; i++) provCount[col.prov[i]]++;
function aggregate(dim, region){
  const t0 = performance.now(), d = col[dim], p = col.prov, out = new Float64Array(DIMS[dim].length);
  if (region < 0) for (let i = 0; i < N; i++) out[d[i]] += rev[i];
  else for (let i = 0; i < N; i++) if (p[i] === region) out[d[i]] += rev[i];
  return { v:out, ms:performance.now() - t0, rows: region < 0 ? N : provCount[region] };
}
const aggMemo = new Map();
const agg = (dim, region) => { const k = dim + '|' + region; if (!aggMemo.has(k)) aggMemo.set(k, aggregate(dim, region)); return aggMemo.get(k); };

/* ================= story diorama ================= */
const T = THREE;
const stage = $('#stage'), cv = $('#gl'), annoEl = $('#anno'), leadEl = $('#leaders');
const S = { ch:0, epoch:0, dim:'prov', viewers:0, queries:0, hits:0, keys:new Set(), edgeT:0, edge:0, wHot:0, eHot:0,
  agent:0, agentV:0, focus:'W', lastMs:null, lastRows:0, auto:true, chDone:false, chT0:0, onscreen:true, layoutN:1, keyHash:'' };
const REG = [0, 1, 2, 0, 1, 3];
const TW = 140, TH = 96;
const TAB_POS = [[40,20,120],[210,20,120],[380,20,120],[90,150,-40],[260,150,-40],[430,150,-40]];
const PLACE = [.55,.85,.4,.7,.5,.78,.35,.6,.45];
const LAYOUTS = [{ kpi:0, hmax:64 }, { kpi:2, hmax:42 }, { kpi:3, hmax:42 }];
let renderer, scene, camera, rt = null, ok = false;
const wake = (ms = 600) => { if (rt) rt.invalidate(ms) };
const lineMats = [], hits = [], routes = {};
const tabs = TAB_POS.map(() => ({ shown:0, vis:0, filled:0, region:-1, dataRegion:-1, layout:0, kpiFilled:0, kv:new Float32Array(3),
  h:new Float32Array(9), gh:new Float32Array(9), scan:-1, scanDelay:0, g:null, mats:[], tok:null }));
let Wp, Ep, Ap, topW, topE;
const Ccol = {};

try { const t = document.createElement('canvas'); ok = !!(T && (t.getContext('webgl2') || t.getContext('webgl'))); } catch { ok = false; }

function readColors(){
  for (const [k, v] of [['ink','--ink'],['ink2','--ink-2'],['ink3','--ink-3'],['hair','--hair'],['acc','--accent'],['accS','--accent-soft'],['paper','--paper'],['sheet','--sheet']]) Ccol[k] = new T.Color(css(v));
  if (renderer) renderer.setClearColor(Ccol.paper, 1);
  tabs.forEach(t => t.screen && t.screen.material.color.copy(Ccol.sheet));
}

const SK_V = `attribute vec3 iEdge; attribute vec4 iFill; attribute float iEA;
varying vec2 vUv; varying vec3 vE; varying vec4 vF; varying float vA;
void main(){ vUv = uv; vE = iEdge; vF = iFill; vA = iEA; gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0); }`;
const SK_F = `uniform float uBack; varying vec2 vUv; varying vec3 vE; varying vec4 vF; varying float vA;
void main(){
  vec2 fw = fwidth(vUv); vec2 a = min(vUv, 1.0 - vUv) / max(fw, vec2(1e-5));
  float e = 1.0 - smoothstep(0.55, 1.5, min(a.x, a.y));
  float dashed = (vA < 0.0 || uBack > 0.5) ? step(0.45, fract((gl_FragCoord.x + gl_FragCoord.y) / 9.0)) : 1.0;
  float ea = e * abs(vA) * dashed * (uBack > 0.5 ? 0.45 : 1.0);
  float fa = uBack > 0.5 ? 0.0 : vF.a;
  float al = max(fa, ea); if (al < 0.01) discard;
  gl_FragColor = vec4(mix(vF.rgb, vE, ea / al), al);
}`;
class Boxes {
  constructor(n){
    const g = new T.BoxGeometry(1, 1, 1);
    this.E = new Float32Array(n * 3); this.F = new Float32Array(n * 4); this.A = new Float32Array(n);
    this.aE = new T.InstancedBufferAttribute(this.E, 3); this.aF = new T.InstancedBufferAttribute(this.F, 4); this.aA = new T.InstancedBufferAttribute(this.A, 1);
    [this.aE, this.aF, this.aA].forEach(a => a.setUsage(T.DynamicDrawUsage));
    g.setAttribute('iEdge', this.aE); g.setAttribute('iFill', this.aF); g.setAttribute('iEA', this.aA);
    const mk = back => new T.ShaderMaterial({ vertexShader:SK_V, fragmentShader:SK_F, uniforms:{ uBack:{ value:back } }, transparent:true,
      side: back ? T.BackSide : T.FrontSide, depthWrite: !back });
    this.back = new T.InstancedMesh(g, mk(1), n); this.front = new T.InstancedMesh(g, mk(0), n);
    this.back.instanceMatrix.setUsage(T.DynamicDrawUsage); this.front.instanceMatrix = this.back.instanceMatrix;
    for (const m of [this.back, this.front]) { m.frustumCulled = false; m.count = 0; scene.add(m); }
    this.back.renderOrder = 2; this.front.renderOrder = 3; this.n = n; this.i = 0;
  }
  begin(){ this.i = 0; }
  add(m, edge, ea, fill, fa){
    const i = this.i++; if (i >= this.n) return;
    m.toArray(this.back.instanceMatrix.array, i * 16);
    this.E[i*3] = edge.r; this.E[i*3+1] = edge.g; this.E[i*3+2] = edge.b;
    this.F[i*4] = fill.r; this.F[i*4+1] = fill.g; this.F[i*4+2] = fill.b; this.F[i*4+3] = fa; this.A[i] = ea;
  }
  end(){ const c = Math.min(this.i, this.n); this.back.count = this.front.count = c; this.back.instanceMatrix.needsUpdate = true; this.aE.needsUpdate = this.aF.needsUpdate = this.aA.needsUpdate = true; }
}
let BX;
const V3 = (x, y, z) => new T.Vector3(x, y, z);
const M4 = () => new T.Matrix4();
const _m = new T.Matrix4(), _q = new T.Quaternion(), _p = new T.Vector3(), _s = new T.Vector3(), _e = new T.Euler();
function boxM(parent, x, y, z, sx, sy, sz, ry){
  _e.set(0, ry || 0, 0); _q.setFromEuler(_e);
  _m.compose(_p.set(x, y, z), _q, _s.set(Math.max(sx, .01), Math.max(sy, .01), Math.max(sz, .01)));
  if (parent) _m.premultiply(parent.matrixWorld);
  return _m;
}
function segs(arr){ const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(arr, 3)); return g; }
function lmat(role, extra){ const m = new T.LineBasicMaterial({ color:0, transparent:true, opacity:1 }); lineMats.push(Object.assign({ m, role }, extra || {})); return m; }
function hitMesh(geo, tag, parent, pos){ const m = new T.Mesh(geo, new T.MeshBasicMaterial({ transparent:true, opacity:0, depthWrite:false, colorWrite:false })); m.userData.hit = tag; if (pos) m.position.copy(pos); (parent || scene).add(m); hits.push(m); return m; }
function curve(a, b, lift){ const m = a.clone().lerp(b, .5); m.y = Math.max(a.y, b.y) + lift; return new T.QuadraticBezierCurve3(a, m, b); }
const tabTop = i => V3(TAB_POS[i][0], TAB_POS[i][1] + TH + 3, TAB_POS[i][2]);

function build(){
  renderer = new T.WebGLRenderer({ canvas:cv, antialias:true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = T.LinearSRGBColorSpace;
  scene = new T.Scene();
  camera = new T.PerspectiveCamera(30, 1, 10, 20000);
  readColors();
  // ground grid
  const g = [];
  for (let x = -520; x <= 560; x += 40) g.push(x, 0, -300, x, 0, 320);
  for (let z = -300; z <= 320; z += 40) g.push(-520, 0, z, 560, 0, z);
  scene.add(new T.LineSegments(segs(g), lmat('grid')));
  // warehouse
  Wp = V3(-380, 0, -120);
  const wg = new T.Group(); wg.position.copy(Wp); scene.add(wg);
  const wm = lmat('W');
  for (let k = 0; k < 3; k++) { const l = new T.LineSegments(new T.EdgesGeometry(new T.CylinderGeometry(52, 52, 28, 14, 1), 1), wm); l.position.y = 18 + k * 34; wg.add(l); }
  wg.add(new T.LineSegments(segs([-70,0,-70, 70,0,-70, 70,0,-70, 70,0,70, 70,0,70, -70,0,70, -70,0,70, -70,0,-70]), wm));
  const pp = [];
  for (let i = 0; i < 900; i++) { const a = rnd() * 6.283, r = Math.sqrt(rnd()) * 46, k = Math.floor(rnd() * 3); pp.push(Math.cos(a) * r, 18 + k * 34 + (rnd() - .5) * 22, Math.sin(a) * r); }
  const pts = new T.Points(segs(pp), new T.PointsMaterial({ size:2, sizeAttenuation:false, color:0, transparent:true }));
  lineMats.push({ m:pts.material, role:'Wpts' }); wg.add(pts);
  hitMesh(new T.CylinderGeometry(60, 60, 110, 12), 'W', wg, V3(0, 52, 0));
  topW = Wp.clone().add(V3(0, 104, 0));
  // edge cache
  Ep = V3(-150, 0, -40);
  const eg = new T.Group(); eg.position.copy(Ep); scene.add(eg);
  const em = lmat('E');
  const ebox = new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(76, 76, 76)), em); ebox.position.y = 78; eg.add(ebox);
  const lat = [], s = 76 / 6 * 2, h = 38;
  for (const a of [-s / 2, s / 2]) for (const b of [-s / 2, s / 2]) lat.push(-h, 78 + a, b, h, 78 + a, b, a, 78 - h, b, a, 78 + h, b, a, 78 + b, -h, a, 78 + b, h);
  for (const [x, z] of [[-38,-38],[38,-38],[38,38],[-38,38]]) lat.push(x, 0, z, x, 40, z);
  eg.add(new T.LineSegments(segs(lat), lmat('E', { soft:true })));
  hitMesh(new T.BoxGeometry(96, 130, 96), 'E', eg, V3(0, 65, 0));
  topE = Ep.clone().add(V3(0, 120, 0));
  // viewer tabs
  tabs.forEach((t, i) => {
    const tg = new T.Group(); tg.position.set(...TAB_POS[i]); tg.rotation.y = -.14; scene.add(tg); t.g = tg;
    const m = lmat('T', { tab:i }), y = TAB_POS[i][1];
    const fr = [-70,0,0, 70,0,0, 70,0,0, 70,TH,0, 70,TH,0, -70,TH,0, -70,TH,0, -70,0,0, -70,TH-14,0, 70,TH-14,0];
    for (let d = 0; d < 3; d++) { const x = -62 + d * 7; fr.push(x-2,TH-9,0, x+2,TH-9,0, x+2,TH-5,0, x+2,TH-5,0, x+2,TH-5,0, x-2,TH-5,0, x-2,TH-5,0, x-2,TH-9,0); }
    fr.push(-40,0,0, -40,-y,0, 40,0,0, 40,-y,0, -58,-y,0, -22,-y,0, 22,-y,0, 58,-y,0);
    tg.add(new T.LineSegments(segs(fr), m));
    const scr = new T.Mesh(new T.PlaneGeometry(TW - 2, TH - 16), new T.MeshBasicMaterial({ color:Ccol.sheet, transparent:true, opacity:.8, depthWrite:false }));
    scr.position.set(0, (TH - 14) / 2, -.6); scr.renderOrder = 1; tg.add(scr); t.screen = scr;
    hitMesh(new T.PlaneGeometry(TW, TH), 'T' + i, tg, V3(0, TH / 2, 1));
    routes['E' + i] = curve(topE, tabTop(i), 90 + i * 6);
  });
  // agent
  Ap = V3(250, 360, 40);
  const ag = new T.Group(); ag.position.copy(Ap); scene.add(ag); S.ag = ag;
  const am = lmat('A');
  const outer = new T.LineSegments(new T.EdgesGeometry(new T.IcosahedronGeometry(30, 0)), am), inner = new T.LineSegments(new T.EdgesGeometry(new T.OctahedronGeometry(12, 0)), am);
  ag.add(outer, inner); S.agOuter = outer; S.agInner = inner;
  const teth = new T.Line(segs([0,-30,0, 0,-Ap.y,0]), new T.LineDashedMaterial({ color:0, dashSize:4, gapSize:4, transparent:true }));
  teth.computeLineDistances(); ag.add(teth); lineMats.push({ m:teth.material, role:'A' });
  hitMesh(new T.SphereGeometry(42, 8, 6), 'A', ag);
  tabs.forEach((t, i) => routes['A' + i] = curve(Ap.clone().add(V3(0, -32, 0)), tabTop(i), 20));
  routes.WE = curve(topW, topE, 70);
  // dashed route lines
  for (const [k, c] of Object.entries(routes)) {
    const l = new T.Line(new T.BufferGeometry().setFromPoints(c.getPoints(48)), new T.LineDashedMaterial({ color:0, dashSize:5, gapSize:5, transparent:true }));
    l.computeLineDistances(); scene.add(l); lineMats.push({ m:l.material, role:'route', key:k });
  }
  BX = new Boxes(420);
  scene.updateMatrixWorld(true);
  layout();
}

/* camera */
const cam = { th:-.34, ph:.42, base:-.34, dist:1000, drag:null, tgt:V3(40, 120, 10) };
function placeCam(th, ph, d){ camera.position.set(cam.tgt.x + d * Math.sin(th) * Math.cos(ph), cam.tgt.y + d * Math.sin(ph), cam.tgt.z + d * Math.cos(th) * Math.cos(ph)); camera.lookAt(cam.tgt); camera.updateMatrixWorld(); }
function layout(){
  const r = stage.getBoundingClientRect(), w = Math.max(280, r.width), h = Math.max(300, r.height);
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  S.W = w; S.H = h;
  const B = [V3(-450,0,-190), V3(-450,150,-190), V3(-310,0,-50), V3(-190,130,-80), V3(-30,0,200), V3(-30,TH+40,130), V3(500,0,160), V3(500,TH+170,-40), V3(30,TH+190,-40), V3(250,400,40)];
  const fitAt = th => { let d = 400; for (; d < 9000; d *= 1.03) { placeCam(th, .42, d); if (B.every(p => { const v = p.clone().project(camera); return Math.abs(v.x) < .96 && v.y < .86 && v.y > -.96; })) break; } return d; };
  if (!cam.touched) { let best = [-.34, fitAt(-.34)]; if (w / h < 1.2) for (const th of [-.6, -.85, -1.05]) { const d = fitAt(th); if (d < best[1] * .97) best = [th, d]; } cam.base = cam.th = best[0]; cam.dist = best[1]; }
  else cam.dist = fitAt(-.34);
}
const proj = v => { const p = v.clone().project(camera); return [(p.x + 1) / 2 * S.W, (1 - p.y) / 2 * S.H, p.z]; };

/* packets */
const packets = [];
function send(route, kind, dur, ep, reverse){
  if (ep !== undefined && ep !== S.epoch) return new Promise(() => {});
  wake();
  return new Promise(res => packets.push({ c:routes[route], t:0, dur:(RM.matches ? 80 : dur) / 1000, kind, res, rev:!!reverse, spin:rnd() * 6 }));
}
const gone = ep => ep !== S.epoch;

/* tab state changes */
function fillTab(i){ const t = tabs[i]; t.filled = 1; t.scan = 0; t.scanDelay = 0; t.dataRegion = t.region; const a = agg(S.dim, t.region); S.lastMs = a.ms; S.lastRows = a.rows; }
async function assignToken(i, r, ep){
  const t = tabs[i]; t.region = r; const key = 'r' + r;
  if (!S.keys.has(key)) {
    S.keys.add(key);
    await send('WE', 'query', 650, ep, true); if (gone(ep)) return;
    S.queries++; S.wHot = 1;
    const ps = [];
    for (let k = 0; k < 6; k++) { ps.push(send('WE', 'batch', 800, ep).then(() => { S.edgeT = Math.min(27, S.edgeT + 4 / 6); S.eHot = 1; })); await wait(70); if (gone(ep)) return; }
    await Promise.all(ps); if (gone(ep)) return;
  } else S.hits++;
  await send('E' + i, 'batch', 700, ep); if (gone(ep)) return;
  if (t.region !== r) return;
  t.dataRegion = r; t.scan = 0; t.scanDelay = 0; const a = agg(S.dim, r); S.lastMs = a.ms; S.lastRows = a.rows;
}
function cycleToken(i){ wake(1200); setAuto(false); const t = tabs[i]; const r = (Math.max(0, t.region) + 1) % DIMS.prov.length; assignToken(i, r, S.epoch); toast(`Viewer ${i + 1} now has region=${DIMS.prov[r][0]}${S.keys.has('r' + r) ? ' · cached key' : ' · new key, one query'}`); }
function setDim(v){
  wake(1200);
  S.dim = v; const a = aggregate(v, -1); aggMemo.set(v + '|-1', a); S.lastMs = a.ms; S.lastRows = N;
  tabs.forEach((t, i) => { if (t.filled) { t.scan = 0; t.scanDelay = i * .07; } });
  root.querySelectorAll('#dimseg button').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v)));
  toast(`Regrouped ${fmtInt(N)} rows in ${a.ms.toFixed(1)} ms in this tab · no query sent`);
}
async function relayout(L, ep){
  const ps = tabs.map((t, i) => t.shown ? wait(i * 110).then(() => send('A' + i, 'layout', 900, ep)).then(() => { t.layout = L; t.kpiFilled = 0; return send('E' + i, 'batch', 650, ep); }).then(() => { t.kpiFilled = 1; S.hits++; }) : null);
  await Promise.all(ps.filter(Boolean));
}
function addViewers(n){
  wake(); setAuto(false); S.viewers += n; S.hits += n; S.eHot = 1;
  for (let k = 0; k < 18; k++) setTimeout(() => send('E' + Math.floor(rnd() * 6), 'batch', 700 + rnd() * 300), k * 60);
  toast(`${n} more viewers · served from the edge · warehouse queries still ${S.queries}`);
}

/* chapters */
const CH = [
  { t:'A viewer opens a dashboard.', focus:'W',
    b:'Until data arrives the tab draws a skeleton. Nubi plans the SQL once, the warehouse runs it, and the result streams back as <b>Arrow record batches</b>: columns, ready to use, with no JSON step.',
    ctl: c => { c.innerHTML = '<button class="btn sm ghost" type="button" id="c-again">Send the query again</button>'; $('#c-again').onclick = () => go(1); },
    play: async ep => {
      await wait(500); if (gone(ep)) return;
      await send('WE', 'query', 800, ep, true); if (gone(ep)) return;
      S.queries++; S.wHot = 1;
      const ps = [];
      for (let i = 0; i < 14; i++) { ps.push(send('WE', 'batch', 1000, ep).then(() => { S.edgeT = Math.min(9, S.edgeT + 9 / 14); S.eHot = 1; S.wHot = 1; })); await wait(85); if (gone(ep)) return; }
      await Promise.all(ps); if (gone(ep)) return;
      S.keys.add('all'); S.focus = 'WT';
      const qs = []; for (let i = 0; i < 4; i++) { qs.push(send('E0', 'batch', 850, ep)); await wait(100); if (gone(ep)) return; }
      await Promise.all(qs); if (gone(ep)) return;
      fillTab(0);
    } },
  { t:'The next viewers never reach the warehouse.', focus:'ET',
    b:'The result is kept at the edge under a hash of the SQL, its parameters and the viewer’s claims. Five more people open the same board, get the same key, and the warehouse still ran <b>one</b> query.',
    ctl: c => { c.innerHTML = `<button class="btn sm ghost" type="button" id="c-add">Add 100 viewers</button><span class="hint">Cache key for this board</span><code id="c-key">sha256:${S.keyHash.slice(0, 24)}…</code>`; $('#c-add').onclick = () => addViewers(100); },
    play: async ep => {
      for (let i = 1; i < 6; i++) {
        tabs[i].shown = 1; S.viewers++; await wait(360); if (gone(ep)) return;
        await Promise.all([send('E' + i, 'batch', 800, ep), wait(90).then(() => send('E' + i, 'batch', 800, ep))]); if (gone(ep)) return;
        S.hits++; S.eHot = 1; fillTab(i); await wait(120); if (gone(ep)) return;
      }
    } },
  { t:'Each tab does its own math.', focus:'T',
    b:'DuckDB-WASM aggregates inside the viewer’s browser. Change the grouping and a million rows are regrouped right here, on this page, in milliseconds. Your servers do nothing.',
    ctl: c => { c.innerHTML = `<div class="seg" role="group" aria-label="Group by" id="dimseg"><button type="button" data-v="prov" aria-pressed="${S.dim === 'prov'}">Region</button><button type="button" data-v="chan" aria-pressed="${S.dim === 'chan'}">Channel</button><button type="button" data-v="cat" aria-pressed="${S.dim === 'cat'}">Category</button></div><span class="hint">The measured time below is your machine.</span>`;
      $('#dimseg').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { setAuto(false); setDim(b.dataset.v); } }); },
    play: async ep => { await wait(400); if (gone(ep)) return; setDim(S.dim); } },
  { t:'The token decides what each tab sees.', focus:'TE',
    b:'Your app signs a short-lived JWT per viewer. Nubi turns its claims into predicates in the query plan. <b>Dashed</b> outlines are rows that exist; <b>solid</b> bars are rows this viewer may see. A new region is a new cache key, so it costs one query.',
    ctl: c => { c.innerHTML = '<span class="hint">Click a token above any tab to give that viewer a different region.</span>'; },
    play: async ep => { for (let i = 0; i < 6; i++) { await assignToken(i, REG[i], ep); if (gone(ep)) return; } } },
  { t:'An agent lays out the board.', focus:'AT',
    b:'Claude or ChatGPT connect over MCP and arrange the widgets. <b>Ink</b> packets carry layout from the agent. <b>Blue</b> packets carry numbers, and numbers only come from SQL.',
    ctl: c => { c.innerHTML = '<button class="btn sm ghost" type="button" id="c-lay">Ask for another layout</button>'; $('#c-lay').onclick = () => { setAuto(false); S.layoutN = S.layoutN === 1 ? 2 : 1; relayout(S.layoutN, S.epoch); }; },
    play: async ep => { S.agent = 1; await wait(800); if (gone(ep)) return; S.layoutN = 1; await relayout(1, ep); } }
];
const DUR = [6.5, 6.5, 2.5, 9, 4.5];

function applyInstant(lv){
  packets.length = 0; S.keys = new Set(); S.viewers = 1; S.queries = 0; S.hits = 0; S.edgeT = 0; S.agent = 0;
  tabs.forEach(t => { t.shown = 0; t.filled = 0; t.region = -1; t.dataRegion = -1; t.layout = 0; t.kpiFilled = 0; t.scan = -1; });
  tabs[0].shown = 1;
  if (lv >= 1) { tabs[0].filled = 1; S.keys.add('all'); S.queries = 1; S.edgeT = 9; }
  if (lv >= 2) { tabs.forEach(t => { t.shown = 1; t.filled = 1; }); S.viewers = 6; S.hits = 5; }
  if (lv >= 4) { tabs.forEach((t, i) => { t.region = t.dataRegion = REG[i]; }); REG.forEach(r => S.keys.add('r' + r)); S.queries = S.keys.size; S.edgeT = Math.min(27, 9 + (S.keys.size - 1) * 4); S.hits += 2; }
  if (lv >= 5) { S.agent = 1; tabs.forEach(t => { t.layout = 1; t.kpiFilled = 1; }); }
}
function renderCap(){
  const c = CH[S.ch - 1];
  $('#cap-n').textContent = `${String(S.ch).padStart(2, '0')} / 05`;
  $('#cap-t').textContent = c.t; $('#cap-b').innerHTML = c.b; c.ctl($('#cap-ctl'));
  root.querySelectorAll('#rail button').forEach(b => { const k = +b.dataset.c; b.setAttribute('aria-current', k === S.ch ? 'step' : 'false'); b.setAttribute('aria-selected', String(k === S.ch)); b.classList.toggle('done', k < S.ch); });
  $('#prev').disabled = S.ch <= 1; $('#next').textContent = S.ch >= 5 ? 'Start over' : 'Next step →';
}
async function go(k, fromAuto){
  wake(1200);
  if (!fromAuto) setAuto(false);
  S.epoch++; const ep = S.epoch; S.ch = k; S.chDone = false; S.chT0 = performance.now(); S.focus = CH[k - 1].focus;
  applyInstant(k - 1); renderCap();
  await CH[k - 1].play(ep); if (gone(ep)) return;
  S.chDone = true;
  if (S.auto) { await wait(3600); if (gone(ep) || !S.auto) return; if (k < 5) go(k + 1, true); else setAuto(false); }
}
function setAuto(v){ S.auto = v; $('#play').textContent = v ? 'Pause' : 'Play story'; $('#cap-auto').textContent = v ? 'Playing' : 'You’re driving'; }
$('#rail').addEventListener('click', e => { const b = e.target.closest('button'); if (b && ok) go(+b.dataset.c); });
$('#prev').addEventListener('click', () => ok && S.ch > 1 && go(S.ch - 1));
$('#next').addEventListener('click', () => ok && go(S.ch >= 5 ? 1 : S.ch + 1));
$('#play').addEventListener('click', () => { if (!ok) return; if (S.auto) return setAuto(false); setAuto(true); if (S.chDone) go(S.ch >= 5 ? 1 : S.ch + 1, true); });
stage.addEventListener('keydown', e => { if (e.key === 'ArrowRight') go(Math.min(5, S.ch + 1)); if (e.key === 'ArrowLeft') go(Math.max(1, S.ch - 1)); });

/* annotations */
const LBL = [
  { id:'W', a:() => topW, dx:-34, dy:-58, ndx:-12, ndy:-26, t:() => ['Warehouse', S.queries ? `${S.queries} ${S.queries === 1 ? 'query' : 'queries'} run` : 'waiting'], show:() => true },
  { id:'E', a:() => topE, dx:-30, dy:-104, t:() => ['Edge cache', `${S.keys.size} ${S.keys.size === 1 ? 'key' : 'keys'} · ${fmtInt(S.hits)} hits`], show:() => true },
  { id:'T', a:() => { let k = 0; tabs.forEach((t, i) => { if (t.shown) k = i; }); return tabTop(k >= 3 ? 5 : k); }, dx:30, dy:-46, t:() => ['Viewer tabs', `${fmtInt(S.viewers)} ${S.viewers === 1 ? 'viewer' : 'viewers'} · 0 ms server`], show:() => true },
  { id:'A', a:() => Ap.clone().add(V3(0, 30, 0)), dx:-40, dy:-28, t:() => ['Agent · MCP', 'layout only, never values'], show:() => S.agentV > .5 }
];
LBL.forEach(l => { l.el = document.createElement('div'); l.el.className = 'an'; l.el.innerHTML = '<b></b><span></span>'; annoEl.appendChild(l.el); });
tabs.forEach((t, i) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'tok'; b.hidden = true; b.addEventListener('click', () => cycleToken(i)); annoEl.appendChild(b); t.tok = b; });
function annotate(){
  let svg = '';
  const narrow = S.W < 560;
  for (const l of LBL) {
    const vis = l.show(); l.el.hidden = !vis; if (!vis) continue;
    const [ax, ay] = proj(l.a()); const tx = l.t();
    if (l.el.firstChild.textContent !== tx[0]) l.el.firstChild.textContent = tx[0];
    if (l.el.lastChild.textContent !== tx[1]) l.el.lastChild.textContent = tx[1];
    l.el.lastChild.hidden = narrow && l.id !== 'T' && l.id !== 'W';
    const w = l.el.offsetWidth, h = l.el.offsetHeight, dim = !S.focus.includes(l.id);
    let dx = narrow ? (l.ndx ?? l.dx * .6) : l.dx, dy = narrow ? (l.ndy ?? l.dy * .7) : l.dy;
    let ex = ax + dx, ey = ay + dy, lx = dx < 0 ? ex - w : ex;
    if (lx < 4) { lx = 4; } if (lx + w > S.W - 4) lx = S.W - 4 - w;
    const ux0 = Math.min(ex, lx), ux1 = Math.max(ex, lx + w);
    l.el.style.transform = `translate(${lx}px, ${ey - h - 4}px)`;
    l.el.classList.toggle('dim', dim); l.el.classList.toggle('focus', !dim);
    svg += `<g class="${dim ? 'dim' : ''}"><path d="M${ax},${ay} L${ex},${ey} M${ux0},${ey} L${ux1},${ey}"/><circle cx="${ax}" cy="${ay}" r="3"/></g>`;
  }
  leadEl.innerHTML = svg;
  tabs.forEach((t, i) => {
    const vis = t.region >= 0 && t.vis > .6 && S.ch >= 4; t.tok.hidden = !vis; if (!vis) return;
    const [x, y] = proj(tabTop(i).add(V3(0, 6, 0)));
    const txt = `region=${DIMS.prov[t.region][0]}`; if (t.tok.textContent !== txt) t.tok.textContent = txt;
    t.tok.classList.toggle('wait', t.dataRegion !== t.region);
    t.tok.setAttribute('aria-label', `Viewer ${i + 1} token, region ${DIMS.prov[t.region][1]}. Click to change.`);
    t.tok.style.transform = `translate(${x - t.tok.offsetWidth / 2}px, ${y - t.tok.offsetHeight - 2}px)`;
  });
}

/* pointer: orbit, hover, click */
const ray = new T.Raycaster(), ndc = new T.Vector2();
let hover = null;
function pickHit(e){
  const r = stage.getBoundingClientRect(); ndc.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const live = hits.filter(h => { const tag = h.userData.hit; if (tag[0] === 'T') return tabs[+tag[1]].vis > .5; if (tag === 'A') return S.agentV > .5; return true; });
  const hit = ray.intersectObjects(live, false)[0];
  return hit ? hit.object.userData.hit : null;
}
stage.addEventListener('pointerdown', e => { wake(); if (!ok || e.target.closest('.tok')) return; cam.drag = { x:e.clientX, y:e.clientY, moved:false, th:cam.th, ph:cam.ph }; });
addEventListener('pointermove', e => {
  if (!ok) return;
  if (cam.drag) {
    const dx = e.clientX - cam.drag.x, dy = e.clientY - cam.drag.y;
    if (!cam.drag.moved && Math.abs(dx) + Math.abs(dy) > 5) { cam.drag.moved = true; stage.classList.add('drag'); }
    if (cam.drag.moved) { cam.th = Math.max(-1.25, Math.min(.9, cam.drag.th - dx * .006)); cam.ph = Math.max(.1, Math.min(1.1, cam.drag.ph + dy * .004)); cam.base = cam.th; cam.touched = true; }
    return;
  }
  if (!stage.contains(e.target) || e.target.closest('.tok')) { if (hover) { hover = null; stage.classList.remove('hot'); } return; }
  hover = pickHit(e); stage.classList.toggle('hot', !!hover);
});
addEventListener('pointerup', e => {
  if (!cam.drag) return;
  wake(1500);
  const d = cam.drag; cam.drag = null; stage.classList.remove('drag');
  if (d.moved || !stage.contains(e.target)) return;
  const tag = pickHit(e); if (!tag) return;
  if (tag === 'W') go(1); else if (tag === 'E') go(2); else if (tag === 'A') go(5);
  else if (tag[0] === 'T') { if (S.ch === 4) cycleToken(+tag[1]); else go(3); }
});
addEventListener('pointercancel', () => { cam.drag = null; stage.classList.remove('drag'); });

/* frame */
let tAcc = 0, roT = 0;
const lerpC = (out, a, b, t) => out.copy(a).lerp(b, Math.max(0, Math.min(1, t)));
const tmpC = new T.Color();
// Called by the figure runtime only while something is moving (render on demand); returns true
// while the scene is still away from rest, so a settled figure costs no GPU at all.
function frame(now, dt){
  tAcc += dt;
  let err = 0;
  const k = 1 - Math.pow(.004, dt), slow = 1 - Math.pow(.15, dt);
  S.wHot = Math.max(0, S.wHot - dt * 1.2); S.eHot = Math.max(0, S.eHot - dt * 1.2);
  err += S.wHot + S.eHot + Math.abs(S.edgeT - S.edge) + Math.abs(S.agent - S.agentV);
  S.edge += (S.edgeT - S.edge) * k; S.agentV += (S.agent - S.agentV) * k;
  // camera: a slow sway only while the story plays; otherwise ease to rest and stop
  if (!cam.drag && !RM.matches) {
    const want = cam.base + ((S.auto || !S.chDone) ? Math.sin(tAcc * .18) * .05 : 0);
    err += Math.abs(want - cam.th) * 40;
    cam.th += (want - cam.th) * slow * .5;
  }
  placeCam(cam.th, cam.ph, cam.dist);
  // line colours
  const F = S.focus;
  for (const L of lineMats) {
    const m = L.m; let c = Ccol.ink, op = 1;
    switch (L.role) {
      case 'grid': c = Ccol.hair; break;
      case 'W': lerpC(tmpC, F.includes('W') ? Ccol.ink : Ccol.ink3, Ccol.acc, S.wHot); c = tmpC; break;
      case 'Wpts': lerpC(tmpC, Ccol.ink3, Ccol.acc, S.wHot); c = tmpC; break;
      case 'E': lerpC(tmpC, F.includes('E') ? Ccol.ink : Ccol.ink3, Ccol.acc, S.eHot); c = tmpC; op = L.soft ? .5 : 1; break;
      case 'T': c = F.includes('T') ? Ccol.ink : Ccol.ink3; op = tabs[L.tab].vis; break;
      case 'A': c = Ccol.ink; op = S.agentV; break;
      case 'route': {
        c = Ccol.ink3; const key = L.key;
        op = key === 'WE' ? 1 : key[0] === 'E' ? tabs[+key[1]].vis * .9 : S.agentV * tabs[+key[1]].vis * .9; break; }
    }
    m.color.copy(c); m.opacity = op; m.visible = op > .01;
  }
  // agent
  if (!RM.matches && S.agentV > .02 && (S.auto || !S.chDone)) err += 1; // the agent only spins while the story plays
  S.ag.scale.setScalar(Math.max(.001, S.agentV)); S.agOuter.rotation.y += dt * .5; S.agInner.rotation.x -= dt * .8;
  S.ag.position.y = Ap.y + Math.sin(tAcc * 1.3) * 4;
  // tabs + bars
  BX.begin();
  const n = DIMS[S.dim].length, full = agg(S.dim, -1).v; let maxV = 0; for (let j = 0; j < n; j++) maxV = Math.max(maxV, full[j]);
  const pitch = 118 / n, bw = Math.min(11, pitch - 3.5);
  tabs.forEach(t => {
    err += Math.abs(t.shown - t.vis); if (t.shown && !t.filled && !RM.matches) err += 1;
    t.vis += (t.shown - t.vis) * k;
    const s = t.vis < .002 ? .001 : 1 - Math.pow(1 - t.vis, 3);
    t.g.scale.setScalar(s); t.g.updateMatrixWorld(true);
    t.screen.material.opacity = .8 * t.vis; t.screen.visible = t.vis > .01;
    if (t.vis < .01) return;
    const lay = LAYOUTS[t.layout], hmax = lay.hmax;
    const fv = t.dataRegion >= 0 ? agg(S.dim, t.dataRegion).v : full;
    for (let j = 0; j < 9; j++) {
      let sol = 0, gh = 0;
      if (j < n) {
        if (!t.filled) gh = PLACE[j] * hmax;
        else { sol = fv[j] / maxV * hmax; gh = t.dataRegion >= 0 ? full[j] / maxV * hmax : 0; }
      }
      err += Math.abs(sol - t.h[j]) + Math.abs(gh - t.gh[j]);
      t.h[j] += (sol - t.h[j]) * k; t.gh[j] += (gh - t.gh[j]) * k;
      if (j >= n) continue;
      const x = -59 + (j + .5) * pitch;
      if (t.gh[j] > .4) {
        const shimmer = t.filled ? .9 : .35 + .3 * Math.sin(tAcc * 4 - j * .6);
        BX.add(boxM(t.g, x, 4 + t.gh[j] / 2, 6, bw, t.gh[j], 10), Ccol.ink3, -shimmer, Ccol.paper, 0);
      }
      if (t.h[j] > .3) {
        const mine = t.dataRegion >= 0;
        BX.add(boxM(t.g, x, 4 + t.h[j] / 2, 6, bw, t.h[j], 10), mine ? Ccol.acc : (F.includes('T') ? Ccol.ink : Ccol.ink3), 1, mine ? Ccol.accS : Ccol.paper, .82);
      }
    }
    // KPI tiles
    for (let q = 0; q < 3; q++) {
      const want = q < lay.kpi ? 1 : 0; err += Math.abs(want - t.kv[q]); t.kv[q] += (want - t.kv[q]) * k;
      if (t.kv[q] < .02) continue;
      const kw = (118 - (lay.kpi - 1) * 6) / Math.max(1, lay.kpi), x = -59 + kw / 2 + q * (kw + 6);
      const fillK = t.kpiFilled;
      BX.add(boxM(t.g, x, 66, 4, kw * t.kv[q], 12, 6 * t.kv[q]), fillK ? Ccol.acc : Ccol.ink3, fillK ? 1 : -(.35 + .3 * Math.sin(tAcc * 4 + q)), fillK ? Ccol.accS : Ccol.paper, fillK ? .9 : 0);
    }
    // scan
    if (t.scan >= 0) {
      err += 1;
      if (t.scanDelay > 0) t.scanDelay -= dt;
      else { t.scan += dt * 1.5; if (t.scan > 1) t.scan = -1; else BX.add(boxM(t.g, -60 + 120 * t.scan, 4 + hmax / 2 + 2, 7, 1.4, hmax + 6, 18), Ccol.acc, 1, Ccol.acc, .22); }
    }
  });
  // edge cells
  const ec = Ep.clone().add(V3(0, 78, 0)), st = 76 / 3;
  for (let c = 0; c < 27; c++) {
    const f = Math.max(0, Math.min(1, S.edge - c)); if (f < .02) break;
    const ix = c % 3, iz = Math.floor(c / 3) % 3, iy = Math.floor(c / 9);
    const sz = (st - 5) * f;
    BX.add(boxM(null, ec.x + (ix - 1) * st, ec.y + (iy - 1) * st, ec.z + (iz - 1) * st, sz, sz, sz), Ccol.acc, 1, Ccol.accS, .85);
  }
  // packets
  err += packets.length;
  for (let i = packets.length - 1; i >= 0; i--) {
    const p = packets[i]; p.t += dt / p.dur;
    if (p.t >= 1) { packets.splice(i, 1); p.res(); continue; }
    const e = p.t < .5 ? 2 * p.t * p.t : 1 - Math.pow(-2 * p.t + 2, 2) / 2;
    const pos = p.c.getPoint(p.rev ? 1 - e : e);
    if (p.kind === 'batch') BX.add(boxM(null, pos.x, pos.y, pos.z, 14, 4, 9, -.3), Ccol.acc, 1, Ccol.acc, .95);
    else if (p.kind === 'query') BX.add(boxM(null, pos.x, pos.y, pos.z, 8, 8, 8, p.spin + tAcc * 3), Ccol.acc, 1, Ccol.paper, 1);
    else BX.add(boxM(null, pos.x, pos.y, pos.z, 11, 11, 11, p.spin + tAcc * 2), Ccol.ink, 1, Ccol.paper, .9);
  }
  BX.end();
  renderer.render(scene, camera);
  annotate();
  // rail progress + readouts
  const busy = err > .004 || !S.chDone || !!cam.drag || (!RM.matches && S.auto);
  roT += dt;
  if (roT > .1 || !busy) {
    roT = 0;
    root.querySelectorAll('#rail button').forEach(b => { const c = +b.dataset.c; const p = c < S.ch ? 100 : c > S.ch ? 0 : (S.auto ? Math.min(100, (performance.now() - S.chT0) / 1000 / (DUR[c - 1] + 3.6) * 100) : 100); b.style.setProperty('--p', p + '%'); });
    $('#r-view').textContent = fmtInt(S.viewers);
    $('#r-wq').textContent = fmtInt(S.queries);
    $('#r-wq-s').textContent = S.keys.size > 1 ? `${S.keys.size} cache keys` : 'one per cache key';
    $('#r-hit').textContent = fmtInt(S.hits);
    $('#r-agg').textContent = S.lastMs == null ? '—' : S.lastMs.toFixed(S.lastMs < 10 ? 2 : 1) + ' ms';
    $('#r-agg-s').textContent = S.lastMs == null ? 'measured in your browser' : `${fmtInt(S.lastRows)} rows scanned here`;
  }
  return busy;
}

if (ok) {
  try {
    build();
    agg('prov', -1);
    sha(canon({ sql:'SELECT region, SUM(revenue) FROM sales GROUP BY 1', params:{ from:'2026-07-01' }, rls_claims:{} })).then(h => { S.keyHash = h; const c = $('#c-key'); if (c) c.textContent = `sha256:${h.slice(0, 24)}…`; });
    stage.tabIndex = 0;
    rt = createFigureRuntime({
      el: stage, canvas: cv, frame,
      resize: (w, h, dpr) => { renderer.setPixelRatio(dpr); layout(); },
      restore: () => { readColors(); },
      onMotionPref: () => wake(300),
    });
    cleanups.push(() => rt.dispose());
    go(1, true);
  } catch (e) { ok = false; console.warn(e); }
}
if (!ok) { cv.hidden = true; const d = document.createElement('div'); d.className = 'nogl'; d.textContent = 'This figure needs WebGL. The rest of the page works without it.'; stage.appendChild(d); S.ch = 1; renderCap(); }


new MutationObserver(() => { if (ok) { readColors(); wake(300) } }).observe(document.documentElement, { attributes:true, attributeFilter:['class', 'data-theme'] });

  return () => { alive = false; cleanups.forEach(f => { try { f() } catch { /* already gone */ } }); if (renderer) { renderer.dispose(); renderer.forceContextLoss(); } }
}
