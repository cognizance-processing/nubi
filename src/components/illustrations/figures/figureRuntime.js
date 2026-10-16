/**
 * figureRuntime — the small shared runtime behind every live docs/landing figure.
 *
 * What it owns, so figures do not each hand-roll it:
 *   - render on demand: `frame(now, dt)` runs only while it returns true (still
 *     animating) or inside an `invalidate(ms)` window. A settled figure costs 0 GPU.
 *   - pause while offscreen (IntersectionObserver, 80px margin) and on hidden tabs.
 *   - ResizeObserver (rAF-coalesced) plus devicePixelRatio changes (zoom, moving
 *     the window between monitors) -> `resize(width, height, dpr)`; dpr capped at 2.
 *   - WebGL context loss: stop, `preventDefault`, and on restore call `restore()`.
 *   - prefers-reduced-motion as a LIVE query: `reduced()`; `onMotionPref` fires on change.
 *   - `dispose()` removes every listener and observer.
 *
 * createFigureRuntime({ el, canvas?, frame?, resize?, restore?, onLost?, onMotionPref?, dprCap? })
 *   -> { invalidate(ms?), reduced(), dpr(), visible(), dispose() }
 */
export function createFigureRuntime({ el, canvas, frame, resize, restore, onLost, onMotionPref, dprCap = 2, margin = 80 }) {
  let alive = true, raf = 0, rafResize = 0, visible = true, tabHidden = document.hidden, lost = false, keepUntil = 0, last = 0
  const off = []
  const on = (t, type, fn, o) => { t.addEventListener(type, fn, o); off.push(() => t.removeEventListener(type, fn, o)) }
  const dpr = () => Math.min(window.devicePixelRatio || 1, dprCap)
  const canRun = () => alive && visible && !tabHidden && !lost

  function tick(now) {
    raf = 0
    if (!alive || !canRun()) { last = 0; return }
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60
    last = now
    let busy = false
    try { busy = !!(frame && frame(now, dt)) } catch (e) { console.warn('[figure] frame failed', e); return }
    if (busy || now < keepUntil) schedule(); else last = 0
  }
  function schedule() { if (!raf && canRun() && frame) raf = window.requestAnimationFrame(tick) }
  function invalidate(ms = 0) { keepUntil = Math.max(keepUntil, performance.now() + ms); schedule() }

  function doResize() {
    rafResize = 0
    if (!alive || !resize) return
    const r = el.getBoundingClientRect()
    resize(r.width, r.height, dpr())
    invalidate(120)
  }
  function queueResize() { if (!rafResize) rafResize = window.requestAnimationFrame(doResize) }

  // size + devicePixelRatio
  const ro = new window.ResizeObserver(queueResize); ro.observe(el); off.push(() => ro.disconnect())
  let mqDpr = null
  const watchDpr = () => {
    mqDpr = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
    const h = () => { queueResize(); watchDpr() }
    mqDpr.addEventListener('change', h, { once: true })
    off.push(() => mqDpr.removeEventListener('change', h))
  }
  watchDpr()

  // visibility
  const io = new window.IntersectionObserver(es => {
    visible = es[es.length - 1].isIntersecting
    if (visible) invalidate(200)
  }, { rootMargin: `${margin}px` })
  io.observe(el); off.push(() => io.disconnect())
  on(document, 'visibilitychange', () => { tabHidden = document.hidden; if (!tabHidden) invalidate(200) })

  // live reduced-motion preference
  const mqRM = window.matchMedia('(prefers-reduced-motion: reduce)')
  on(mqRM, 'change', () => { onMotionPref && onMotionPref(mqRM.matches); invalidate(300) })

  // WebGL context loss / restore
  if (canvas) {
    on(canvas, 'webglcontextlost', e => { e.preventDefault(); lost = true; if (raf) { cancelAnimationFrame(raf); raf = 0 } onLost && onLost() })
    on(canvas, 'webglcontextrestored', () => { lost = false; restore && restore(); queueResize(); invalidate(400) })
  }

  queueResize()
  invalidate(400)

  return {
    invalidate,
    reduced: () => mqRM.matches,
    dpr,
    visible: () => visible && !tabHidden,
    dispose() {
      alive = false
      if (raf) cancelAnimationFrame(raf)
      if (rafResize) cancelAnimationFrame(rafResize)
      off.forEach(f => { try { f() } catch { /* already gone */ } })
    },
  }
}
