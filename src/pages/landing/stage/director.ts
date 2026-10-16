/**
 * Director: the stage's runtime state. The frame is a pure function of (T, scene state, ambient time):
 * the shell sets T from scroll, controls/picks patch scene state, and renderers (the R3F stage, the 2D
 * poster, the DOM label layer) read display() every frame. No three, no React.
 */
import { camOf, easeInOutCubic, lerpCam, type CamState, type Viewport } from './camera'
import { activeSceneAt, currentBeatAt, presetState, quantise, resolve, type CompiledStory } from './story'
import { kit } from './kit'
import type { Beat, Layout, MorphSpec, SceneModule, Vec3 } from './types'

export interface RowKey { row: number; variant?: undefined }
export interface VariantKey { variant: number; scene: number; beat: number; state: any; base: number; row?: undefined }
export type DisplayKey = RowKey | VariantKey
export const isVariant = (k: DisplayKey): k is VariantKey => k.variant !== undefined
export interface Display {
  from: DisplayKey; to: DisplayKey; m: number; spec: MorphSpec | null
  scene: number; beat: number; fromBeat: number; fromScene: number
  kind: 'build' | 'scroll' | 'interaction' | 'rest'
  cam: CamState; state: any
  wave: { centre: Vec3; radius: number; width: number } | null
  atRest: boolean
}
type Listener = () => void

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

export class Director {
  story: CompiledStory
  T = 0
  reduced = false
  mobile = false
  /** measured by the shell; `viewport` adds the hero's proof-strip clearance (safe rect ends 96 px above the bottom) */
  vpBase: Viewport = { w: 1, h: 1, mobile: false, safe: { x0: 0, y0: 0, x1: 1, y1: 1 } }
  heroBottomExtra = 64
  paused = false
  hover: { scene: number; group: string; t0: number } | null = null
  pickTip: { anchor: Vec3; text: string; sub?: string } | null = null
  ambientEnabled = true
  private overrides = new Map<string, { key: VariantKey; state: any }>()
  private serial = 0
  private interaction: { from: DisplayKey; to: DisplayKey; t0: number; dur: number; spec: MorphSpec; wave?: { centre: Vec3; r: [number, number]; width: number } } | null = null
  private build: { t0: number; dur: number } | null = null
  private lastInput = now()
  private frozen: number | null = null
  private ambientStart = now()
  private lastRestKey = ''
  private listeners = new Set<Listener>()
  private variantCache = new Map<number, Layout>()
  private frameWaiters: Array<() => void> = []
  /** bumped when anything the copy shows changes (scene state, overrides) */
  rev = 0
  frames = 0
  /** set by the active renderer: requests one more frame */
  invalidate: () => void = () => {}

  constructor(story: CompiledStory) { this.story = story }

  get scenes(): SceneModule<any>[] { return this.story.scenes }

  get viewport(): Viewport {
    const v = this.vpBase
    if (v.mobile || this.story.spans.length < 2) return v
    const a = this.story.spans[0].t1, b = this.story.spans[1].activeAt
    const f = 1 - Math.min(1, Math.max(0, (this.T - a) / Math.max(1e-3, b - a)))
    return f <= 0 ? v : { ...v, safe: { ...v.safe, y1: v.safe.y1 - this.heroBottomExtra * f } }
  }

  /* ── subscriptions: UI + renderers re-read display() on change ── */
  on(fn: Listener) { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  emit() { this.listeners.forEach(f => f()); this.invalidate() }

  /** Renderers call this after drawing a frame. */
  markFrame() { this.frames++; const w = this.frameWaiters; this.frameWaiters = []; w.forEach(f => f()) }
  /** Resolves after the next rendered frame (or 250 ms when nothing renders). */
  nextFrame(): Promise<void> {
    return new Promise(res => {
      let done = false
      const fin = () => { if (!done) { done = true; res() } }
      this.frameWaiters.push(fin)
      this.invalidate()
      setTimeout(fin, 250)
    })
  }

  /* ── time ── */
  setT(T: number) {
    const t = Math.min(Math.max(T, 0), this.story.total)
    const q = this.reduced ? quantise(this.story, t) : t
    if (q === this.T) return
    this.T = q
    if (this.build && q > 0.02) this.build = null      // scrolling cancels the build-in
    this.touch()
    this.cleanupOverrides()
    this.emit()
  }
  touch() { this.lastInput = now() }

  startBuild(durMs: number) { if (this.reduced) return; this.build = { t0: now(), dur: durMs }; this.emit() }
  cancelBuild() { if (this.build) { this.build = null; this.emit() } }
  get building() { return !!this.build && now() - this.build.t0 < this.build.dur }

  /* ── active scene / beat (for copy, rail, aria) ── */
  activeScene() { return activeSceneAt(this.story, this.T) }
  currentBeat(si = this.activeScene()) { return currentBeatAt(this.story, si, this.T) }

  /** The state a scene shows now: a user override on the current beat, else the beat's scroll preset. */
  sceneState(si: number): any {
    const sp = this.story.spans[si]
    const beat = this.T < sp.t0 ? 0 : this.T >= sp.t1 ? this.scenes[si].beats.length - 1 : resolve(this.story, this.T).scene === si ? resolve(this.story, this.T).beat : this.currentBeat(si)
    const o = this.overrides.get(`${si}:${beat}`)
    return o ? o.state : presetState(this.scenes[si], beat)
  }

  /** Apply an interaction patch (controls, picks, debug hook). Time-based morph unless reduced. */
  setSceneState(si: number, patch: any, instant = false): any {
    const r = resolve(this.story, this.T)
    const beat = r.scene === si ? r.beat : Math.max(0, this.scenes[si].beats.findIndex((b: Beat<any>) => b.id === this.scenes[si].poster))
    const mod = this.scenes[si]
    const prev = this.sceneState(si)
    const next = { ...prev, ...patch }
    const fromKey = this.restKey(si, beat)
    const key: VariantKey = { variant: ++this.serial, scene: si, beat, state: next, base: this.story.rowOf(si, beat) }
    this.overrides.set(`${si}:${beat}`, { key, state: next })
    const spec = mod.onState?.(prev, next) ?? { kind: 'cut', len: 450, spread: 0 }
    const b = mod.beats[beat] as Beat<any>
    const wave = b.wave && spec.order === 'radial' ? { centre: spec.from ?? b.wave.centre, r: b.wave.radius, width: b.wave.width ?? 6 } : undefined
    this.interaction = { from: fromKey, to: key, t0: now(), dur: this.reduced || instant ? 0 : spec.len, spec, wave }
    this.rev++
    this.touch()
    this.emit()
    return next
  }

  /** The layout of a display key (base rows from the compiled story; variants computed once). */
  layoutOf(k: DisplayKey): Layout {
    if (!isVariant(k)) return this.story.rows[k.row].layout
    let l = this.variantCache.get(k.variant)
    if (!l) {
      l = (this.scenes[k.scene].beats[k.beat] as Beat<any>).layout(kit, k.state)
      this.variantCache.set(k.variant, l)
      if (this.variantCache.size > 8) this.variantCache.delete(this.variantCache.keys().next().value as number)
    }
    return l
  }

  private restKey(si: number, beat: number): DisplayKey {
    const o = this.overrides.get(`${si}:${beat}`)
    return o ? o.key : { row: this.story.rowOf(si, beat) }
  }

  private cleanupOverrides() {
    const r = resolve(this.story, this.T)
    if (r.seg.kind !== 'rest') return
    const k = `${r.scene}:${r.beat}`
    if (k === this.lastRestKey) return
    this.lastRestKey = k
    for (const key of [...this.overrides.keys()]) if (key !== k) { this.overrides.delete(key); this.rev++ }
    const to = this.interaction?.to
    if (to && isVariant(to) && !this.overrides.has(`${to.scene}:${to.beat}`)) this.interaction = null
  }

  /* ── hover ── */
  setHover(si: number, group: string | null) {
    const cur = this.hover
    if ((group ?? null) === (cur?.group ?? null) && (!cur || cur.scene === si)) return
    this.hover = group ? { scene: si, group, t0: now() } : null
    const mod = this.scenes[si]
    this.pickTip = group ? mod.pick?.hover?.(group, this.sceneState(si)) ?? this.tooltipOf(si, group) : null
    this.emit()
  }
  private tooltipOf(si: number, group: string) {
    const t = this.story.rows[this.story.rowOf(si, this.currentBeat(si))].layout.groups?.[group]?.tooltip
    return t ? { anchor: [0, 0, 0] as Vec3, text: t } : null
  }

  /* ── ambient ── */
  freeze(t: number | null) { this.frozen = t; this.emit() }
  ambientT(): number {
    if (this.frozen != null) return this.frozen
    return (now() - this.ambientStart) / 1000
  }
  /** Ambient runs only within 5 s of the last input, never under reduced motion. */
  ambientActive(): boolean {
    if (this.reduced || !this.ambientEnabled || this.frozen != null) return false
    const d = this.display()
    if (!d.atRest) return false
    const b = this.scenes[d.scene].beats[d.beat] as Beat<any>
    return !!b.ambient && b.ambient.kind !== 'none' && now() - this.lastInput < 5000
  }

  /** True while something time-based is animating (build, interaction, hover lift, ambient). */
  animating(): boolean {
    const t = now()
    if (this.build && t - this.build.t0 < this.build.dur) return true
    if (this.interaction && t - this.interaction.t0 < this.interaction.dur) return true
    if (this.hover && t - this.hover.t0 < 200) return true
    return this.ambientActive()
  }

  /* ── the frame ── */
  display(): Display {
    const t = now()
    const story = this.story
    if (this.build && t - this.build.t0 < this.build.dur) {
      const m = Math.min(1, (t - this.build.t0) / this.build.dur)
      const hero = this.scenes[0]
      return { from: { row: 0 }, to: { row: story.rowOf(0, 0) }, m, spec: hero.build?.morph ?? null, scene: 0, beat: 0, fromScene: 0, fromBeat: 0, kind: 'build',
        cam: camOf(hero.beats[0].camera, this.mobile), state: this.sceneState(0), wave: null, atRest: false }
    }
    if (this.build) this.build = null
    const r = resolve(story, this.T)
    const seg = r.seg
    const mod = this.scenes[seg.scene]
    const beat = mod.beats[seg.beat] as Beat<any>
    if (seg.kind === 'rest' || r.m >= 1) {
      const key = this.restKey(seg.scene, seg.beat)
      const state = this.sceneState(seg.scene)
      const cam = camOf(beat.camera, this.mobile)
      const it = this.interaction
      if (it && it.to === key && t - it.t0 < it.dur) {
        const m = Math.min(1, (t - it.t0) / it.dur)
        const wave = it.wave ? { centre: it.wave.centre, radius: it.wave.r[0] + (it.wave.r[1] - it.wave.r[0]) * m, width: it.wave.width } : null
        return { from: it.from, to: key, m, spec: it.spec, scene: seg.scene, beat: seg.beat, fromScene: seg.scene, fromBeat: seg.beat, kind: 'interaction', cam, state, wave, atRest: false }
      }
      return { from: key, to: key, m: 1, spec: null, scene: seg.scene, beat: seg.beat, fromScene: seg.scene, fromBeat: seg.beat, kind: 'rest', cam, state, wave: null, atRest: true }
    }
    const fromRow = story.rows[seg.fromRow]
    const fromScene = fromRow.scene < 0 ? 0 : fromRow.scene, fromBeat = fromRow.beat < 0 ? 0 : fromRow.beat
    const fromKey = fromRow.scene < 0 ? { row: 0 } : this.restKey(fromScene, fromBeat)
    const fromCam = camOf((this.scenes[fromScene].beats[fromBeat] as Beat<any>).camera, this.mobile)
    const cam = lerpCam(fromCam, camOf(beat.camera, this.mobile), easeInOutCubic(r.m))
    const wave = beat.wave ? { centre: beat.wave.centre, radius: beat.wave.radius[0] + (beat.wave.radius[1] - beat.wave.radius[0]) * r.m, width: beat.wave.width ?? 6 } : null
    return { from: fromKey, to: { row: seg.toRow }, m: r.m, spec: seg.morph, scene: seg.scene, beat: seg.beat, fromScene, fromBeat, kind: 'scroll', cam, state: this.sceneState(seg.scene), wave, atRest: false }
  }
}
