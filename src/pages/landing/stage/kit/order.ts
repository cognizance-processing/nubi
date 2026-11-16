/** Seeded randomness and arrival-order helpers. Scenes never call Math.random(). */
import type { Cell, MorphSpec, Vec3 } from '../types'

/** mulberry32: a small, fast, seeded PRNG returning [0, 1). */
export function rng(seed = 1): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Integer hash of a string (stable ids, seeds). */
export function hashStr(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

export type OrderKey = NonNullable<MorphSpec['order']>

/** A raw (un-normalised) sort key for a cell under an order spec. Larger = later. */
export function orderKeyFn(key: OrderKey, from: Vec3 = [0, 0, 0], seed = 7): (c: Cell, i: number) => number {
  if (typeof key === 'function') return c => key(c)
  switch (key) {
    case 'y-desc': return c => -c.p[1]
    case 'y-asc': return c => c.p[1]
    case 'x-asc': return c => c.p[0]
    case 'x-desc': return c => -c.p[0]
    case 'z-asc': return c => c.p[2]
    case 'z-desc': return c => -c.p[2]
    case 'radial': return c => Math.hypot(c.p[0] - from[0], c.p[1] - from[1], c.p[2] - from[2])
    case 'random': { const r = rng(seed); const cache: number[] = []; return (_c, i) => (cache[i] ??= r()) }
  }
  return () => 0
}

/** Normalise raw keys of `cells` to 0..1 (min → 0, max → 1). */
export function normalisedOrder(cells: Cell[], key: OrderKey, from?: Vec3): number[] {
  const f = orderKeyFn(key, from)
  const raw = cells.map((c, i) => f(c, i))
  let lo = Infinity, hi = -Infinity
  for (const v of raw) { if (v < lo) lo = v; if (v > hi) hi = v }
  const span = hi - lo || 1
  return raw.map(v => (v - lo) / span)
}

/** Write `order` (0..1) on every cell from an order key; returns the same array. */
export function orderBy<C extends Cell>(cells: C[], key: OrderKey, from?: Vec3): C[] {
  const o = normalisedOrder(cells, key, from)
  cells.forEach((c, i) => { c.order = o[i] })
  return cells
}

/** Remap the `order` of cells into a sub-range [a, b] (compose phases of a build). */
export function orderRange<C extends Cell>(cells: C[], a: number, b: number, key?: OrderKey, from?: Vec3): C[] {
  if (key) orderBy(cells, key, from)
  cells.forEach(c => { c.order = a + (b - a) * (c.order ?? 0) })
  return cells
}
