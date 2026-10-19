/**
 * Scroll → T (storyboard §1.3, F3.14). Desktop: one beat = --beat of scroll (48vh, 42vh under 820px tall),
 * T(scroll) linear over #story. Mobile: the beat steps are the anchors; a step crossing the reading line
 * (band bottom + 30% of the rest) reaches its label; morphs complete within the first 60% between steps.
 *
 * Before GSAP arrives a native scroll listener drives T. Then one ScrollTrigger on #story maps scroll → T
 * (Lenis smooths desktop pointers). The picture follows at a capped, calm pace and, when scrolling stops inside
 * a morph, the page glides to the nearest composed beat, so the visitor never rests on a half-built picture.
 * Everything is wrapped in gsap.context() and reverted on unmount.
 */
import type { Director } from './stage/director'
import type { CompiledStory } from './stage/story'

/* ───────────── calm motion: the picture follows scroll at a capped pace, and never rests mid-morph ───────────── */

/** Top speed (beats/s) while the picture is within a beat of the scroll position: a 1-beat morph takes ≥ 1.1 s. */
export const FOLLOW_VMAX = 0.9
/** Exponential approach time constant (s); the cap above wins whenever scrolling is faster than calm. */
export const FOLLOW_TAU = 0.16

/**
 * One frame of the displayed T chasing the scroll T. Within a beat of the target the speed is capped at
 * FOLLOW_VMAX, so a fast wheel flick can't rush a morph; further away (a rail jump) it speeds up with distance.
 */
export function followStep(cur: number, target: number, dt: number): number {
  const d = target - cur
  if (Math.abs(d) < 1e-4) return target
  const ease = Math.abs(d) * (1 - Math.exp(-dt / FOLLOW_TAU))
  const cap = Math.max(FOLLOW_VMAX, (Math.abs(d) - 1) * 1.5) * dt
  return cur + Math.sign(d) * Math.min(ease, cap)
}

/**
 * Where the page should glide when scrolling stops at T (null when T is at rest). Inside a morph it finishes
 * the morph in the direction of travel, unless the visitor had barely started it (then it goes back). It lands
 * a third of the way into the neighbouring rest, not on its edge, so a small scroll back from there stays at
 * rest instead of starting the morph again (and being pulled back).
 */
export function settleTarget(story: CompiledStory, T: number, dir: 1 | -1): number | null {
  const i = story.segs.findIndex(s => s.kind === 'morph' && T > s.t0 + 1e-3 && T < s.t1 - 1e-3)
  if (i < 0) return null
  const seg = story.segs[i]
  const m = (T - seg.t0) / (seg.t1 - seg.t0)
  const forward = dir > 0 ? m >= 0.15 : m > 0.85
  const rest = story.segs[forward ? i + 1 : i - 1]
  const inset = rest?.kind === 'rest' ? (rest.t1 - rest.t0) / 3 : 0
  return forward ? seg.t1 + inset : seg.t0 - inset
}

/** Settle glide duration (s): slower for longer distances, always unhurried. */
export const settleDuration = (dT: number) => Math.min(1.6, Math.max(0.8, 0.6 + 0.9 * Math.abs(dT)))

const easeInOutSine = (t: number) => -(Math.cos(Math.PI * t) - 1) / 2

export interface ScrollEnv { reduced: boolean; mobile: boolean; debug: boolean }
export interface ScrollMap { toT(y: number): number; toScroll(T: number): number }

export const beatPx = () => (window.innerHeight >= 820 ? 0.48 : 0.42) * window.innerHeight

const docTop = (el: Element) => el.getBoundingClientRect().top + window.scrollY

export function makeScrollMap(story: HTMLElement, director: Director, mobile: boolean): ScrollMap {
  if (!mobile) {
    const top = docTop(story), bp = beatPx()
    return { toT: y => (y - top) / bp, toScroll: T => top + T * bp }
  }
  // mobile: steps carry data-t (their label's T); reading line = band bottom + 30% of the remaining viewport
  const band = story.querySelector<HTMLElement>('.story-pin')
  const header = parseFloat(getComputedStyle(story).getPropertyValue('--header-h')) || 56
  const bandH = band?.offsetHeight ?? 0
  const line = header + bandH + 0.3 * (window.innerHeight - header - bandH)
  const pts = [...story.querySelectorAll<HTMLElement>('[data-t]')].map(el => ({ T: +el.dataset.t!, y: docTop(el) - line })).sort((a, b) => a.y - b.y)
  if (!pts.length) return { toT: () => 0, toScroll: () => 0 }
  return {
    toT(y) {
      if (y <= pts[0].y) return pts[0].T
      for (let k = 0; k < pts.length - 1; k++) {
        const a = pts[k], b = pts[k + 1]
        if (y < b.y) { const u = (y - a.y) / Math.max(1, b.y - a.y); return a.T + (b.T - a.T) * Math.min(1, u / 0.6) }
      }
      return pts[pts.length - 1].T
    },
    toScroll(T) {
      if (T <= pts[0].T + 1e-6) return 0                 // the first label is the top of the page (hero copy in view)
      for (let k = 0; k < pts.length - 1; k++) {
        const a = pts[k], b = pts[k + 1]
        if (T <= b.T) return a.y + ((T - a.T) / Math.max(1e-6, b.T - a.T)) * 0.6 * (b.y - a.y)
      }
      return pts[pts.length - 1].y
    },
  }
}

export interface ScrollController {
  map(): ScrollMap
  refresh(): void
  scrollToT(T: number, opts?: { instant?: boolean }): void
  scrollToEl(el: Element, opts?: { instant?: boolean }): void
  destroy(): void
  lenis: boolean
}

/** Native fallback (and the reduced-motion / debug path): T follows scroll directly. */
export function nativeScroll(story: HTMLElement, director: Director, env: ScrollEnv): ScrollController {
  let map = makeScrollMap(story, director, env.mobile)
  const onScroll = () => director.setT(map.toT(window.scrollY))
  const onResize = () => { map = makeScrollMap(story, director, env.mobile); onScroll() }
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onResize)
  onScroll()
  return {
    map: () => map,
    refresh: onResize,
    scrollToT(T, o) { window.scrollTo({ top: map.toScroll(T), behavior: o?.instant || env.reduced ? 'auto' : 'smooth' }) },
    scrollToEl(el, o) { el.scrollIntoView({ behavior: o?.instant || env.reduced ? 'auto' : 'smooth', block: 'start' }) },
    destroy() { window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onResize) },
    lenis: false,
  }
}

/** GSAP ScrollTrigger (+ Lenis on desktop pointers). Loaded with dynamic import() after first paint. */
export async function gsapScroll(story: HTMLElement, director: Director, env: ScrollEnv): Promise<ScrollController> {
  const [{ gsap }, { ScrollTrigger }] = await Promise.all([import('gsap'), import('gsap/ScrollTrigger')])
  gsap.registerPlugin(ScrollTrigger)
  const fine = window.matchMedia('(pointer: fine)').matches
  const useLenis = fine && !env.reduced && !env.mobile && !env.debug
  let lenis: any = null
  if (useLenis) {
    const { default: Lenis } = await import('lenis')
    lenis = new Lenis({ autoRaf: false, anchors: true, lerp: 0.08 })
    lenis.on('scroll', ScrollTrigger.update)
    const tick = (t: number) => lenis.raf(t * 1000)
    gsap.ticker.add(tick)
    gsap.ticker.lagSmoothing(0)
    lenis.__tick = tick
  }
  let map = makeScrollMap(story, director, env.mobile)
  // Calm mode: the displayed T follows the scroll T at a capped pace (followStep), and when scrolling stops
  // inside a morph the page glides to the nearest composed beat (settleTarget). Reduced motion and the debug
  // hook keep the direct mapping.
  const calm = !env.reduced && !env.debug
  let target = director.T, cur = director.T
  const follow = (_t: number, dtMs: number) => {
    if (cur === target) return
    cur = followStep(cur, target, Math.min(dtMs, 250) / 1000)
    director.setT(cur)
  }
  if (calm) gsap.ticker.add(follow)

  // settle: after a short idle, finish (or undo) a half-played morph with an unhurried glide
  let lastY = window.scrollY, dir: 1 | -1 = 1, idle = 0, touching = false, glide = 0, gliding = false
  const stopGlide = () => { if (glide) { cancelAnimationFrame(glide); glide = 0 } gliding = false }
  const glideTo = (y: number, dur: number) => {
    if (lenis) { gliding = true; lenis.scrollTo(y, { duration: dur, easing: easeInOutSine, onComplete: () => { gliding = false } }); return }
    stopGlide(); gliding = true
    const y0 = window.scrollY, t0 = performance.now()
    const step = (t: number) => {
      const u = Math.min(1, (t - t0) / (dur * 1000))
      window.scrollTo(0, y0 + (y - y0) * easeInOutSine(u))
      glide = u < 1 ? requestAnimationFrame(step) : 0
      if (u >= 1) gliding = false
    }
    glide = requestAnimationFrame(step)
  }
  const settle = () => {
    if (touching || gliding) return
    const T = map.toT(window.scrollY)
    const to = settleTarget(director.story, T, dir)
    if (to == null) return
    glideTo(map.toScroll(to), settleDuration(to - T))
  }
  const onScrollY = () => {
    const y = window.scrollY
    if (y !== lastY) dir = y > lastY ? 1 : -1
    lastY = y
    clearTimeout(idle)
    if (calm) idle = window.setTimeout(settle, 220)
  }
  // the visitor taking over cancels a glide in progress
  const interrupt = () => { if (gliding) { stopGlide(); if (lenis) lenis.scrollTo(window.scrollY, { immediate: true }) } }
  const onTouchStart = () => { touching = true; interrupt() }
  const onTouchEnd = () => { touching = false; onScrollY() }
  if (calm) {
    window.addEventListener('wheel', interrupt, { passive: true })
    window.addEventListener('keydown', interrupt)
    window.addEventListener('touchstart', onTouchStart, { passive: true })
    window.addEventListener('touchend', onTouchEnd, { passive: true })
  }

  const ctx = gsap.context(() => {
    ScrollTrigger.create({
      trigger: story, start: 'top top', end: 'bottom bottom',
      onUpdate: () => {
        target = map.toT(window.scrollY)
        if (!calm) { cur = target; director.setT(target) }
        onScrollY()
      },
      onRefresh: () => { map = makeScrollMap(story, director, env.mobile) },
    })
  })

  const refresh = () => ScrollTrigger.refresh()
  const fonts = (document as any).fonts
  if (fonts?.ready) fonts.ready.then(refresh)
  window.addEventListener('resize', refresh)
  target = cur = map.toT(window.scrollY)
  director.setT(cur)
  return {
    map: () => map,
    refresh,
    scrollToT(T, o) {
      const y = map.toScroll(T)
      if (lenis) lenis.scrollTo(y, { immediate: o?.instant || env.reduced, duration: 0.9 })
      else window.scrollTo({ top: y, behavior: o?.instant || env.reduced ? 'auto' : 'smooth' })
    },
    scrollToEl(el, o) {
      if (lenis) lenis.scrollTo(el, { immediate: o?.instant || env.reduced, duration: 1 })
      else el.scrollIntoView({ behavior: o?.instant || env.reduced ? 'auto' : 'smooth', block: 'start' })
    },
    destroy() {
      window.removeEventListener('resize', refresh)
      clearTimeout(idle); stopGlide()
      gsap.ticker.remove(follow)
      window.removeEventListener('wheel', interrupt)
      window.removeEventListener('keydown', interrupt)
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchend', onTouchEnd)
      ctx.revert()
      if (lenis) { gsap.ticker.remove(lenis.__tick); lenis.destroy() }
    },
    lenis: !!lenis,
  }
}
