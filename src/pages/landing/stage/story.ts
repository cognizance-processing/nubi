/**
 * Story timeline (storyboard §1.3): compiles the scene registry into one axis T measured in beats.
 * Every scene contributes in.len + Σ (beat.morph?.len) + Σ beat.len. Labels `<scene>.<beat>` sit at each
 * beat's rest point (morph complete); snapping, anchors and the debug hook target them. Pure.
 */
import { kit } from './kit'
import type { Beat, Layout, MorphSpec, SceneModule } from './types'

export interface BeatRow { scene: number; beat: number; row: number; state: any; layout: Layout; label: string; T: number; restEnd: number }
export interface Seg { scene: number; beat: number; kind: 'morph' | 'rest'; t0: number; t1: number; fromRow: number; toRow: number; morph: MorphSpec | null; isIn: boolean }
export interface SceneSpan { t0: number; t1: number; activeAt: number; firstRow: number; lastRow: number }
export interface Label { name: string; T: number; scene: number; beat: number }
export interface CompiledStory {
  scenes: SceneModule<any>[]
  rows: BeatRow[]                 // index = atlas row; row 0 is the EMPTY layout (everything parked)
  segs: Seg[]
  labels: Label[]
  spans: SceneSpan[]
  total: number
  rowOf(scene: number, beat: number): number
}

export const DEFAULT_BEAT_MORPH: MorphSpec = { kind: 'slide', len: 0.6, spread: 0.45 }
export const EMPTY_ROW = 0

/** The state a beat is laid out with when entered by scroll: initialState ⊕ presets of beats 0..b. */
export function presetState<S>(mod: SceneModule<S>, beat: number): S {
  let s = { ...(mod.initialState as any) }
  for (let i = 0; i <= beat && i < mod.beats.length; i++) if (mod.beats[i].state) s = { ...s, ...mod.beats[i].state }
  return s
}

export function compileStory(scenes: SceneModule<any>[]): CompiledStory {
  const rows: BeatRow[] = [{ scene: -1, beat: -1, row: 0, state: null, layout: { cells: [] }, label: '', T: 0, restEnd: 0 }]
  const segs: Seg[] = [], labels: Label[] = [], spans: SceneSpan[] = []
  const rowIdx: number[][] = []
  let T = 0
  scenes.forEach((mod, si) => {
    rowIdx[si] = []
    const t0 = T
    let activeAt = T
    mod.beats.forEach((beat: Beat<any>, bi: number) => {
      const state = presetState(mod, bi)
      const row = rows.length
      rows.push({ scene: si, beat: bi, row, state, layout: beat.layout(kit, state), label: `${mod.id}.${beat.id}`, T: 0, restEnd: 0 })
      rowIdx[si][bi] = row
      const prevRow = bi > 0 ? rowIdx[si][bi - 1] : si > 0 ? rowIdx[si - 1][scenes[si - 1].beats.length - 1] : EMPTY_ROW
      let morph: MorphSpec | null = null, isIn = false
      if (bi === 0 && si > 0) { morph = mod.in ?? DEFAULT_BEAT_MORPH; isIn = true }
      else if (bi > 0) morph = beat.morph ?? DEFAULT_BEAT_MORPH
      if (morph) {
        const len = Math.max(0.05, morph.len)
        segs.push({ scene: si, beat: bi, kind: 'morph', t0: T, t1: T + len, fromRow: prevRow, toRow: row, morph, isIn })
        if (isIn) activeAt = T + len / 2
        T += len
      }
      rows[row].T = T
      labels.push({ name: rows[row].label, T, scene: si, beat: bi })
      const rest = Math.max(0.05, beat.len)
      segs.push({ scene: si, beat: bi, kind: 'rest', t0: T, t1: T + rest, fromRow: row, toRow: row, morph: null, isIn: false })
      T += rest
      rows[row].restEnd = T
    })
    spans.push({ t0, t1: T, activeAt: si === 0 ? 0 : activeAt, firstRow: rowIdx[si][0], lastRow: rowIdx[si][mod.beats.length - 1] })
  })
  return { scenes, rows, segs, labels, spans, total: T, rowOf: (s, b) => rowIdx[s]?.[b] ?? EMPTY_ROW }
}

export interface Resolved { seg: Seg; m: number; scene: number; beat: number; atRest: boolean }

/** The segment at story time T (clamped), with progress m ∈ [0,1] through it (1 at rest). */
export function resolve(story: CompiledStory, T: number): Resolved {
  const segs = story.segs
  const t = Math.min(Math.max(T, 0), story.total)
  let lo = 0, hi = segs.length - 1
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (segs[mid].t0 <= t) lo = mid; else hi = mid - 1 }
  const seg = segs[lo]
  const m = seg.kind === 'rest' ? 1 : Math.min(1, Math.max(0, (t - seg.t0) / (seg.t1 - seg.t0)))
  return { seg, m, scene: seg.scene, beat: seg.beat, atRest: seg.kind === 'rest' || m >= 1 }
}

/** The scene whose copy is shown at T: the last scene whose `in` midpoint is ≤ T. */
export function activeSceneAt(story: CompiledStory, T: number): number {
  let a = 0
  story.spans.forEach((s, i) => { if (T >= s.activeAt - 1e-6) a = i })
  return a
}

/** The highlighted beat of scene `si` at T: the last beat whose rest point is within .15 beat of T or behind it. */
export function currentBeatAt(story: CompiledStory, si: number, T: number): number {
  let b = 0
  story.labels.forEach(l => { if (l.scene === si && T >= l.T - 0.15) b = l.beat })
  return b
}

/** Reduced motion: T snapped to the last rest point at or before T. */
export function quantise(story: CompiledStory, T: number): number {
  let q = 0
  for (const l of story.labels) if (l.T <= T + 1e-6) q = l.T
  return q
}

export const labelT = (story: CompiledStory, sceneId: string, beat: string | number): number | null => {
  const si = story.scenes.findIndex(s => s.id === sceneId)
  if (si < 0) return null
  const bi = typeof beat === 'number' ? beat : story.scenes[si].beats.findIndex((b: Beat<any>) => b.id === beat)
  const l = story.labels.find(x => x.scene === si && x.beat === bi)
  return l ? l.T : null
}
