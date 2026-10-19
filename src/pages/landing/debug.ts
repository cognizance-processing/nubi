/**
 * Debug hook (storyboard §1.11): URL parameters and window.__nubiLanding for deterministic screenshots.
 *   /?scene=<id>&beat=<id|index>&p=<0..1>&state=<urlencoded JSON>&t=<ambient s>&labels=0|1&ui=0|1&poster=1&rm=1&perf=1&theme=light|dark
 * Any parameter disables the build-in, Lenis and snap.
 */
import type { Director } from './stage/director'
import { labelT } from './stage/story'
import type { Beat, SceneId } from './stage/types'
import type { ScrollController } from './scroll'

export interface DebugParams {
  any: boolean; scene?: string; beat?: string; p: number; state?: any; t?: number
  labels: boolean; ui: boolean; poster: boolean; rm: boolean; perf: boolean; theme?: 'light' | 'dark'; probe: boolean
}

export function parseDebug(search = typeof location !== 'undefined' ? location.search : ''): DebugParams {
  const q = new URLSearchParams(search)
  const keys = ['scene', 'beat', 'p', 'state', 't', 'labels', 'ui', 'poster', 'rm', 'perf', 'theme', 'probe']
  let state: any
  try { state = q.get('state') ? JSON.parse(q.get('state')!) : undefined } catch { state = undefined }
  const theme = q.get('theme')
  return {
    any: keys.some(k => q.has(k)),
    scene: q.get('scene') ?? undefined, beat: q.get('beat') ?? undefined,
    p: q.has('p') ? Math.min(1, Math.max(0, +q.get('p')!)) : 1, state,
    t: q.has('t') ? +q.get('t')! : undefined,
    labels: q.get('labels') !== '0', ui: q.get('ui') !== '0', poster: q.get('poster') === '1', rm: q.get('rm') === '1', perf: q.get('perf') === '1',
    theme: theme === 'dark' || theme === 'light' ? theme : undefined,
    probe: q.get('probe') === '1',
  }
}

export function applyTheme(theme?: 'light' | 'dark') {
  if (!theme) return
  try { localStorage.setItem('nubi-theme', theme) } catch { /* private mode */ }
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
}

export interface Metrics { fps: number; cpuMs: number; calls: number; tris: number; cells: number; dpr: number; tier: string; frames?: number; probe?: [number, number] }

/** T for "progress p through the morph INTO beat" of a scene. */
export function targetT(director: Director, scene: string, beat: string | number, p = 1): number | null {
  const story = director.story
  const T = labelT(story, scene, beat)
  if (T == null) return null
  if (p >= 1) return T
  const si = story.scenes.findIndex(s => s.id === scene)
  const mod = story.scenes[si]
  const bi = typeof beat === 'number' ? beat : mod.beats.findIndex((b: Beat<any>) => b.id === beat)
  const len = bi === 0 ? (si === 0 ? 0 : mod.in?.len ?? 0.6) : (mod.beats[bi] as Beat<any>).morph?.len ?? 0.6
  return T - (1 - p) * len
}

export function installHook(opts: {
  director: Director; scroll: () => ScrollController | null; ready: Promise<void>; metrics: () => Metrics
}) {
  const { director } = opts
  const sid = (s: string) => director.scenes.findIndex(m => m.id === s)
  const settle = async (n = 2) => { for (let i = 0; i < n; i++) await director.nextFrame() }
  const api = {
    version: 2,
    ready: opts.ready,
    scenes: () => director.scenes.map(s => ({ id: s.id as SceneId, beats: s.beats.map((b: Beat<any>) => ({ id: b.id, caption: b.caption })) })),
    async goto(scene: SceneId, beat: string | number, o?: { p?: number; state?: object }) {
      const T = targetT(director, scene, beat, o?.p ?? 1)
      if (T == null) throw new Error(`unknown beat ${scene}.${beat}`)
      director.cancelBuild()
      const sc = opts.scroll()
      if (sc) sc.scrollToT(T, { instant: true })
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
      director.setT(T)
      director.T = T
      if (o?.state) director.setSceneState(sid(scene), o.state, true)
      director.emit()
      await settle(2)
    },
    async set(scene: SceneId, patch: object) {
      director.setSceneState(sid(scene), patch)
      while (director.animating() && !director.ambientActive()) await director.nextFrame()
      await settle(2)
    },
    async hover(scene: SceneId, group: string | null) { director.setHover(sid(scene), group); await settle(3) },
    state: (scene?: SceneId) => director.sceneState(scene ? sid(scene) : director.activeScene()),
    freeze: (t = 0) => director.freeze(t),
    /** The displayed story time (beats). */
    T: () => director.T,
    /** The story time the scroll position maps to (the displayed T follows it). */
    scrollT: () => opts.scroll()?.map().toT(window.scrollY) ?? null,
    frame: () => settle(1),
    metrics: () => ({ ...opts.metrics(), frames: director.frames }),
  }
  ;(window as any).__nubiLanding = api
  return () => { if ((window as any).__nubiLanding === api) delete (window as any).__nubiLanding }
}
