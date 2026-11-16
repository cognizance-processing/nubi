/**
 * Instance assignment and the layout atlas (storyboard §1.4). Pure TypeScript, no three.
 *
 * Layouts are matched to the N instances once, in story order, so scrubbing backwards is a true rewind:
 *   - assign 'group' : same group name, then rank within the group
 *   - assign 'rank'  : sort both sets by the morph's order key and pair in order
 *   - assign 'nearest': greedy nearest neighbour (spatial hash)
 *   - default        : 'group', then 'nearest' for what is left
 * A destination cell whose own `kind` is 'rise' is never matched: it always rises from the floor.
 * Unmatched destination cells take parked instances (they rise through the floor at their own x,z);
 * unmatched source cells sink. Interaction variants are assigned against their base beat.
 *
 * Atlas: RGBA32F, width 2048, 3 texels per instance per layout:
 *   [pos.xyz, tone] [scale.xyz, groupId] [yaw, roll, order, flags]
 * flags = kind (4 bits, 0 = the morph's kind) + 16 · (flight tone + 1) + 256 · wave.
 */
import type { Cell, Layout, MorphKind, MorphSpec } from './types'
import { normalisedOrder, orderKeyFn } from './kit/order'

export const N_INSTANCES = 4096
export const ATLAS_W = 2048
export const TEX_PER_INST = 3
export const ROWS_PER_LAYOUT = Math.ceil((N_INSTANCES * TEX_PER_INST) / ATLAS_W)  // 6
export const KIND_CODE: Record<MorphKind, number> = { rise: 1, sink: 2, flip: 3, slide: 4, lift: 5, arc: 6, helix: 7, scale: 8, cut: 9 }
export const DEFAULT_SIZE: [number, number, number] = [0.95, 0.95, 0.95]

export interface Placed { cells: Cell[]; inst2cell: Int32Array }

/** Match `layout` against the previous placement. Returns inst → cell index (-1 = parked). */
export function assignLayout(prev: Placed | null, layout: Layout, spec: MorphSpec | null, N = N_INSTANCES): Int32Array {
  const cells = layout.cells
  const out = new Int32Array(N).fill(-1)
  if (cells.length > N) console.warn(`[stage] layout has ${cells.length} cells; only ${N} are drawn`)
  if (!prev) { for (let c = 0; c < Math.min(N, cells.length); c++) out[c] = c; return out }

  const destDone = new Uint8Array(cells.length)
  const srcFree: number[] = [], parked: number[] = []
  for (let i = 0; i < N; i++) (prev.inst2cell[i] >= 0 ? srcFree : parked).push(i)
  const srcUsed = new Uint8Array(N)
  const forced = (c: number) => cells[c].kind === 'rise'
  const strategy = spec?.assign

  if (strategy === undefined || strategy === 'group') {
    const bySrc = new Map<string, number[]>()
    for (const i of srcFree) {
      const g = prev.cells[prev.inst2cell[i]].group
      if (!g) continue
      let l = bySrc.get(g); if (!l) bySrc.set(g, (l = [])); l.push(i)
    }
    for (const l of bySrc.values()) l.sort((a, b) => prev.inst2cell[a] - prev.inst2cell[b])
    const cursor = new Map<string, number>()
    for (let c = 0; c < cells.length; c++) {
      const g = cells[c].group
      if (!g || forced(c)) continue
      const l = bySrc.get(g); if (!l) continue
      const k = cursor.get(g) ?? 0
      if (k >= l.length) continue
      cursor.set(g, k + 1)
      out[l[k]] = c; destDone[c] = 1; srcUsed[l[k]] = 1
    }
  }

  const restDest = () => { const r: number[] = []; for (let c = 0; c < cells.length; c++) if (!destDone[c] && !forced(c)) r.push(c); return r }
  const restSrc = () => srcFree.filter(i => !srcUsed[i])

  if (strategy === 'rank') {
    const key = orderKeyFn(spec?.order ?? 'x-asc', spec?.from)
    const d = restDest().map(c => ({ c, k: key(cells[c], c) })).sort((a, b) => a.k - b.k)
    const s = restSrc().map(i => ({ i, k: key(prev.cells[prev.inst2cell[i]], prev.inst2cell[i]) })).sort((a, b) => a.k - b.k)
    const n = Math.min(d.length, s.length)
    for (let k = 0; k < n; k++) { out[s[k].i] = d[k].c; destDone[d[k].c] = 1; srcUsed[s[k].i] = 1 }
  }

  if (strategy === undefined || strategy === 'nearest') nearest(prev, cells, restDest(), restSrc(), out, destDone, srcUsed)

  // whatever is left rises from parked instances; if those run out, borrow left-over sources
  let pk = 0
  const leftover: number[] = []
  for (let c = 0; c < cells.length; c++) {
    if (destDone[c]) continue
    if (pk < parked.length) { out[parked[pk++]] = c; destDone[c] = 1 } else leftover.push(c)
  }
  if (leftover.length) nearest(prev, cells, leftover, restSrc(), out, destDone, srcUsed)
  return out
}

function nearest(prev: Placed, cells: Cell[], dest: number[], src: number[], out: Int32Array, destDone: Uint8Array, srcUsed: Uint8Array) {
  if (!dest.length || !src.length) return
  const S = 4
  const grid = new Map<string, number[]>()
  const keyOf = (x: number, y: number, z: number) => `${Math.floor(x / S)},${Math.floor(y / S)},${Math.floor(z / S)}`
  let minI = Infinity, maxI = -Infinity
  for (const i of src) {
    const p = prev.cells[prev.inst2cell[i]].p
    const k = keyOf(p[0], p[1], p[2])
    let l = grid.get(k); if (!l) grid.set(k, (l = [])); l.push(i)
    minI = Math.min(minI, p[0], p[1], p[2]); maxI = Math.max(maxI, p[0], p[1], p[2])
  }
  let remaining = src.length
  const maxR = Math.ceil((maxI - minI) / S) + 2
  for (const c of dest) {
    if (!remaining) break
    const p = cells[c].p
    const gx = Math.floor(p[0] / S), gy = Math.floor(p[1] / S), gz = Math.floor(p[2] / S)
    let best = -1, bestD = Infinity
    for (let r = 0; r <= maxR; r++) {
      if (best >= 0 && (r - 1) * S > Math.sqrt(bestD)) break
      for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) for (let dz = -r; dz <= r; dz++) {
        if (Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dz)) !== r) continue
        const l = grid.get(`${gx + dx},${gy + dy},${gz + dz}`); if (!l) continue
        for (const i of l) {
          if (srcUsed[i]) continue
          const q = prev.cells[prev.inst2cell[i]].p
          const d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2
          if (d < bestD) { bestD = d; best = i }
        }
      }
    }
    if (best >= 0) { out[best] = c; destDone[c] = 1; srcUsed[best] = 1; remaining-- }
  }
}

/** Interned group ids: `${sceneId}/${group}` → 1..65535 ('' → 0). */
export class GroupIds {
  private map = new Map<string, number>()
  private names: string[] = ['']
  id(scene: string, group: string): number {
    if (!group) return 0
    const k = `${scene}/${group}`
    let v = this.map.get(k)
    if (v === undefined) { v = this.names.length; this.map.set(k, v); this.names.push(k) }
    return v
  }
  name(id: number): string { return this.names[id] ?? '' }
  /** group name without the scene prefix */
  group(id: number): string { const n = this.names[id] ?? ''; return n.slice(n.indexOf('/') + 1) }
}

const FLOOR_RISE_KINDS = new Set<MorphKind>(['rise', 'flip', 'scale', 'cut'])

/**
 * Write one layout row into the atlas data. `prev` is the placement this row is morphed FROM by `spec`
 * (needed for the order of sinking cells and their last pose).
 */
export function writeRow(data: Float32Array, rowIndex: number, cur: Placed, prev: Placed | null, spec: MorphSpec | null, gid: (group: string) => number, N = N_INSTANCES) {
  const base = rowIndex * ROWS_PER_LAYOUT * ATLAS_W * 4
  const { cells, inst2cell } = cur
  // arrival order: destination cells by their position, sinking sources by theirs, normalised together
  const key = spec?.order ?? 'x-asc'
  const probe: Cell[] = [], probeInst: number[] = []
  for (let i = 0; i < N; i++) {
    const c = inst2cell[i]
    if (c >= 0) { probe.push(cells[c]); probeInst.push(i) }
    else if (prev && prev.inst2cell[i] >= 0) { probe.push(prev.cells[prev.inst2cell[i]]); probeInst.push(i) }
  }
  const ord = normalisedOrder(probe, key, spec?.from)
  const orderOf = new Float32Array(N)
  probeInst.forEach((i, k) => { orderOf[i] = ord[k] })
  const sinkKind = spec && (spec.kind === 'scale' || spec.kind === 'cut') ? KIND_CODE[spec.kind] : KIND_CODE.sink
  const riseKind = spec && FLOOR_RISE_KINDS.has(spec.kind) ? KIND_CODE[spec.kind] : KIND_CODE.rise

  for (let i = 0; i < N; i++) {
    const o = base + i * TEX_PER_INST * 4
    const ci = inst2cell[i]
    if (ci >= 0) {
      const c = cells[ci], s = c.s ?? DEFAULT_SIZE
      const wasParked = !prev || prev.inst2cell[i] < 0
      const kind = c.kind ? KIND_CODE[c.kind] : wasParked ? riseKind : 0
      const flags = kind + 16 * (c.flight != null ? c.flight + 1 : 0) + 256 * (c.wave ? 1 : 0)
      data[o] = c.p[0]; data[o + 1] = c.p[1]; data[o + 2] = c.p[2]; data[o + 3] = c.tone
      data[o + 4] = s[0]; data[o + 5] = s[1]; data[o + 6] = s[2]; data[o + 7] = gid(c.group)
      data[o + 8] = c.yaw ?? 0; data[o + 9] = c.roll ?? 0; data[o + 10] = c.order ?? orderOf[i]; data[o + 11] = flags
    } else if (prev && prev.inst2cell[i] >= 0) {
      const c = prev.cells[prev.inst2cell[i]]
      data[o] = c.p[0]; data[o + 1] = c.p[1]; data[o + 2] = c.p[2]; data[o + 3] = c.tone
      data[o + 4] = 0; data[o + 5] = 0; data[o + 6] = 0; data[o + 7] = 0
      data[o + 8] = c.yaw ?? 0; data[o + 9] = c.roll ?? 0; data[o + 10] = orderOf[i]; data[o + 11] = sinkKind
    } else {
      for (let k = 0; k < 12; k++) data[o + k] = 0
    }
  }
}

export interface AABB { min: [number, number, number]; max: [number, number, number] }

/** Group AABBs of the interactive groups of a layout (rotation ignored; good enough for picking). */
export function groupBoxes(layout: Layout): Map<string, AABB> {
  const want = new Set(Object.entries(layout.groups ?? {}).filter(([, g]) => g.hover || g.click).map(([k]) => k))
  return boxesOf(layout.cells, g => want.has(g))
}

export function boxesOf(cells: Cell[], keep: (group: string) => boolean = () => true): Map<string, AABB> {
  const m = new Map<string, AABB>()
  for (const c of cells) {
    if (!c.group || !keep(c.group)) continue
    const s = c.s ?? DEFAULT_SIZE
    const r = Math.max(s[0], s[2]) / 2
    let b = m.get(c.group)
    if (!b) m.set(c.group, (b = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] }))
    const ext = [c.yaw ? r : s[0] / 2, s[1] / 2, c.yaw ? r : s[2] / 2]
    for (let k = 0; k < 3; k++) { b.min[k] = Math.min(b.min[k], c.p[k] - ext[k]); b.max[k] = Math.max(b.max[k], c.p[k] + ext[k]) }
  }
  return m
}
