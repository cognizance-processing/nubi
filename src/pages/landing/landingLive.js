import * as THREE from 'three'
// three r186 keeps the r128 look: colours are used as given (no sRGB decode) and written without an sRGB encode.
THREE.ColorManagement.enabled = false
import { toast as uiToast } from './chrome.js'

/**
 * Landing outro: the live 1M-row instrument, the embed demo, the transcript replay and the
 * dot-matrix wordmark. (The ⌘K palette and the menu live in chrome.js.)
 *
 * Engineering notes
 *  - the instrument renders only while something moves (tiles easing, drops falling, the cloud
 *    refreshing at 15 fps); with reduced motion it idles completely once settled.
 *  - the halftone cloud is computed on a small cell-sized target and only sampled per pixel.
 *  - pauses offscreen (IntersectionObserver) and on hidden tabs; recovers from WebGL context loss.
 *
 * mount(root) wires the figure to the markup inside `root` and returns a cleanup
 * that stops every loop, listener and observer and frees the WebGL context.
 */
export function mount(root) {
  const cleanups = []
  let alive = true
  const on = (el, t, f, o) => { el.addEventListener(t, f, o); cleanups.push(() => el.removeEventListener(t, f, o)) }
  const raf = f => (alive ? window.requestAnimationFrame(t => { if (alive) f(t) }) : 0)
  const later = (f, ms) => { const id = window.setTimeout(f, ms); cleanups.push(() => window.clearTimeout(id)); return id }
  const watch = (Cls, cb, o) => { const x = new Cls(cb, o); cleanups.push(() => x.disconnect()); return x }
  const doc = window.document
  const getRootStyle = () => getComputedStyle(root)
  // A context lost by a previous mount (StrictMode, route re-entry) can't be reused: start on a fresh canvas.
  { const old = root.querySelector('canvas#pgl'); if (old) old.replaceWith(old.cloneNode(false)) }

  const $ = s => root.querySelector(s)
  const RM = window.matchMedia('(prefers-reduced-motion: reduce)')
  const fmtInt = n => Math.round(n).toLocaleString('en-US')
  const fmtUsd = v => v >= 1e9 ? '$' + (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? '$' + (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? '$' + (v / 1e3).toFixed(0) + 'k' : '$' + Math.round(v)
  const css = n => getRootStyle().getPropertyValue(n).trim()
  const toast = (msg, ms) => uiToast(root, msg, ms)

  /* ================= data: 1,048,576 synthetic rows in typed arrays ================= */
  const DIMS = {
    cty: [['US', 'United States'], ['GB', 'United Kingdom'], ['DE', 'Germany'], ['FR', 'France'], ['BR', 'Brazil'], ['CA', 'Canada'], ['JP', 'Japan'], ['AU', 'Australia'], ['IN', 'India']],
    reg: [['AMER', 'AMER'], ['EMEA', 'EMEA'], ['APAC', 'APAC'], ['LATAM', 'LATAM']],
    chan: [['STORE', 'Store'], ['ONLINE', 'Online'], ['WHSL', 'Wholesale'], ['MKT', 'Marketplace']],
    cat: [['GROC', 'Grocery'], ['HOME', 'Home'], ['APPRL', 'Apparel'], ['ELEC', 'Electronics'], ['HLTH', 'Health'], ['OUTD', 'Outdoor']],
  }
  const W = { cty: [.34, .14, .12, .1, .09, .08, .06, .04, .03], reg: [.38, .3, .22, .1], chan: [.46, .28, .17, .09], cat: [.31, .19, .16, .15, .11, .08] }
  const CAT_MEAN = [190, 640, 520, 2350, 310, 880]
  const N = 1 << 20, BATCH = 4096, NB = N / BATCH
  let seed = 1042
  const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296 }
  const cum = a => { let s = 0; return a.map(x => s += x) }
  const pick = (c, r) => { let i = 0; while (i < c.length - 1 && r > c[i]) i++; return i }
  const col = { cty: new Uint8Array(N), reg: new Uint8Array(N), chan: new Uint8Array(N), cat: new Uint8Array(N) }, rev = new Float32Array(N)
  ;(() => {
    const cp = cum(W.cty), cr = cum(W.reg), cc = cum(W.chan), ck = cum(W.cat)
    for (let i = 0; i < N; i++) {
      const p = pick(cp, rnd()), r = pick(cr, rnd()), c = pick(cc, rnd()), k = pick(ck, rnd())
      col.cty[i] = p; col.reg[i] = r; col.chan[i] = c; col.cat[i] = k
      const u = rnd() + rnd() + rnd()
      rev[i] = CAT_MEAN[k] * (0.25 + u * 0.5) * (c === 2 ? 2.6 : 1)
    }
  })()
  const BYTES_PER_ROW = 3 + 4
  const PVIS = { on: false }

  /* ================= instrument state ================= */
  const S = { dim: 'cty', meas: 'rev', landed: new Uint8Array(NB), nLanded: 0, spawned: 0, acc: new Float64Array(9),
    mode: 'cold', queries: 1, cacheHits: 0, pinned: -1, hover: -1, lastAgg: 0, lastAggRows: 0, fill: 1 }
  function aggBatch(b) {
    const d = col[S.dim], from = b * BATCH, to = from + BATCH, a = S.acc
    if (S.meas === 'rev') for (let i = from; i < to; i++) a[d[i]] += rev[i]
    else for (let i = from; i < to; i++) a[d[i]] += 1
  }
  function reaggregate() {
    const t0 = performance.now()
    S.acc.fill(0)
    for (let b = 0; b < NB; b++) if (S.landed[b]) aggBatch(b)
    S.lastAgg = performance.now() - t0; S.lastAggRows = S.nLanded * BATCH
  }
  const nGroups = () => DIMS[S.dim].length
  const accTotal = () => S.acc.reduce((a, b) => a + b, 0)

  /* ================= instrument renderer ================= */
  const stage = $('#pstage'), cv = $('#pgl')
  let gl3 = null
  /* capability probe: release the throwaway context at once (browsers cap live contexts at ~16, and a
     detached canvas is not collected promptly, so each landing mount would otherwise leak one) */
  try {
    const t = doc.createElement('canvas')
    const g = t.getContext('webgl2') || t.getContext('webgl')
    gl3 = g ? true : null
    g?.getExtension('WEBGL_lose_context')?.loseContext()
    t.width = t.height = 0
  } catch { gl3 = null }

  const MAXG = 9, MAXPER = 700, NT = MAXG * MAXPER, ND = 260
  let renderer, scene, camera, tiles, drops, cloud, dense, denseScene, denseCam, denseRT, Wd = 0, Ht = 0, L = {}
  let glLost = false
  const tc = { x: new Float32Array(NT), y: new Float32Array(NT), s: new Float32Array(NT), tx: new Float32Array(NT), ty: new Float32Array(NT), ts: new Float32Array(NT), d: new Float32Array(NT), colr: new Int8Array(NT).fill(-2) }
  const dr = { x: new Float32Array(ND), y: new Float32Array(ND), v: new Float32Array(ND), b: new Int32Array(ND).fill(-1), c: new Int8Array(ND), stop: new Float32Array(ND) }
  let counts = new Int32Array(MAXG), unit = 1
  const pointer = { x: -9999, y: -9999, nx: 0, ny: 0, in: false }
  let colors = {}
  let needsRender = true, densityAt = -1e9, densityDirty = true, activeDrops = 0

  function readColors() {
    colors = { ink: new THREE.Color(css('--ink')), ink3: new THREE.Color(css('--subtle')), accent: new THREE.Color(css('--accent')), paper: new THREE.Color(css('--bg')) }
    if (!renderer) return
    renderer.setClearColor(colors.paper, 1)
    cloud.material.uniforms.uInk.value.copy(colors.ink)
    cloud.material.uniforms.uAcc.value.copy(colors.accent)
    drops.material.color.copy(colors.accent)
    tc.colr.fill(-2); needsRender = true
  }

  const VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }'
  /* pass 1: halftone density per cell (the expensive fbm), rendered to a tiny cell-sized target at ~15 fps */
  const DENSE_FRAG = `
precision highp float;
varying vec2 vUv;
uniform vec2 uSize; uniform vec2 uGrid; uniform float uTime; uniform float uCell; uniform float uFill;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float n2(vec2 p){ vec2 i=floor(p), f=fract(p); vec2 u=f*f*(3.-2.*f);
  return mix(mix(h(i),h(i+vec2(1,0)),u.x), mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x), u.y); }
float fbm(vec2 p){ float v=0., a=.5; for(int i=0;i<5;i++){ v+=a*n2(p); p=p*2.03+vec2(1.7,9.2); a*=.5; } return v; }
void main(){
  vec2 px = vec2(vUv.x * uGrid.x * uCell, (1.0 - vUv.y) * uGrid.y * uCell);
  vec2 g = floor(px / uCell); vec2 c = (g + .5) * uCell;
  float t = c.y / uSize.y;
  float band = smoothstep(.02, .38, t) * (1. - smoothstep(.62, .98, t));
  vec2 q = c / 300.;
  float n = fbm(vec2(q.x - uTime*.035, q.y*1.25 + uTime*.008));
  float m = fbm(q*.55 + vec2(uTime*.012, 3.1));
  float v = n*.95 + m*.55 - .62 + band*.28;
  float d = smoothstep(.22, .72, v) * band * uFill;
  gl_FragColor = vec4(clamp(d, 0., 1.), 0., 0., 1.);
}`
  /* pass 2: per pixel only a texture read, a mouse falloff and a disc */
  const FRAG = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uDense; uniform vec2 uSize; uniform vec2 uGrid; uniform float uTime; uniform vec2 uMouse; uniform float uCell; uniform float uAA;
uniform vec3 uInk; uniform vec3 uAcc; uniform float uRain;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
void main(){
  vec2 px = vec2(vUv.x * uSize.x, (1.0 - vUv.y) * uSize.y);
  vec2 g = floor(px / uCell); vec2 c = (g + .5) * uCell;
  float t = c.y / uSize.y;
  float d = texture2D(uDense, vec2((g.x + .5) / uGrid.x, 1.0 - (g.y + .5) / uGrid.y)).r;
  float md = length(c - uMouse);
  d *= smoothstep(26., 170., md);
  float r = sqrt(clamp(d,0.,1.)) * uCell * .44;
  float dist = length(px - c);
  float a = 1. - smoothstep(r - uAA, r + uAA, dist);
  float hot = step(.985, h(g + floor(uTime*3.)*.37)) * step(.55, t) * uRain;
  vec3 col = mix(uInk, uAcc, hot);
  if (a < .01) discard;
  gl_FragColor = vec4(col, a);
}`

  function initGL() {
    renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true, alpha: false })
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace
    scene = new THREE.Scene()
    camera = new THREE.PerspectiveCamera(28, 1, 10, 20000)
    // r155+ dropped the legacy ×π light scale; restore it so the tiles shade as they did on r128
    scene.add(new THREE.AmbientLight(0xffffff, .62 * Math.PI))
    const dl = new THREE.DirectionalLight(0xffffff, .55 * Math.PI); dl.position.set(-.5, .8, 1); scene.add(dl)
    cloud = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
      uniforms: { uDense: { value: null }, uSize: { value: new THREE.Vector2(1, 1) }, uGrid: { value: new THREE.Vector2(1, 1) }, uTime: { value: 0 }, uMouse: { value: new THREE.Vector2(-9999, -9999) }, uCell: { value: 9 }, uAA: { value: .8 },
        uInk: { value: new THREE.Color() }, uAcc: { value: new THREE.Color() }, uRain: { value: 1 } },
    }))
    cloud.position.z = -80; scene.add(cloud)
    // the density target: one texel per halftone cell
    denseCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
    denseScene = new THREE.Scene()
    dense = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: DENSE_FRAG, depthTest: false, depthWrite: false,
      uniforms: { uSize: { value: new THREE.Vector2(1, 1) }, uGrid: { value: new THREE.Vector2(1, 1) }, uTime: { value: 0 }, uCell: { value: 9 }, uFill: { value: 1 } },
    }))
    denseScene.add(dense)
    tiles = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshLambertMaterial({ color: 0xffffff }), NT)
    tiles.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    for (let i = 0; i < NT; i++) tiles.setColorAt(i, new THREE.Color(0x000000))
    scene.add(tiles)
    drops = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: 0x2244cc }), ND)
    drops.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    scene.add(drops)
    readColors()
    layout()
  }

  function layout() {
    if (!renderer) return
    const r = stage.getBoundingClientRect()
    Wd = Math.max(300, r.width); Ht = Math.max(300, r.height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(Wd, Ht, false)
    camera.aspect = Wd / Ht
    const camDist = (Ht / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))
    camera.position.set(0, 0, camDist); camera.lookAt(0, 0, 0); camera.updateProjectionMatrix()
    const narrow = Wd < 640
    L.groundY = Ht - 52
    L.cloudH = Ht * .5
    L.plotL = narrow ? 4 : 24; L.plotR = Wd - L.plotL
    L.pitch = narrow ? 6 : Wd > 1100 ? 8 : 7
    L.tile = L.pitch * .78
    L.maxH = L.groundY - Ht * .44
    const k = cloud.position.z, sc = (camDist - k) / camDist
    cloud.scale.set(Wd * 1.08 * sc, L.cloudH * sc, 1)
    cloud.position.x = 0; cloud.position.y = (Ht / 2 - L.cloudH / 2) * sc + 6
    const cell = narrow ? 7 : 9, sw = Wd * 1.08, sh = L.cloudH
    const gw = Math.ceil(sw / cell), gh = Math.ceil(sh / cell)
    const u = cloud.material.uniforms
    u.uSize.value.set(sw, sh); u.uGrid.value.set(gw, gh); u.uCell.value = cell
    u.uAA.value = .9 / renderer.getPixelRatio() + .3
    const d = dense.material.uniforms
    d.uSize.value.set(sw, sh); d.uGrid.value.set(gw, gh); d.uCell.value = cell
    if (denseRT) denseRT.dispose()
    denseRT = new THREE.WebGLRenderTarget(gw, gh, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: false, stencilBuffer: false })
    u.uDense.value = denseRT.texture
    densityDirty = true
    buildCols(); retarget(true); needsRender = true
  }

  function colGeom() {
    const n = nGroups(), cw = (L.plotR - L.plotL) / n
    const k = Math.max(2, Math.min(14, Math.floor(cw * .62 / L.pitch)))
    const rows = Math.max(4, Math.floor(L.maxH / L.pitch))
    return { n, cw, k, rows, cap: Math.min(MAXPER, k * rows) }
  }
  function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), m = x / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p }

  function retarget(snap) {
    const g = colGeom()
    let mx = 0; for (let c = 0; c < g.n; c++) mx = Math.max(mx, S.acc[c])
    const minUnit = S.meas === 'rev' ? 5e4 : 50
    unit = Math.max(minUnit, niceStep(Math.max(1, mx) / g.cap))
    $('#unit').textContent = '■ = ' + (S.meas === 'rev' ? fmtUsd(unit) : fmtInt(unit) + ' orders')
    for (let c = 0; c < MAXG; c++) {
      const cnt = c < g.n ? Math.min(g.cap, Math.round(S.acc[c] / unit)) : 0
      const cx = L.plotL + (c + .5) * g.cw
      for (let i = 0; i < MAXPER; i++) {
        const id = c * MAXPER + i
        if (i < cnt) {
          const row = Math.floor(i / g.k), j = i % g.k
          const x = cx - g.k * L.pitch / 2 + (j + .5) * L.pitch
          const y = L.groundY - (row + .5) * L.pitch
          if (tc.ts[id] === 0 && tc.s[id] < .01) { tc.x[id] = x; tc.y[id] = y - 30; tc.d[id] = snap ? 0 : Math.random() * .12 }
          else if (!snap) tc.d[id] = (tc.tx[id] !== x || tc.ty[id] !== y) ? (row / g.rows) * .25 + j * .004 : 0
          tc.tx[id] = x; tc.ty[id] = y; tc.ts[id] = 1
        } else { tc.ts[id] = 0 }
        if (snap) { tc.x[id] = tc.tx[id]; tc.y[id] = tc.ty[id]; tc.s[id] = tc.ts[id]; tc.d[id] = 0 }
      }
      counts[c] = cnt
    }
    tilesMoving = true; needsRender = true
    updateCols(accTotal())
  }
  function stackTop(c) { const g = colGeom(); return L.groundY - Math.ceil(counts[c] / g.k) * L.pitch }

  /* column labels double as buttons: the keyboard twin of clicking a bar */
  const colsEl = $('#cols')
  function buildCols() {
    const g = colGeom(), narrow = Wd < 640, labels = DIMS[S.dim]
    colsEl.innerHTML = labels.map((d, c) => `<div style="left:${L.plotL + (c + .5) * g.cw}px"><button type="button" data-c="${c}" aria-pressed="false" aria-label="${d[1]}, filter the chart"><b>${narrow ? d[0] : d[1]}</b><span class="num">—</span></button></div>`).join('')
  }
  function updateCols(total) {
    const spans = colsEl.querySelectorAll('span'), divs = colsEl.children
    spans.forEach((s, c) => { const v = S.acc[c]; s.textContent = Wd < 640 ? (total ? Math.round(v / total * 100) + '%' : '—') : (S.meas === 'rev' ? fmtUsd(v) : fmtInt(v)) })
    for (let c = 0; c < divs.length; c++) { divs[c].classList.toggle('on', c === S.pinned || c === S.hover); divs[c].firstChild.setAttribute('aria-pressed', String(c === S.pinned)) }
  }
  function pin(c) {
    S.pinned = c === S.pinned ? -1 : c
    updateCols(accTotal()); needsRender = true
    if (S.pinned >= 0) toast(`Filtered to ${DIMS[S.dim][S.pinned][1]} · in this tab, no query sent`)
  }
  on(colsEl, 'click', e => { const b = e.target.closest('button[data-c]'); if (b) { e.stopPropagation(); pin(+b.dataset.c) } })

  /* rain */
  let spawnAcc = 0
  const spawnRate = () => S.mode === 'cold' ? 46 : 190
  function spawnDrop() {
    if (S.spawned >= NB) return
    const slot = dr.b.indexOf(-1); if (slot < 0) return
    const g = colGeom(), c = Math.floor(rnd() * g.n)
    const cx = L.plotL + (c + .5) * g.cw
    dr.x[slot] = cx + (rnd() - .5) * g.k * L.pitch * .9
    dr.y[slot] = Ht * (.16 + rnd() * .2)
    dr.v[slot] = 120 + rnd() * 140
    dr.c[slot] = c; dr.b[slot] = S.spawned++
  }
  let dirtyTargets = false, tilesMoving = true
  function land(slot) {
    const b = dr.b[slot]; dr.b[slot] = -1
    if (S.landed[b]) return
    const t0 = performance.now(); aggBatch(b); S.lastAgg = performance.now() - t0; S.lastAggRows = BATCH
    S.landed[b] = 1; S.nLanded++
    if (S.mode === 'cold') S.fill = Math.max(.58, S.fill - .0016)
    dirtyTargets = true
    if (S.nLanded === NB) finished()
  }
  function finished() {
    $('#sdot').classList.remove('live')
    $('#stext').textContent = S.mode === 'cold' ? 'Complete · result cached at the edge' : 'Complete · served from edge cache'
  }
  function start(mode) {
    S.mode = mode; S.landed.fill(0); S.nLanded = 0; S.spawned = 0; S.acc.fill(0); dr.b.fill(-1)
    if (mode === 'cold') { S.queries++; S.fill = 1 } else S.cacheHits++
    $('#sdot').classList.add('live')
    $('#stext').textContent = mode === 'cold' ? 'Streaming from warehouse' : 'Streaming from edge cache'
    $('#r-wq').textContent = S.queries; $('#r-wq-s').textContent = mode === 'cold' ? 'cold run · cache miss' : `cache hit ×${S.cacheHits} · no new query`
    retarget(false)
    if (RM.matches) instantLand(NB)
    wake()
  }
  function instantLand(n) { for (let b = 0; b < n; b++) { S.landed[b] = 1 } S.nLanded = n; S.spawned = n; reaggregate(); retarget(true); densityDirty = true; if (n === NB) finished(); wake() }

  /* readouts */
  function readouts() {
    $('#r-rows').textContent = fmtInt(S.nLanded * BATCH)
    $('#r-rows-s').textContent = `of 1,048,576 · ${S.nLanded} / ${NB} batches`
    $('#r-bytes').textContent = (S.nLanded * BATCH * BYTES_PER_ROW / 1048576).toFixed(1) + ' MB'
    $('#r-agg').textContent = S.lastAgg.toFixed(S.lastAgg < 10 ? 2 : 1) + ' ms'
    $('#r-agg-s').textContent = `${fmtInt(S.lastAggRows)} rows, measured here`
  }

  /* tile colours */
  function paintTiles() {
    for (let c = 0; c < MAXG; c++) {
      const want = S.pinned >= 0 ? (c === S.pinned ? 1 : 2) : (c === S.hover ? 1 : 0)
      const clr = want === 1 ? colors.accent : want === 2 ? colors.ink3 : colors.ink
      for (let i = 0; i < MAXPER; i++) { const id = c * MAXPER + i; if (tc.colr[id] !== want) { tiles.setColorAt(id, clr); tc.colr[id] = want } }
    }
    tiles.instanceColor.needsUpdate = true
  }
  let lastPaint = ''

  /* frame: runs only while the figure is on screen, renders only when something changed */
  let last = performance.now(), tAcc = 0, roT = 0, loopOn = false
  function wake() { needsRender = true; if (!loopOn && gl3 && PVIS.on && !glLost && !doc.hidden) { loopOn = true; last = performance.now(); raf(frame) } }
  function frame(now) {
    if (!PVIS.on || glLost || doc.hidden) { loopOn = false; return }
    const dt = Math.min(.05, (now - last) / 1000); last = now
    const reduced = RM.matches
    let busy = false
    if (!reduced) {
      if (S.spawned < NB) { spawnAcc += dt * spawnRate(); while (spawnAcc >= 1) { spawnDrop(); spawnAcc -= 1 } }
      activeDrops = 0
      for (let i = 0; i < ND; i++) {
        if (dr.b[i] < 0) continue
        activeDrops++
        dr.v[i] += 900 * dt; dr.y[i] += dr.v[i] * dt
        if (dr.y[i] >= stackTop(dr.c[i]) - 2) land(i)
      }
      busy = activeDrops > 0 || S.spawned < NB
    }
    if (dirtyTargets) { dirtyTargets = false; retarget(false) }

    // tiles: ease toward their targets, upload only when something moved
    if (tilesMoving) {
      const arr = tiles.instanceMatrix.array, k = 1 - Math.pow(.0009, dt)
      let moved = false
      for (let id = 0; id < NT; id++) {
        if (tc.d[id] > 0) { tc.d[id] -= dt; moved = true; continue }
        let s = tc.s[id]
        if (tc.ts[id] === 0 && s < .002) { if (arr[id * 16] !== 0) { arr.fill(0, id * 16, id * 16 + 16); moved = true } continue }
        const ox = tc.x[id], oy = tc.y[id], os = s
        tc.x[id] += (tc.tx[id] - tc.x[id]) * k; tc.y[id] += (tc.ty[id] - tc.y[id]) * k; s = tc.s[id] += (tc.ts[id] - s) * k
        if (Math.abs(tc.x[id] - ox) + Math.abs(tc.y[id] - oy) + Math.abs(s - os) > 1e-4 || Math.abs(tc.tx[id] - tc.x[id]) > .02 || Math.abs(tc.ty[id] - tc.y[id]) > .02) moved = true
        const sz = L.tile * s, o = id * 16
        arr[o] = sz; arr[o + 1] = 0; arr[o + 2] = 0; arr[o + 3] = 0; arr[o + 4] = 0; arr[o + 5] = sz; arr[o + 6] = 0; arr[o + 7] = 0
        arr[o + 8] = 0; arr[o + 9] = 0; arr[o + 10] = sz; arr[o + 11] = 0
        arr[o + 12] = tc.x[id] - Wd / 2; arr[o + 13] = Ht / 2 - tc.y[id]; arr[o + 14] = 0; arr[o + 15] = 1
      }
      tiles.instanceMatrix.needsUpdate = true
      tilesMoving = moved; if (moved) { busy = true; needsRender = true }
    }
    // drops matrices (only while some are falling)
    if (activeDrops > 0 || drops.userData.had) {
      const da = drops.instanceMatrix.array
      for (let i = 0; i < ND; i++) {
        const o = i * 16
        if (dr.b[i] < 0) { da.fill(0, o, o + 16); continue }
        const len = Math.min(22, 6 + dr.v[i] * .02)
        da[o] = 1.6; da[o + 1] = 0; da[o + 2] = 0; da[o + 3] = 0; da[o + 4] = 0; da[o + 5] = len; da[o + 6] = 0; da[o + 7] = 0; da[o + 8] = 0; da[o + 9] = 0; da[o + 10] = 1.6; da[o + 11] = 0
        da[o + 12] = dr.x[i] - Wd / 2; da[o + 13] = Ht / 2 - dr.y[i] + len / 2; da[o + 14] = 2; da[o + 15] = 1
      }
      drops.instanceMatrix.needsUpdate = true; drops.userData.had = activeDrops > 0; needsRender = true
    }
    const key = S.pinned + ':' + S.hover
    if (key !== lastPaint || tc.colr[0] === -2) { paintTiles(); lastPaint = key; needsRender = true }

    // cloud: the density target refreshes at ~15 fps (or once, with reduced motion), the display pass follows the mouse
    tAcc += dt
    const u = cloud.material.uniforms
    const fillErr = Math.abs(S.fill - dense.material.uniforms.uFill.value)
    if (fillErr > .002) { dense.material.uniforms.uFill.value += (S.fill - dense.material.uniforms.uFill.value) * (1 - Math.pow(.0009, dt)) * .5; densityDirty = true }
    if (densityDirty || (!reduced && now - densityAt > 66)) {
      dense.material.uniforms.uTime.value = reduced ? 0 : tAcc
      renderer.setRenderTarget(denseRT); renderer.render(denseScene, denseCam); renderer.setRenderTarget(null)
      densityAt = now; densityDirty = false; needsRender = true
    }
    u.uTime.value = reduced ? 0 : tAcc
    u.uRain.value = S.spawned < NB ? 1 : 0
    const mx = pointer.in ? pointer.x + Wd * .04 : -9999, my = pointer.in ? pointer.y : -9999
    const nx = u.uMouse.value.x + (mx - u.uMouse.value.x) * (pointer.in ? .2 : 1), ny = u.uMouse.value.y + (my - u.uMouse.value.y) * (pointer.in ? .2 : 1)
    if (Math.abs(nx - u.uMouse.value.x) + Math.abs(ny - u.uMouse.value.y) > .05) { u.uMouse.value.set(nx, ny); needsRender = true }
    if (!reduced) {
      const tx = pointer.in ? pointer.nx * 46 : 0, ty = pointer.in ? -pointer.ny * 26 : 0
      const k2 = 1 - Math.pow(.0009, dt)
      const ox = camera.position.x, oy = camera.position.y
      camera.position.x += (tx - camera.position.x) * k2 * .4; camera.position.y += (ty - camera.position.y) * k2 * .4
      if (Math.abs(camera.position.x - ox) + Math.abs(camera.position.y - oy) > 1e-3) { camera.lookAt(0, 0, 0); needsRender = true }
    }
    if (needsRender) { needsRender = false; renderer.render(scene, camera) }
    roT += dt; if (roT > .08) { roT = 0; readouts() }
    // idle: with reduced motion the loop stops once nothing moves; otherwise it keeps the cloud drifting at ~15 fps
    if (reduced && !busy && !densityDirty && !pointer.in) { loopOn = false; readouts(); return }
    raf(frame)
  }

  /* pointer */
  function colAt(x, y) {
    if (y < Ht * .4 || y > Ht) return -1
    const g = colGeom(), c = Math.floor((x - L.plotL) / g.cw)
    return c >= 0 && c < g.n ? c : -1
  }
  const tip = $('#tip')
  function showTip(c) {
    if (c < 0) { tip.hidden = true; return }
    const g = colGeom(), total = accTotal(), v = S.acc[c], d = DIMS[S.dim][c]
    tip.innerHTML = `<span>${d[1]}</span><b>${S.meas === 'rev' ? fmtUsd(v) : fmtInt(v) + ' orders'}</b><small>${total ? (v / total * 100).toFixed(1) : '0.0'}% of rows landed so far</small>`
    tip.style.left = Math.min(Wd - 90, Math.max(90, L.plotL + (c + .5) * g.cw)) + 'px'
    tip.style.top = Math.max(70, stackTop(c) - 12) + 'px'
    tip.hidden = false
  }
  on(stage, 'pointermove', e => {
    const r = stage.getBoundingClientRect(); pointer.x = e.clientX - r.left; pointer.y = e.clientY - r.top
    pointer.nx = pointer.x / r.width * 2 - 1; pointer.ny = pointer.y / r.height * 2 - 1; pointer.in = true
    const c = colAt(pointer.x, pointer.y); if (c !== S.hover) { S.hover = c; updateCols(accTotal()) }
    showTip(c); wake()
  })
  on(stage, 'pointerleave', () => { pointer.in = false; S.hover = -1; tip.hidden = true; updateCols(accTotal()); wake() })
  on(stage, 'click', e => {
    const r = stage.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top
    const c = colAt(x, y); if (c >= 0) pin(c)
  })

  /* controls */
  function seg(id, fn) {
    const el = $(id)
    on(el, 'click', e => { const b = e.target.closest('button'); if (!b) return; el.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); fn(b.dataset.v) })
  }
  function setDim(v) {
    if (!gl3) return
    S.dim = v; S.pinned = -1; S.hover = -1; reaggregate(); buildCols(); retarget(false); wake()
    $('#seg-dim').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.v === v)))
    toast(`Regrouped ${fmtInt(S.lastAggRows)} rows in ${S.lastAgg.toFixed(1)} ms · warehouse not asked`)
  }
  function setMeas(v) { if (!gl3) return; S.meas = v; reaggregate(); retarget(false); wake(); toast(`Re-aggregated in ${S.lastAgg.toFixed(1)} ms in this tab`) }
  seg('#seg-dim', setDim)
  seg('#seg-meas', setMeas)
  on($('#rerun'), 'click', () => gl3 && start('cache'))
  on($('#purge'), 'click', () => { if (!gl3) return; start('cold'); toast('Cache cleared · next run goes to the warehouse') })

  if (gl3) {
    try {
      initGL()
      instantLand(Math.round(NB * .42)); S.spawned = S.nLanded; S.fill = .82
      dense.material.uniforms.uFill.value = S.fill
      $('#sdot').classList.add('live'); $('#stext').textContent = 'Streaming from warehouse'
      if (RM.matches) instantLand(NB)
      readouts()
      watch(window.ResizeObserver, () => layout()).observe(stage)
      watch(window.IntersectionObserver, es => es.forEach(en => { PVIS.on = en.isIntersecting; if (PVIS.on) wake() })).observe(stage)
      on(doc, 'visibilitychange', () => { if (!doc.hidden) wake() })
      on(RM, 'change', () => { if (RM.matches) instantLand(NB); wake() })
      // moving the window to another display (or zooming) changes the pixel ratio
      let dprMq = null
      const onDpr = () => { watchDpr(); layout() }
      const watchDpr = () => { if (dprMq) dprMq.removeEventListener('change', onDpr); dprMq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`); dprMq.addEventListener('change', onDpr) }
      watchDpr(); cleanups.push(() => dprMq && dprMq.removeEventListener('change', onDpr))
      // WebGL context loss: stop, then rebuild GPU state on restore
      on(cv, 'webglcontextlost', e => { e.preventDefault(); glLost = true })
      on(cv, 'webglcontextrestored', () => { glLost = false; tc.colr.fill(-2); tiles.instanceMatrix.needsUpdate = true; readColors(); layout(); densityDirty = true; wake() })
    } catch (e) { gl3 = null; console.warn(e) }
  }
  if (!gl3) { cv.hidden = true; const d = doc.createElement('div'); d.className = 'nogl'; d.textContent = 'This figure needs WebGL. The rest of the page works without it.'; stage.appendChild(d) }

  /* ================= embed ================= */
  const E = { theme: 'light', board: 'brd_sales', n: 0 }
  const BOARDS = {
    brd_sales: { title: 'Sales report', kpis: [['Revenue Q3', '$21.4M', '+8.2% vs Q2'], ['Orders', '184,207', '+3.1%'], ['Avg basket', '$116', '+4.9%']], series: [38, 41, 40, 44, 47, 46, 52, 55, 53, 58, 61, 64], label: 'Weekly revenue, $M (sample)' },
    brd_ops: { title: 'Operations', kpis: [['On-time delivery', '94.2%', '+1.4 pts'], ['Stockouts', '37', '−12'], ['Pick rate', '118/h', '+6']], series: [88, 90, 89, 91, 93, 92, 93, 95, 94, 94, 96, 94], label: 'On-time delivery by week, % (sample)' },
  }
  function snippet() {
    const a = (k, v) => `  <span class="k">${k}</span>=<span class="s">"${v}"</span>`
    return [
      '<span class="k">&lt;script</span> src=<span class="s">"https://cdn.example.com/dist-embed/nubi-dashboard.js"</span><span class="k">&gt;&lt;/script&gt;</span>',
      '<span class="k">&lt;nubi-dashboard</span>',
      `<span class="hl">${a('dashboard-id', E.board)}</span>`,
      a('get-token', 'getEmbedToken'),
      a('backend', 'https://api.example.com'),
      `<span class="hl">${a('theme', E.theme)}</span>`,
      '<span class="k">&gt;&lt;/nubi-dashboard&gt;</span>',
    ].join('\n')
  }
  let embedT = 0
  function renderEmbed() {
    $('#snippet').innerHTML = snippet()
    const B = BOARDS[E.board], nd = $('#nd')
    nd.className = 'nd ' + (E.theme === 'light' ? 'light' : '')
    $('#hosth').textContent = B.title
    nd.innerHTML = `<span class="tag">&lt;nubi-dashboard dashboard-id="${E.board}"&gt;</span><div class="kpis">${'<div class="kpi"><i class="sk" style="width:60%"></i><i class="sk" style="height:20px;width:80%"></i><i class="sk" style="width:40%"></i></div>'.repeat(3)}</div><div class="chart"><i class="sk" style="width:45%;margin-bottom:10px"></i><i class="sk" style="height:120px"></i></div>`
    clearTimeout(embedT); embedT = later(() => fillEmbed(B), RM.matches ? 0 : 520)
  }
  function fillEmbed(B) {
    const nd = $('#nd')
    const s = B.series, mx = Math.max(...s), mn = Math.min(...s) * .9, w = 320, h = 120, bw = w / s.length
    const bars = s.map((v, i) => { const bh = (v - mn) / (mx - mn) * (h - 18); return `<rect x="${i * bw + 3}" y="${h - bh}" width="${bw - 6}" height="${bh}" fill="${i === s.length - 1 ? 'var(--accent)' : 'currentColor'}" opacity="${i === s.length - 1 ? 1 : .82}"/>` }).join('')
    nd.innerHTML = `<span class="tag">&lt;nubi-dashboard dashboard-id="${E.board}"&gt;</span>
    <div class="kpis">${B.kpis.map(k => `<div class="kpi"><span>${k[0]}</span><b>${k[1]}</b><i>${k[2]}</i></div>`).join('')}</div>
    <div class="chart"><div class="muted" style="margin-bottom:8px">${B.label}</div><svg viewBox="0 0 ${w} ${h + 16}" role="img" aria-label="${B.label}">${bars}<line x1="0" x2="${w}" y1="${h}" y2="${h}" stroke="var(--line2)"/><text x="0" y="${h + 13}" font-size="9" fill="var(--mute)">wk 27</text><text x="${w}" y="${h + 13}" font-size="9" fill="var(--mute)" text-anchor="end">wk 38</text></svg></div>`
    E.n++
    const hit = E.n > 1, ms = hit ? 2 + Math.floor(Math.random() * 4) : 41
    const ev = doc.createElement('div')
    ev.innerHTML = `<i>${new Date().toLocaleTimeString('en-GB')}</i><span>nubi:query-run {rowCount: ${B.series.length}, cacheStatus: "${hit ? 'hit' : 'miss'}", elapsedMs: ${ms}}</span>`
    const log = $('#events'); log.prepend(ev); while (log.children.length > 6) log.lastChild.remove()
  }
  seg('#seg-etheme', v => { E.theme = v; renderEmbed() })
  seg('#seg-eboard', v => { E.board = v; renderEmbed() })
  function selectEl(s) { const r = doc.createRange(); r.selectNodeContents($(s)); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r); toast('Selected · press Ctrl C to copy') }
  function copyFrom(sel, done) {
    const txt = $(sel).textContent
    try { navigator.clipboard.writeText(txt).then(() => toast(done), () => selectEl(sel)) } catch { selectEl(sel) }
  }
  on($('#copy'), 'click', () => copyFrom('#snippet', 'Snippet copied'))
  const copySh = $('#copy-sh'); if (copySh) on(copySh, 'click', () => copyFrom('#sh-cmd', 'Commands copied'))
  renderEmbed()

  /* ================= agent transcript replay ================= */
  const replayBtn = $('#replay')
  if (replayBtn) on(replayBtn, 'click', () => {
    const lns = [...root.querySelectorAll('#log .ln')]
    lns.forEach((l, i) => { if (i) l.classList.add('wait') })
    lns.forEach((l, i) => later(() => l.classList.remove('wait'), RM.matches ? 0 : i * 520))
  })

  /* ================= dot-matrix wordmark: fine particles that gather on arrival and part around the cursor ================= */
  function sizeCanvas(c) { const r = c.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2); c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); return r }
  const wm = $('#wm'), wx2 = wm.getContext('2d'), off = doc.createElement('canvas')
  const wmP = { x: -999, y: -999 }
  let dots = [], wmR = null, wmRun = false, wmBorn = 0, wmSeen = false, wmImg = null
  // The particles are sampled from the brand lockup ([mark]ubi, public/brand/nubi-lockup.svg); the text is only a fallback.
  function loadWordmark() {
    return new Promise(res => { const img = new Image(); img.onload = () => res(img.naturalWidth > 0 ? img : null); img.onerror = () => res(null); img.src = '/brand/nubi-lockup.svg' })
  }
  function buildWM() {
    if (!alive) return
    wmR = sizeCanvas(wm)
    const w = Math.round(wmR.width), h = Math.round(wmR.height)
    if (w < 8 || h < 8) { dots = []; wmRun = false; return }                       // not laid out yet: nothing to sample
    off.width = w; off.height = h; const o = off.getContext('2d', { willReadFrequently: true })
    o.clearRect(0, 0, w, h); o.fillStyle = '#000'; o.textBaseline = 'alphabetic'
    if (wmImg) {
      const ar = wmImg.naturalWidth / wmImg.naturalHeight
      const dh = Math.min(h, w * .98 / ar), dw = dh * ar
      o.drawImage(wmImg, 0, (h - dh) / 2, dw, dh)
    } else {
      let size = h * 1.3
      o.font = `600 ${size}px "Instrument Sans", "Helvetica Neue", Arial, sans-serif`
      const tw = o.measureText('nubi').width; if (tw > w * .98) { size *= w * .98 / tw; o.font = `600 ${size}px "Instrument Sans", "Helvetica Neue", Arial, sans-serif` }
      o.fillText('nubi', 0, h * .94)
    }
    let data
    try { data = o.getImageData(0, 0, w, h).data } catch { dots = []; return }
    const cell = w < 600 ? 3.6 : 5
    dots = []
    for (let y = cell / 2; y < h; y += cell) for (let x = cell / 2; x < w; x += cell) {
      const a = data[(Math.floor(y) * w + Math.floor(x)) * 4 + 3] / 255
      if (a < .15) continue
      const ang = Math.random() * 6.283, far = 40 + Math.random() * 160
      dots.push({ hx: x, hy: y, x: x + Math.cos(ang) * far, y: y + Math.sin(ang) * far - 60, r: a * cell * .4, d: Math.random() * .5 + x / w * .4 })
    }
    if (RM.matches || wmSeen) dots.forEach(p => { p.x = p.hx; p.y = p.hy })
    kickWM()
  }
  function kickWM() { if (!wmRun && alive) { wmRun = true; raf(stepWM) } }
  function stepWM(now) {
    if (!wmR || doc.hidden) { wmRun = false; return }
    const ink = css('--ink'), acc = css('--accent')
    const born = wmSeen ? (now - wmBorn) / 1000 : 0
    wx2.clearRect(0, 0, wmR.width, wmR.height)
    let moving = false
    wx2.fillStyle = ink; wx2.beginPath()
    const hot = []
    for (const p of dots) {
      let tx = p.hx, ty = p.hy
      const dx = p.hx - wmP.x, dy = p.hy - wmP.y, d = Math.hypot(dx, dy)
      if (d < 90) { const f = (1 - d / 90) ** 2 * 18; tx += dx / (d || 1) * f; ty += dy / (d || 1) * f }
      const k = wmSeen && born > p.d ? .2 : 0
      p.x += (tx - p.x) * k; p.y += (ty - p.y) * k
      if (Math.abs(tx - p.x) + Math.abs(ty - p.y) > .2) moving = true
      if (d < 90) { hot.push(p); continue }
      wx2.moveTo(p.x + p.r, p.y); wx2.arc(p.x, p.y, p.r, 0, 6.283)
    }
    wx2.fill()
    if (hot.length) { wx2.fillStyle = acc; wx2.beginPath(); for (const p of hot) { wx2.moveTo(p.x + p.r, p.y); wx2.arc(p.x, p.y, p.r, 0, 6.283) } wx2.fill() }
    if (moving || !wmSeen || born < 1.6) raf(stepWM); else wmRun = false
  }
  on(wm, 'pointermove', e => { const r = wm.getBoundingClientRect(); wmP.x = e.clientX - r.left; wmP.y = e.clientY - r.top; kickWM() })
  on(wm, 'pointerleave', () => { wmP.x = wmP.y = -999; kickWM() })
  watch(window.ResizeObserver, () => buildWM()).observe(wm)
  watch(window.IntersectionObserver, es => es.forEach(en => { if (en.isIntersecting && !wmSeen) { wmSeen = true; wmBorn = performance.now(); kickWM() } }), { threshold: .3 }).observe(wm)
  loadWordmark().then(img => { wmImg = img; if (alive) buildWM() })
  if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(() => { if (alive && !wmImg) buildWM() })

  /* ================= theme changes ================= */
  function onTheme() { if (gl3 && renderer) readColors(); kickWM(); wake() }
  on(window.matchMedia('(prefers-color-scheme: dark)'), 'change', onTheme)
  watch(window.MutationObserver, onTheme).observe(doc.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })

  /* ================= palette actions (the palette itself lives in chrome.js) ================= */
  const jump = id => () => root.__nl && root.__nl.jump(id)
  if (root.__nl) root.__nl.addCommands([
    ['Group the live chart by channel', 'Action', () => { jump('live')(); setDim('chan') }],
    ['Run the live query again from cache', 'Action', () => { jump('live')(); gl3 && start('cache') }],
    ['Clear the edge cache', 'Action', () => { jump('live')(); gl3 && start('cold') }],
    ['Embed in dark theme', 'Action', () => { jump('embed')(); $('#seg-etheme [data-v="dark"]').click() }],
  ])

  return () => {
    alive = false
    cleanups.forEach(f => { try { f() } catch { /* already gone */ } })
    if (denseRT) denseRT.dispose()
    if (renderer) { renderer.dispose(); renderer.forceContextLoss() }
  }
}
