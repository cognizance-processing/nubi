/**
 * LandingPage — the public home page (Swiss, round 2: "the life of a query").
 *
 *   header           static markup (landingMarkup): [mark]ubi lockup, nav, ⌘K, Sign in, Start free
 *   #story           <Story>: a pinned stage + caption plates (desktop) or a sticky band + chapters (mobile).
 *                    One persistent stage plays six scenes (scenes/<id>/) with the same 4,096 cubes.
 *   outro            static markup: 06 Live · 07 Embed · 08 What you get · 09 Open core · 10 Pricing · 11 Start
 *
 * First paint has the copy and a 2D poster of the hero. Then, lazily (never on other routes):
 *   - the R3F stage chunk (three, @react-three/fiber, the ink shader, the morph engine)
 *   - GSAP ScrollTrigger (+ Lenis on desktop pointers) for scroll → story time T
 *   - landingLive (the Live instrument, the embed demo, the particle wordmark)
 * Every module returns a cleanup, so leaving the route stops every loop and frees WebGL.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { HEADER_MARKUP, OUTRO_MARKUP, OVERLAY_MARKUP } from './landing/landingMarkup'
import { mount as mountChrome } from './landing/chrome.js'
import Footer from '../components/Footer'
import { Story, type StoryApi } from './landing/Story'
import { SCENES } from './landing/stage/registry'
import { compileStory } from './landing/stage/story'
import { Director } from './landing/stage/director'
import { LabelLayer } from './landing/stage/labels'
import { attachPicking } from './landing/stage/pick'
import { mountPosterStage } from './landing/stage/posterStage'
import { gsapScroll, nativeScroll, type ScrollController } from './landing/scroll'
import { applyTheme, installHook, parseDebug, targetT, type Metrics } from './landing/debug'
import './landing/landing.css'

type Mount = (root: HTMLElement) => () => void
const GLStage = lazy(() => import('./landing/stage/Stage'))

function useMedia(q: string) {
  const sub = useCallback((f: () => void) => { const m = window.matchMedia(q); m.addEventListener('change', f); return () => m.removeEventListener('change', f) }, [q])
  return useSyncExternalStore(sub, () => window.matchMedia(q).matches, () => false)
}

function webgl2(): boolean {
  try { const c = document.createElement('canvas'); return !!c.getContext('webgl2') } catch { return false }
}

const HEADER_H = 56

export default function LandingPage() {
  const navigate = useNavigate()
  const debug = useMemo(() => parseDebug(), [])
  useMemo(() => applyTheme(debug.theme), [debug.theme])
  const prefersReduced = useMedia('(prefers-reduced-motion: reduce)')
  const reduced = prefersReduced || debug.rm
  const mobile = useMedia('(max-width: 860px)')
  const story = useMemo(() => compileStory(SCENES), [])
  const director = useMemo(() => new Director(story), [story])
  director.reduced = reduced
  director.mobile = mobile

  const rootRef = useRef<HTMLDivElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const posterRef = useRef<HTMLCanvasElement>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<ScrollController | null>(null)
  const labelLayer = useRef<LabelLayer | null>(null)
  const metricsRef = useRef<Metrics>({ fps: 0, cpuMs: 0, calls: 0, tris: 0, cells: 0, dpr: 1, tier: 'poster' })
  const [glState, setGlState] = useState<'off' | 'loading' | 'ready' | 'lost'>(() => (debug.poster || !webgl2() ? 'off' : 'loading'))
  const readyRef = useRef<{ resolve: () => void; promise: Promise<void> } | null>(null)
  if (!readyRef.current) { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); readyRef.current = { resolve, promise } }

  /* ── chrome (menu, anchors, ⌘K), page title, the outro figures ── */
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    let cancelled = false
    const prevTitle = document.title
    document.title = 'Nubi: open-source BI that runs in your browser'
    const stops: Array<() => void> = []
    const run = (name: string, fn: () => () => void) => { try { stops.push(fn()) } catch (e) { console.warn(`[landing] ${name} failed`, e) } }
    run('chrome', () => mountChrome(root, { navigate }))
    const nl = (root as any).__nl
    if (nl) {
      nl.scrollTo = (id: string) => {
        const el = document.getElementById(id)
        if (el) scrollRef.current ? scrollRef.current.scrollToEl(el) : el.scrollIntoView({ block: 'start' })
      }
      nl.addCommands(story.scenes.map((s, k) => [typeof s.copy.title === 'string' ? s.copy.title.replace(/<[^>]+>/g, '') : s.kicker, `Chapter ${s.index}`, () => api.jumpToScene(k)]))
    }
    const load = (name: string, importer: () => Promise<{ mount: Mount }>) =>
      importer().then(m => { if (!cancelled) run(name, () => m.mount(root)) }).catch(e => console.warn(`[landing] ${name} failed to load`, e))
    const idle = (window as any).requestIdleCallback ?? ((f: () => void) => setTimeout(f, 200))
    idle(() => load('live figure', () => import('./landing/landingLive.js')))
    return () => {
      cancelled = true
      stops.forEach(stop => stop())
      document.title = prevTitle
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate])

  /* ── jumps (beat buttons, rail, ⌘K, focus in an inactive chapter) ── */
  const api: StoryApi = useMemo(() => ({
    jumpToLabel(T, instant) { director.cancelBuild(); scrollRef.current?.scrollToT(T, { instant: instant || reduced }) },
    jumpToScene(si, instant) {
      const l = story.labels.find(x => x.scene === si && x.beat === 0)
      if (l) { director.cancelBuild(); scrollRef.current?.scrollToT(l.T, { instant: instant || reduced }) }
    },
  }), [director, story, reduced])

  /* ── viewport: the pin's size and the stage safe rect (right of the caption plate) ── */
  useEffect(() => {
    const root = rootRef.current
    const pin = root?.querySelector<HTMLElement>('.story-pin')
    if (!root || !pin) return
    const measure = () => {
      const r = pin.getBoundingClientRect()
      const w = Math.round(r.width), h = Math.round(r.height)
      if (mobile) director.vpBase = { w, h, mobile: true, safe: { x0: 8, y0: 8, x1: w - 8, y1: h - 8 } }
      else {
        const plates = root.querySelector<HTMLElement>('.plates')
        const pr = plates?.getBoundingClientRect()
        const plateRight = pr && debug.ui ? pr.right - r.left : 0
        const x0 = debug.ui ? plateRight + 48 : 32
        director.vpBase = { w, h, mobile: false, safe: { x0, y0: (debug.ui ? HEADER_H : 0) + 24, x1: w - (w >= 1200 ? 56 : 32), y1: h - 32 } }
        director.heroBottomExtra = debug.ui ? 64 : 0
        if (labelLayer.current) labelLayer.current.plateRight = debug.ui ? plateRight : 0
      }
      director.emit()
      clearTimeout(refreshT)
      refreshT = window.setTimeout(() => scrollRef.current?.refresh(), 120)   // band / plate size changed → re-measure the scroll map
    }
    let refreshT = 0
    const ro = new ResizeObserver(measure)
    ro.observe(pin)
    const plates = root.querySelector('.plates'); if (plates) ro.observe(plates)
    measure()
    return () => { ro.disconnect(); clearTimeout(refreshT) }
  }, [director, mobile, debug.ui])

  /* ── scroll → T: native first, GSAP (+ Lenis) after first paint ── */
  useEffect(() => {
    const el = document.getElementById('story')
    if (!el) return
    const env = { reduced, mobile, debug: debug.any }
    let ctl: ScrollController = nativeScroll(el, director, env)
    scrollRef.current = ctl
    let dead = false
    const t = setTimeout(() => {
      gsapScroll(el, director, env).then(g => { if (dead) { g.destroy(); return } ctl.destroy(); ctl = g; scrollRef.current = g })
        .catch(e => console.warn('[landing] scroll timeline failed to load', e))
    }, 50)
    return () => { dead = true; clearTimeout(t); ctl.destroy(); if (scrollRef.current === ctl) scrollRef.current = null }
  }, [director, reduced, mobile, debug.any])

  /* ── label layer + picking ── */
  useEffect(() => {
    const el = labelsRef.current
    if (!el) return
    const L = new LabelLayer(el, director)
    L.showCallouts = debug.labels
    labelLayer.current = L
    let raf = 0
    const kick = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; L.update() }) }
    const off = director.on(kick)
    kick()
    return () => { off(); cancelAnimationFrame(raf); L.destroy(); labelLayer.current = null }
  }, [director, debug.labels, mobile])
  useEffect(() => { const h = hostRef.current; return h ? attachPicking(h, director) : undefined }, [director, mobile])

  /* ── poster: always for first paint; the stage itself when ?poster=1, no WebGL or a lost context ── */
  const posterLive = glState !== 'ready'
  useEffect(() => {
    const c = posterRef.current, root = rootRef.current
    if (!c || !root || !posterLive) return
    const ps = mountPosterStage(c, director, { paletteRoot: root, drawTexts: false })
    const mo = new MutationObserver(() => ps.redraw())
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    if (glState === 'off') director.nextFrame().then(() => readyRef.current!.resolve())
    return () => { mo.disconnect(); ps.destroy() }
  }, [director, posterLive, glState, mobile])

  /* ── pause when the pin is offscreen or the tab is hidden ── */
  useEffect(() => {
    const pin = rootRef.current?.querySelector('.story-pin')
    if (!pin) return
    let vis = true
    const upd = () => { director.paused = !vis || document.hidden; if (!director.paused) director.invalidate() }
    const io = new IntersectionObserver(([e]) => { vis = e.isIntersecting; upd() })
    io.observe(pin)
    document.addEventListener('visibilitychange', upd)
    return () => { io.disconnect(); document.removeEventListener('visibilitychange', upd) }
  }, [director, mobile])

  /* ── build-in: plays if the stage is ready within 1.5 s of first paint and nothing has scrolled ── */
  const t0 = useRef(performance.now())
  const onGlReady = useCallback(() => {
    setGlState('ready')
    if (!debug.any && !reduced && window.scrollY < 2 && performance.now() - t0.current < 1500) director.startBuild(story.scenes[0].build?.morph.len ?? 2600)
    readyRef.current!.resolve()
  }, [director, debug.any, reduced, story])
  const onGlLost = useCallback((lost: boolean) => setGlState(lost ? 'lost' : 'ready'), [])

  /* ── debug hook + initial deep link ── */
  useEffect(() => {
    const off = installHook({ director, scroll: () => scrollRef.current, ready: readyRef.current!.promise.then(() => (document as any).fonts?.ready).then(() => director.nextFrame()).then(() => director.nextFrame()), metrics: () => metricsRef.current })
    if (debug.t != null) director.freeze(debug.t)
    if (debug.scene) {
      readyRef.current!.promise.then(async () => {
        const T = targetT(director, debug.scene!, debug.beat != null && /^\d+$/.test(debug.beat) ? +debug.beat : debug.beat ?? 0, debug.p)
        if (T == null) return
        await new Promise(r => setTimeout(r, 120))
        await (window as any).__nubiLanding.goto(debug.scene, debug.beat != null && /^\d+$/.test(debug.beat) ? +debug.beat : debug.beat ?? 0, { p: debug.p, state: debug.state })
      })
    }
    return off
  }, [director, debug])

  /* ── perf overlay ── */
  const [perf, setPerf] = useState('')
  useEffect(() => {
    if (!debug.perf) return
    const id = setInterval(() => { const m = metricsRef.current; setPerf(`${m.tier} · ${m.fps.toFixed(0)} fps · ${m.cpuMs.toFixed(1)} ms js · ${m.calls} calls · ${(m.tris / 1000).toFixed(0)}k tris · ${m.cells} cells · dpr ${m.dpr}`) }, 500)
    return () => clearInterval(id)
  }, [debug.perf])

  const stage = (
    <div className="stage-host" ref={hostRef}>
      <canvas ref={posterRef} className="poster" aria-hidden="true" style={{ opacity: glState === 'ready' ? 0 : 1 }} />
      {glState !== 'off' && (
        <Suspense fallback={null}>
          <GLStage director={director} paletteRoot={rootRef} onReady={onGlReady} onContextLost={onGlLost} metrics={metricsRef} onFrame={() => labelLayer.current?.update()} probe={debug.probe} />
        </Suspense>
      )}
    </div>
  )

  return (
    <>
      <div ref={rootRef} className={`nl${debug.ui ? '' : ' noui'}${reduced ? ' rm' : ''}`} id="top">
        <div className="hdr-wrap" dangerouslySetInnerHTML={{ __html: HEADER_MARKUP }} />
        <main id="main" tabIndex={-1}>
          <Story director={director} env={{ mobile, reduced, debug: debug.any, ui: debug.ui }} api={api} stage={stage} labelsRef={labelsRef} />
        </main>
        <div className="outro-wrap" dangerouslySetInnerHTML={{ __html: OUTRO_MARKUP }} />
        <div className="ovl-wrap" dangerouslySetInnerHTML={{ __html: OVERLAY_MARKUP }} />
        {debug.perf && <div className="perf" aria-hidden="true">{perf}</div>}
      </div>
      <div className="nl-foot"><Footer /></div>
    </>
  )
}
