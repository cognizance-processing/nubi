/**
 * Scroll → T (storyboard §1.3, F3.14). Desktop: one beat = --beat of scroll (48vh, 42vh under 820px tall),
 * T(scroll) linear over #story. Mobile: the beat steps are the anchors; a step crossing the reading line
 * (band bottom + 30% of the rest) reaches its label; morphs complete within the first 60% between steps.
 *
 * Before GSAP arrives a native scroll listener drives T. Then one ScrollTrigger on #story maps scroll → T,
 * with Lenis (desktop pointer, no reduced motion) or gsap.quickTo smoothing, and directional snap to beat
 * labels on desktop. Everything is wrapped in gsap.context()/matchMedia() and reverted on unmount.
 */
import type { Director } from './stage/director'

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
    lenis = new Lenis({ autoRaf: false, anchors: true })
    lenis.on('scroll', ScrollTrigger.update)
    const tick = (t: number) => lenis.raf(t * 1000)
    gsap.ticker.add(tick)
    gsap.ticker.lagSmoothing(0)
    lenis.__tick = tick
  }
  let map = makeScrollMap(story, director, env.mobile)
  const proxy = { t: director.T }
  const smooth = !env.reduced && !env.debug && !lenis
  const quick = smooth ? gsap.quickTo(proxy, 't', { duration: 0.45, ease: 'power3.out', onUpdate: () => director.setT(proxy.t) }) : null

  const ctx = gsap.context(() => {
    const mm = gsap.matchMedia()
    mm.add({ desktop: '(min-width: 861px)', fine: '(pointer: fine)', reduce: '(prefers-reduced-motion: reduce)' }, c => {
      const { desktop, fine: fp, reduce } = c.conditions as { desktop: boolean; fine: boolean; reduce: boolean }
      const total = director.story.total
      const snapPoints = director.story.labels.map(l => l.T / total)
      ScrollTrigger.create({
        trigger: story, start: 'top top', end: 'bottom bottom',
        onUpdate: () => {
          const T = map.toT(window.scrollY)
          if (quick) quick(T); else director.setT(T)
        },
        onRefresh: () => { map = makeScrollMap(story, director, env.mobile) },
        snap: desktop && fp && !reduce && !env.debug && !env.mobile
          ? { snapTo: snapPoints, directional: true, delay: 0.12, duration: { min: 0.25, max: 0.6 }, ease: 'power2.inOut' }
          : undefined,
      })
    })
  })

  const refresh = () => ScrollTrigger.refresh()
  const fonts = (document as any).fonts
  if (fonts?.ready) fonts.ready.then(refresh)
  window.addEventListener('resize', refresh)
  director.setT(map.toT(window.scrollY))
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
      ctx.revert()
      if (lenis) { gsap.ticker.remove(lenis.__tick); lenis.destroy() }
    },
    lenis: !!lenis,
  }
}
