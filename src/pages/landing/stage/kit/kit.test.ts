/** Voxel kit: counts, bounds and invariants (storyboard §3.1). */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from './index'
import { FONT } from './font'
import { TONE, type Cell } from '../types'

const bounds = (cells: Cell[]) => {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity]
  for (const c of cells) for (let k = 0; k < 3; k++) {
    const h = (c.s ?? [0.95, 0.95, 0.95])[k] / 2
    lo[k] = Math.min(lo[k], c.p[k] - h); hi[k] = Math.max(hi[k], c.p[k] + h)
  }
  return { lo, hi }
}
const sane = (cells: Cell[]) => cells.every(c => c.p.every(Number.isFinite) && (c.s ?? [1, 1, 1]).every(v => v > 0) && c.tone >= 0 && c.tone <= 9)

test('rng is seeded and deterministic', () => {
  const a = kit.rng(42), b = kit.rng(42)
  for (let i = 0; i < 5; i++) assert.equal(a(), b())
  const v = kit.rng(1)(); assert.ok(v >= 0 && v < 1)
})

test('voxelText: one cube per lit pixel; width = Σ advances − 1px', () => {
  const t = kit.voxelText({ text: '5.05M', at: [0, 0, 0], px: 0.5 })
  const lit = [...'5.05M'].reduce((n, ch) => n + FONT[ch].join('').split('').filter(b => b === '1').length, 0)
  assert.equal(t.cells.length, lit)
  assert.equal(t.width, kit.measureVoxelText('5.05M', 0.5))
  assert.ok(Math.abs(t.width - (4 + 2 + 4 + 4 + 6 - 1) * 0.5) < 1e-9)
  assert.ok(sane(t.cells))
})

test('browserWindow 52×34: bounds, screen tiles and anchors', () => {
  const w = kit.browserWindow({ at: [0, 0, 0], w: 52, h: 34, title: 'T', url: 'u' })
  const { lo, hi } = bounds(w.cells)
  assert.ok(lo[0] >= -26.01 && hi[0] <= 26.01, `x ${lo[0]}..${hi[0]}`)
  assert.ok(lo[1] >= -0.01 && hi[1] <= 34.01, `y ${lo[1]}..${hi[1]}`)
  assert.equal(w.cells.filter(c => c.group === 'win-screen').length, 50 * 30)
  assert.deepEqual(w.screen, { x0: -25, x1: 25, y0: 1, y1: 31, zFront: 0.4 })
  for (const k of ['title', 'url', 'status']) assert.ok(w.anchors[k], k)
  assert.equal(w.texts.length, 2)
  assert.ok(sane(w.cells))
})

test('small window (station) stays within 6 × 4.4', () => {
  const w = kit.browserWindow({ at: [0, 0, 0], w: 6, h: 4.4, titleBar: 1, small: true })
  const { lo, hi } = bounds(w.cells)
  assert.ok(hi[0] - lo[0] <= 6.01 && hi[1] - lo[1] <= 4.41)
  assert.equal(w.cells.filter(c => c.group === 'win-screen').length, 1)
})

test('cylinder: n × h ring cells + cap; radius bound', () => {
  const c = kit.cylinder({ at: [0, 0, 0], r: 5.2, h: 9, n: 30 })
  const ring = c.cells.filter(x => x.s![1] > 0.9)
  assert.equal(ring.length, 270)
  assert.equal(ring.filter(x => x.tone === TONE.INK).length, 90)
  for (const x of c.cells) assert.ok(Math.hypot(x.p[0], x.p[2]) <= 5.2 + 1e-6)
})

test('person: height 9.4 at scale 1, badge group when asked', () => {
  const p = kit.person({ at: [0, 0, 0], badge: { region: 'AMER', size: 'l' } })
  const { lo, hi } = bounds(p.cells.filter(c => c.group === 'person'))
  assert.ok(Math.abs(hi[1] - 9.4) < 0.01, `height ${hi[1]}`)
  assert.ok(Math.abs(lo[1]) < 0.01)
  assert.ok(p.cells.some(c => c.group === 'person-badge'))
  assert.equal(p.texts[0].text, 'AMER')
})

test('barChart: one group per bar, heights follow values', () => {
  const b = kit.barChart({ at: [0, 0, 0], values: [1, 2, 4], max: 4, height: 8, sel: 2 })
  assert.equal(b.cells.filter(c => c.group === 'bar-0').length, 2)
  assert.equal(b.cells.filter(c => c.group === 'bar-2').length, 8)
  assert.ok(b.cells.filter(c => c.group === 'bar-2').every(c => c.tone === TONE.ACCENT))
  assert.ok(b.groups['bar-1'].hover)
})

test('table floor 8×24: 8 column groups of (24 + header) × cellW cells', () => {
  const t = kit.table({ plane: 'floor', at: [0, 0, 0], cols: 8, rows: 24, cellW: 4, names: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] })
  assert.equal(t.cells.length, 8 * 25 * 4)
  assert.equal(new Set(t.cells.map(c => c.group)).size, 8)
  assert.equal(t.texts.length, 8)
  const { hi } = bounds(t.cells)
  assert.ok(hi[1] <= 0.61)
})

test('table screen: hidden rows are flat GHOST', () => {
  const t = kit.table({ plane: 'screen', at: [0, 20, 0], rows: 4, cols: 5, hidden: r => r > 0 })
  const r1 = t.cells.filter(c => c.group === 'table-r1')
  assert.ok(r1.length > 0 && r1.every(c => c.tone === TONE.GHOST && c.s![2] <= 0.14))
  assert.ok(t.cells.filter(c => c.group === 'table-r0').some(c => c.tone === TONE.ACCENT))
})

test('columns: 8 towers of 4×4×6, the read tower is accent below its ink top', () => {
  const c = kit.columns({ n: 8, read: 6, ghost: true })
  assert.equal(c.cells.length, 8 * 96)
  const t6 = c.cells.filter(x => x.group === 'col-6')
  assert.equal(t6.filter(x => x.tone === TONE.ACCENT).length, 80)
  assert.ok(c.cells.filter(x => x.group === 'col-0' && x.tone === TONE.GHOST).length === 80)
})

test('every builder yields sane cells', () => {
  const parts = [
    kit.kpiTile({ at: [0, 10, 0], value: '79.5k', caption: 'Orders' }), kit.lineChart({ at: [0, 0, 0], values: [1, 3, 2], max: 3, height: 6 }),
    kit.hbarChart({ at: [0, 10, 0], items: [['A', 1], ['B', 2]], max: 2, len: 10 }), kit.rack({ stored: 1 }), kit.badge({ at: [0, 5, 0], region: 'EMEA' }),
    kit.mcpPort({ at: [0, 0, 0] }), kit.engineChip({ at: [0, 5, 0] }), kit.chatPanel({ messages: [{ role: 'user', text: 'hi' }, { role: 'tool', text: 'get_context()' }] }),
    kit.pane({ w: 10, h: 8 }), kit.placeholder({ at: [0, 0, 0], w: 6, h: 4 }),
  ]
  for (const p of parts) { assert.ok(p.cells.length > 0); assert.ok(sane(p.cells)) }
  assert.equal(kit.placeholder({ at: [0, 0, 0], w: 6, h: 4 }).cells.length, 16)
})

test('orderBy normalises to 0..1', () => {
  const cells: Cell[] = [0, 5, 10].map(x => ({ p: [x, 0, 0], tone: 0, group: '' }))
  kit.orderBy(cells, 'x-desc')
  assert.deepEqual(cells.map(c => c.order), [1, 0.5, 0])
})
