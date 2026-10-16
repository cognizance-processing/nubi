import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { TONE, type Beat, type Cell } from '../../stage/types'
import { FIELDS, FIELD_BYTES } from '../../stage/data'
import hero from '../hero/index'
import scene from './index'
import { BATCHES, LAYERS, N_COLS, N_ROWS, SEQ, aggregate, codeLine, tableLayout, towersLayout, type ColumnsState } from './layouts'

const stateAt = (b: number): ColumnsState => scene.beats.slice(0, b + 1).reduce<ColumnsState>((s, x) => ({ ...s, ...(x.state ?? {}) }), { ...scene.initialState })
const layoutAt = (b: number, patch: Partial<ColumnsState> = {}) => scene.beats[b].layout(kit, { ...stateAt(b), ...patch })
const accentGroups = (cells: Cell[]) => new Set(cells.filter(c => c.tone === TONE.ACCENT).map(c => c.group))

test('columns: every beat and every read variant stays inside the cube budget', () => {
  for (let b = 0; b < scene.beats.length; b++) for (let r = -1; r < N_COLS; r++) {
    const n = layoutAt(b, { read: r }).cells.length
    assert.ok(n <= 3400, `${scene.beats[b].id} read=${r}: ${n} cells`)
  }
  assert.equal(layoutAt(0).cells.length, N_COLS * (4 + N_ROWS * 4), 'table: 8 × (4 header + 96 values)')
})

test('columns: one accent focus per beat (none until a column is read, then exactly that column)', () => {
  assert.equal(accentGroups(layoutAt(0).cells).size, 0)
  assert.equal(accentGroups(layoutAt(1).cells).size, 0)
  assert.deepEqual([...accentGroups(layoutAt(2).cells)], ['read'])
  for (let r = 0; r < N_COLS; r++) assert.deepEqual([...accentGroups(layoutAt(2, { read: r }).cells)], ['read'], `read=${r}`)
})

test('columns: table cell (row, i) and tower cell (row, i) share their slot, so group-rank matching pairs them exactly', () => {
  assert.equal(SEQ.length, N_ROWS * 4 + 4)
  assert.equal(new Set(SEQ.map(([r, i]) => `${r}:${i}`)).size, SEQ.length)
  const t = tableLayout({ read: -1 }), w = towersLayout(kit, { read: -1 }, true)
  for (let c = 0; c < N_COLS; c++) {
    const a = t.cells.filter(x => x.group === `col-${c}`), b = w.cells.filter(x => x.group === `col-${c}`)
    assert.equal(a.length, b.length)
    a.forEach((cell, k) => {
      const [r, i] = SEQ[k]
      if (r < 0) { assert.equal(cell.tone, TONE.INK); assert.equal(b[k].tone, TONE.INK); return }
      assert.equal(b[k].tone, cell.tone, 'a batch keeps its tone from table to tower')
      assert.equal(Math.floor(b[k].p[1]), Math.floor(r / (N_ROWS / LAYERS)), `row ${r} lands on its layer`)
      assert.ok(Math.abs((b[k].p[0] - w.cells.find(x => x.group === `col-${c}`)!.p[0]) - i) < 1e-9, `cell ${i} keeps its x slot`)
    })
  }
})

test('columns: a read column keeps its value cubes; the others collapse to one hidden-line slab per record batch', () => {
  const L = towersLayout(kit, { read: 6 }, true)
  assert.equal(L.cells.filter(c => c.group === 'read' && c.tone === TONE.ACCENT).length, N_ROWS * 4)
  for (let c = 0; c < N_COLS; c++) if (c !== 6) {
    const g = L.cells.filter(x => x.group === `col-${c}`)
    assert.equal(g.filter(x => x.tone === TONE.GHOST).length, BATCHES)
    assert.equal(g.filter(x => x.tone === TONE.INK).length, 4, 'lid kept')
  }
  assert.ok(L.cells.some(c => c.group === 'readout-value'), 'the chart window shows its value')
})

test('columns: copy and labels are derived from the data module', () => {
  const read = scene.callouts.find(c => c.id === 'chart')!
  const s = stateAt(2)
  assert.equal((read.sub as (s: ColumnsState) => string)(s), '28.6% of bytes · 1 of 8 columns')
  assert.equal(scene.controls[0].announce!(s), 'Reading revenue: 28.6% of bytes, 1 of 8 columns')
  assert.equal(codeLine(6), 'SELECT sum(revenue) FROM orders;  -- reads 1 of 8 columns', 'the round-1 code line is unchanged')
  assert.equal(aggregate(6).value, '5.05M')
  assert.equal(aggregate(4).value, '79.5k')
  assert.equal(FIELD_BYTES.length, FIELDS.length)
  for (let c = 0; c < N_COLS; c++) assert.ok(codeLine(c).length <= 63, codeLine(c))
  for (const c of scene.callouts) if (c.short) assert.ok(c.short.length <= 12, c.id)
  for (const b of scene.beats) assert.ok(b.caption.split(/\s+/).length <= 6, b.caption)
})

test('columns: picks and controls read a column; hover names field, Arrow type and byte share', () => {
  assert.deepEqual(scene.pick!.click!('col-3', stateAt(2)), { read: 3 })
  assert.equal(scene.pick!.click!('read', stateAt(2)), null)
  const h = scene.pick!.hover!('col-1', stateAt(2))!
  assert.equal(h.text, 'region · dictionary<uint8>')
  assert.equal(h.sub, '1 B per value · 3.6% of bytes')
  assert.equal(scene.pick!.hover!('read', stateAt(2))!.sub, 'being read · 28.6% of bytes')
  assert.deepEqual(scene.controls[0].set(stateAt(2), '2'), { read: 2 })
  assert.equal(scene.controls[0].options!.length, N_COLS)
})

test('columns: cameras stay in the house range and every transition turns gently (incl. the hand-off from the hero)', () => {
  const cams = [hero.beats[hero.beats.length - 1].camera, ...scene.beats.map((b: Beat<ColumnsState>) => b.camera)]
  for (const c of cams.slice(1)) {
    for (const k of [c, { ...c, ...c.mobile }]) {
      assert.ok(k.az >= -0.75 && k.az <= -0.05, `az ${k.az}`)
      assert.ok(k.el >= 0.12 && k.el <= 0.62, `el ${k.el}`)
    }
  }
  for (let i = 1; i < cams.length; i++) {
    assert.ok(Math.abs(cams[i].az - cams[i - 1].az) <= 0.45 + 1e-9, `az step ${i}`)
    assert.ok(Math.abs(cams[i].el - cams[i - 1].el) <= 0.35 + 1e-9, `el step ${i}`)
  }
})
