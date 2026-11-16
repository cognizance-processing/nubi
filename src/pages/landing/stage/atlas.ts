/**
 * GPU layout atlas: every story row (base beats, in story order) plus a few slots for interaction variants,
 * in one RGBA32F DataTexture. Crossing a boundary only changes uniforms; a variant uploads once per change.
 */
import * as THREE from 'three'
import { assignLayout, ATLAS_W, GroupIds, N_INSTANCES, ROWS_PER_LAYOUT, writeRow, type Placed } from './assign'
import type { Director, DisplayKey } from './director'
import { isVariant } from './director'
import type { Beat, MorphSpec } from './types'

const VARIANT_SLOTS = 3

export class Atlas {
  texture!: THREE.DataTexture
  data!: Float32Array
  placed: Placed[] = []
  ids = new GroupIds()
  private director: Director
  private slotOf = new Map<number, number>()       // variant id → slot
  private slotUse: number[] = []
  private baseRows = 0
  ready = false

  constructor(director: Director) { this.director = director }

  /** Assign and write every story row; yields between rows so the main thread stays responsive. */
  async build(yieldEvery = 3) {
    const story = this.director.story
    this.baseRows = story.rows.length
    const rows = this.baseRows + VARIANT_SLOTS
    this.data = new Float32Array(ATLAS_W * ROWS_PER_LAYOUT * rows * 4)
    // story order: EMPTY → hero.board → … ; each row is matched against the previous one
    const specInto = new Map<number, MorphSpec | null>()
    for (const seg of story.segs) if (seg.kind === 'morph') specInto.set(seg.toRow, seg.morph)
    const hero = story.scenes[0]
    specInto.set(story.rowOf(0, 0), hero.build?.morph ?? { kind: 'rise', len: 1, spread: 0.5 })
    this.placed[0] = { cells: [], inst2cell: new Int32Array(N_INSTANCES).fill(-1) }
    for (let r = 1; r < this.baseRows; r++) {
      const row = story.rows[r]
      const spec = specInto.get(r) ?? null
      const prev = this.placed[r - 1]
      const inst2cell = assignLayout(r === 1 ? null : prev, row.layout, spec)
      this.placed[r] = { cells: row.layout.cells, inst2cell }
      const sid = story.scenes[row.scene].id
      writeRow(this.data, r, this.placed[r], r === 1 ? null : prev, spec, g => this.ids.id(sid, g))
      if (yieldEvery && r % yieldEvery === 0) await new Promise(res => setTimeout(res, 0))
    }
    this.texture = new THREE.DataTexture(this.data, ATLAS_W, ROWS_PER_LAYOUT * rows, THREE.RGBAFormat, THREE.FloatType)
    this.texture.minFilter = THREE.NearestFilter
    this.texture.magFilter = THREE.NearestFilter
    this.texture.generateMipmaps = false
    this.texture.needsUpdate = true
    this.ready = true
  }

  /** First texel row of a display key; uploads a variant into a free slot when first seen. */
  rowOf(k: DisplayKey, protect: DisplayKey[] = []): number {
    if (!isVariant(k)) return k.row * ROWS_PER_LAYOUT
    let slot = this.slotOf.get(k.variant)
    if (slot === undefined) {
      const busy = new Set(protect.filter(isVariant).map(p => this.slotOf.get(p.variant)).filter(s => s !== undefined))
      slot = [0, 1, 2].find(s => !busy.has(s) && !this.slotUse.includes(s)) ?? [0, 1, 2].find(s => !busy.has(s)) ?? 0
      for (const [v, s] of this.slotOf) if (s === slot) this.slotOf.delete(v)
      this.slotOf.set(k.variant, slot)
      this.writeVariant(k, slot)
    }
    this.slotUse = [slot, ...this.slotUse.filter(s => s !== slot)].slice(0, 2)
    return (this.baseRows + slot) * ROWS_PER_LAYOUT
  }

  private writeVariant(k: Extract<DisplayKey, { variant: number }>, slot: number) {
    const d = this.director
    const layout = d.layoutOf(k)
    const mod = d.scenes[k.scene]
    const prevState = d.story.rows[k.base].state
    const spec = mod.onState?.(prevState, k.state) ?? { kind: 'cut', len: 450 }
    const base = this.placed[k.base]
    const inst2cell = assignLayout(base, layout, spec)
    writeRow(this.data, this.baseRows + slot, { cells: layout.cells, inst2cell }, base, spec, g => this.ids.id(mod.id, g))
    this.texture.needsUpdate = true
  }

  gid(sceneIdx: number, group: string) { return this.ids.id(this.director.scenes[sceneIdx].id, group) }

  /** Visible cells of a key (metrics). */
  cellsOf(k: DisplayKey) { return this.director.layoutOf(k).cells.length }

  dispose() { this.texture?.dispose() }
}

export const beatOf = (d: Director, si: number, bi: number) => d.scenes[si].beats[bi] as Beat<any>
