import { createFigureRuntime } from './figureRuntime.js'

/**
 * Edge cache keys: viewers grouped by security scope flow into content-hashed cache keys
 * and on to the warehouse. The keys are real SHA-256 hashes of canonical JSON.
 * mount(root) wires the figure to its markup inside `root` and returns a cleanup
 * that stops every loop, listener and observer.
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
  
const $ = s => root.querySelector(s);
const RM = matchMedia('(prefers-reduced-motion: reduce)');
const fmtInt = n => Math.round(n).toLocaleString('en-US');
const css = n => getComputedStyle(root).getPropertyValue(n).trim();

function seg(id, fn){
  const el = $(id);
  el.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return;
    el.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); fn(b.dataset.v); });
}
/* ================= Fig. 2 · edge cache ================= */
const C = { viewers:1000, scopes:3, shared:true, keys:[] };
const ccv = $('#cachecv'), cx2 = ccv.getContext('2d');
const SQL2 = 'SELECT region, SUM(revenue) FROM sales WHERE order_date >= :from GROUP BY 1';
function canon(o){ return Array.isArray(o) ? '[' + o.map(canon).join(',') + ']' : o && typeof o === 'object' ? '{' + Object.keys(o).sort().map(k => JSON.stringify(k) + ':' + canon(o[k])).join(',') + '}' : JSON.stringify(o); }
async function sha(s){
  try { const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)); return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join(''); }
  catch { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16).padStart(8, '0').repeat(8); }
}
const SCOPE_NAMES = ['AMER','EMEA','APAC','LATAM','UKI','DACH','ANZ','ASEAN','BRAZIL','ANDEAN','HQ','PARTNER-A'];
async function computeKeys(){
  const n = C.scopes;
  C.keys = await Promise.all(Array.from({ length:n }, (_, i) => sha(canon({ sql:SQL2, params:{ from:'2026-07-01' }, rls_claims: n === 1 ? {} : { region: SCOPE_NAMES[i] } }))));
  drawCache();
}
function sizeCanvas(c){ const r = c.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2); c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); return r; }
function drawCache(){
  const r = sizeCanvas(ccv), w = r.width, h = r.height, x = cx2;
  const ink = css('--ink'), ink2 = css('--ink-2'), ink3 = css('--ink-3'), acc = css('--accent'), sheet = css('--sheet');
  x.fillStyle = sheet; x.fillRect(0, 0, w, h);
  const narrow = w < 560;
  const D = Math.min(C.viewers, narrow ? 600 : 1200), per = Math.ceil(C.viewers / D), shown = Math.ceil(C.viewers / per);
  $('#perdot').textContent = per === 1 ? '1 viewer' : fmtInt(per) + ' viewers';
  const gx0 = 18, gw = w * (narrow ? .4 : .36), gy0 = 34, gh = h - 60;
  const cols = Math.max(1, Math.ceil(Math.sqrt(shown * gw / gh))), rowsN = Math.ceil(shown / cols);
  const pitch = Math.min(gw / cols, gh / rowsN, 22), rad = Math.max(1.2, pitch * .32);
  const kx = w * (narrow ? .62 : .6), wx = w - (narrow ? 40 : 70), wy = h / 2;
  x.font = '500 10.5px "Geist Mono", ui-monospace, monospace'; x.textBaseline = 'middle';
  x.fillStyle = ink2; x.fillText('VIEWERS', gx0, 16); x.fillText(narrow ? 'KEYS' : 'CACHE KEYS', kx - 10, 16);
  x.textAlign = 'right'; x.fillText('WAREHOUSE', w - 14, 16); x.textAlign = 'left';
  const n = C.scopes, kys = [];
  for (let i = 0; i < n; i++) kys.push(n === 1 ? wy : 40 + (h - 70) * (i + .5) / n);
  const pts = [];
  for (let i = 0; i < shown; i++) {
    const cc = i % cols, rr = Math.floor(i / cols);
    pts.push([gx0 + (cc + .5) * pitch, gy0 + (rr + .5) * pitch, Math.floor(i / shown * n)]);
  }
  // lines
  x.lineWidth = 1;
  if (C.shared) {
    const step = Math.max(1, Math.floor(shown / 400));
    for (let i = 0; i < shown; i += step) { const p = pts[i], ky = kys[p[2]];
      x.strokeStyle = p[2] === 0 ? acc : ink3; x.globalAlpha = .16; x.beginPath(); x.moveTo(p[0], p[1]); x.bezierCurveTo(p[0] + (kx - p[0]) * .6, p[1], kx - (kx - p[0]) * .3, ky, kx - 8, ky); x.stroke(); }
    x.globalAlpha = 1;
    for (let i = 0; i < n; i++) { x.strokeStyle = i === 0 ? acc : ink; x.lineWidth = 2; x.beginPath(); x.moveTo(kx + (narrow ? 8 : 150), kys[i]); x.bezierCurveTo(wx - 40, kys[i], wx - 60, wy, wx - 14, wy); x.stroke(); }
    x.lineWidth = 1;
    for (let i = 0; i < n; i++) {
      x.fillStyle = i === 0 ? acc : ink; x.fillRect(kx - 8, kys[i] - 4, 8, 8);
      if (!narrow) { x.fillStyle = ink; x.fillText((C.keys[i] || '').slice(0, 10) + '…', kx + 6, kys[i] - (n > 6 ? 0 : 6));
        if (n <= 6) { x.fillStyle = ink2; x.fillText(n === 1 ? 'no claims' : 'region=' + SCOPE_NAMES[i], kx + 6, kys[i] + 8); } }
    }
  } else {
    const step = Math.max(1, Math.floor(shown / 500));
    for (let i = 0; i < shown; i += step) { const p = pts[i];
      x.strokeStyle = ink3; x.globalAlpha = .22; x.beginPath(); x.moveTo(p[0], p[1]); x.bezierCurveTo(p[0] + (wx - p[0]) * .5, p[1], wx - 80, wy, wx - 14, wy); x.stroke(); }
    x.globalAlpha = 1;
    x.fillStyle = ink2; x.fillText('no shared cache', kx - 10, h - 18);
  }
  // dots
  for (const p of pts) { x.fillStyle = p[2] === 0 ? acc : ink; x.beginPath(); x.arc(p[0], p[1], rad, 0, 7); x.fill(); }
  // warehouse cylinder
  const cw = narrow ? 30 : 44, ch = narrow ? 44 : 62, top = wy - ch / 2, rx = cw / 2, cxm = wx - 14 + rx;
  x.strokeStyle = ink; x.lineWidth = 2; x.fillStyle = sheet;
  x.beginPath(); x.moveTo(cxm - rx, top); x.lineTo(cxm - rx, top + ch); x.ellipse(cxm, top + ch, rx, 6, 0, Math.PI, 0, true); x.lineTo(cxm + rx, top); x.closePath(); x.fill(); x.stroke();
  x.beginPath(); x.ellipse(cxm, top, rx, 6, 0, 0, Math.PI * 2); x.fill(); x.stroke();
  x.beginPath(); x.ellipse(cxm, top + ch * .4, rx, 6, 0, Math.PI, 0, true); x.lineWidth = 1; x.stroke();
  x.fillStyle = C.shared ? acc : ink; x.font = '600 13px "Geist Mono", ui-monospace, monospace'; x.textAlign = 'center';
  x.fillText(fmtInt(C.shared ? n : C.viewers), cxm, top + ch + 24); x.textAlign = 'left';
  x.lineWidth = 1;
}
function updCache(){
  $('#viewers-o').textContent = fmtInt(C.viewers);
  const q = C.shared ? Math.min(C.scopes, C.viewers) : C.viewers;
  const el = $('#wq'); el.textContent = fmtInt(q); el.classList.toggle('good', C.shared);
  $('#wq-s').textContent = C.shared ? (C.viewers > 1 ? `one per cache key · ${fmtInt(C.viewers - q)} served from the edge` : 'one per cache key') : 'one per viewer';
  $('#h-cache').textContent = `${cap(words(C.viewers))} viewer${C.viewers === 1 ? '' : 's'}, ${words(q)} quer${q === 1 ? 'y' : 'ies'}.`;
  drawCache();
}
const cap = s => s[0].toUpperCase() + s.slice(1);
function words(n){ const w = ['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve']; if (n <= 12) return w[n]; if (n === 1000) return 'a thousand'; if (n === 100000) return 'a hundred thousand'; return fmtInt(n); }
$('#viewers').addEventListener('input', e => { const v = Math.round(Math.pow(10, +e.target.value)); C.viewers = v; updCache(); });
seg('#seg-ten', v => { C.scopes = +v; updCache(); computeKeys(); });
$('#shared').addEventListener('change', e => { C.shared = e.target.checked; updCache(); });
// Size + devicePixelRatio changes (zoom, moving between monitors) redraw the canvas; no render loop.
const rt = createFigureRuntime({ el: ccv, resize: () => drawCache() }); cleanups.push(() => rt.dispose());
computeKeys();

new MutationObserver(() => drawCache()).observe(document.documentElement, { attributes:true, attributeFilter:['class', 'data-theme'] });
document.fonts && document.fonts.ready.then(() => alive && drawCache());

  return () => { alive = false; cleanups.forEach(f => { try { f() } catch { /* already gone */ } });  }
}
