import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { TONE, type Beat, type Cell } from '../../stage/types'
import { FIELDS, FIELD_BYTES } from '../../stage/data'
import hero from '../hero/index'
import scene from './index'
import { BATCHES, LAYER_H, N_COLS, N_ROWS, SEQ, aggregate, codeLine, tableLayout, towerD, towersLayout, type ColumnsState } from './layouts'

const stateAt = (b: number): ColumnsState => scene.beats.slice(0, b + 1).reduce<ColumnsState>((s, x) => ({ ...s, ...(x.state ?? {}) }), { ...scene.initialState })
const layoutAt = (b: number, patch: Partial<ColumnsState> = {}) => scene.beats[b].layout(kit, { ...stateAt(b), ...patch })
const accentGroups = (cells: Cell[]) => new Set(cells.filter(c => c.tone === TONE.ACCENT).map(c => c.group))
const col = (cells: Cell[], c: number) => cells.filter(x => x.group === `col-${c}`)
const xSpan = (c: Cell) => [c.p[0] - c.s![0] / 2, c.p[0] + c.s![0] / 2]

test('columns: every beat and every read variant stays inside the cube budget', () => {
  for (let b = 0; b < scene.beats.length; b++) for (let r = -1; r < N_COLS; r++) {
    const n = layoutAt(b, { read: r }).cells.length
    assert.ok(n <= 3400, `${scene.beats[b].id} read=${r}: ${n} cells`)
  }
  assert.equal(layoutAt(0).cells.length, N_COLS * (1 + N_ROWS), 'table: 8 × (1 header + 24 values)')
})

test('columns: the first beat is ONE table (touching columns, one continuous header, rows striped across)', () => {
  const t = tableLayout({ read: -1 })
  const heads = t.cells.filter(c => c.tone === TONE.INK).sort((a, b) => a.p[0] - b.p[0])
  assert.equal(heads.length, N_COLS)
  for (let c = 1; c < N_COLS; c++) assert.ok(xSpan(heads[c])[0] - xSpan(heads[c - 1])[1] <= 0.05, `header gap before column ${c}`)
  // same row → same tone in every column; neighbouring rows alternate
  const byZ = new Map<number, Set<number>>()
  for (const c of t.cells) if (c.tone !== TONE.INK) { const k = Math.round(c.p[2] * 100); byZ.set(k, (byZ.get(k) ?? new Set()).add(c.tone)) }
  const zs = [...byZ.keys()].sort((a, b) => a - b)
  assert.equal(zs.length, N_ROWS)
  zs.forEach((z, i) => {
    assert.equal(byZ.get(z)!.size, 1, `row ${i} has one tone across the table`)
    if (i) assert.notEqual([...byZ.get(z)!][0], [...byZ.get(zs[i - 1])!][0], `rows ${i - 1}/${i} alternate`)
  })
  for (let c = 1; c < N_COLS; c++) {
    const a = col(t.cells, c - 1).find(x => x.tone !== TONE.INK)!, b = col(t.cells, c).find(x => x.tone !== TONE.INK)!
    assert.ok(xSpan(b)[0] - xSpan(a)[1] <= 0.12, `columns ${c - 1}/${c} touch (hairline only)`)
  }
})

test('columns: one accent focus per beat (none until a column is read, then exactly that column)', () => {
  assert.equal(accentGroups(layoutAt(0).cells).size, 0)
  assert.equal(accentGroups(layoutAt(1).cells).size, 0)
  assert.deepEqual([...accentGroups(layoutAt(2).cells)], ['col-6'])
  for (let r = 0; r < N_COLS; r++) assert.deepEqual([...accentGroups(layoutAt(2, { read: r }).cells)], [`col-${r}`], `read=${r}`)
})

test('columns: table row r becomes pillar layer r (group-rank matching pairs them exactly), header becomes the lid', () => {
  assert.equal(SEQ.length, N_ROWS + 1)
  assert.equal(new Set(SEQ).size, SEQ.length)
  const t = tableLayout({ read: -1 }), w = towersLayout(kit, { read: -1 }, true)
  for (let c = 0; c < N_COLS; c++) {
    const a = col(t.cells, c), b = col(w.cells, c)
    assert.equal(a.length, b.length)
    a.forEach((cell, k) => {
      const r = SEQ[k]
      if (r < 0) { assert.equal(cell.tone, TONE.INK); assert.equal(b[k].tone, TONE.INK); assert.ok(b[k].p[1] > N_ROWS * LAYER_H, 'lid on top'); return }
      assert.equal(b[k].tone, cell.tone, 'a row keeps its stripe tone as a layer')
      assert.ok(Math.abs(b[k].p[1] - (r + 0.5) * LAYER_H) < 1e-9, `row ${r} lands on layer ${r}`)
    })
  }
})

test('columns: the split files the table row by row (back row first) and puts the lids on last', () => {
  const w = towersLayout(kit, { read: -1 }, true)
  for (let c = 0; c < N_COLS; c++) {
    const g = col(w.cells, c)
    const lid = g[0], rows = g.slice(1).sort((a, b) => a.p[1] - b.p[1])
    for (let i = 1; i < rows.length; i++) assert.ok(rows[i].order! > rows[i - 1].order!, 'higher layers file later')
    assert.ok(lid.order! > rows[rows.length - 1].order!, 'lid last')
  }
})

test('columns: a pillar\'s depth is its bytes per value (revenue, float64, is the deepest)', () => {
  const w = towersLayout(kit, { read: -1 }, true)
  for (let c = 0; c < N_COLS; c++) {
    assert.ok(Math.abs(towerD(c) / towerD(0) - FIELD_BYTES[c] / FIELD_BYTES[0]) < 1e-9)
    assert.ok(Math.abs(col(w.cells, c)[0].s![2] - towerD(c)) < 1e-9)
  }
  assert.equal(FIELD_BYTES.indexOf(Math.max(...FIELD_BYTES)), FIELDS.indexOf('revenue'))
})

test('columns: a read column keeps its values; the others collapse to one hidden-line box per record batch', () => {
  const L = towersLayout(kit, { read: 6 }, true)
  assert.equal(col(L.cells, 6).filter(c => c.tone === TONE.ACCENT).length, N_ROWS)
  for (let c = 0; c < N_COLS; c++) if (c !== 6) {
    const g = col(L.cells, c)
    assert.equal(g.filter(x => x.tone === TONE.GHOST).length, BATCHES)
    assert.equal(g.filter(x => x.tone === TONE.INK).length, 1, 'lid kept')
  }
  assert.ok(L.cells.some(c => c.group === 'readout'), 'the chart window stands in front')
})

test('columns: labels stay within the rules (≤ 3 callouts per beat, ≤ 28-char lines, names as surface text)', () => {
  const s = stateAt(2)
  for (const b of scene.beats) assert.ok(scene.callouts.filter(c => c.beats.includes(b.id)).length <= 3, b.id)
  for (const c of scene.callouts) {
    if (c.short) assert.ok(c.short.length <= 12, c.id)
    for (const t of [c.text, c.sub]) if (t) assert.ok((typeof t === 'function' ? t(s) : t).length <= 28, `${c.id}: ${typeof t === 'function' ? t(s) : t}`)
  }
  for (const f of FIELDS) {
    assert.ok(scene.surface.some(t => t.beats.includes('table') && t.text === f), `${f} printed on the header`)
  }
  for (let c = 0; c < N_COLS; c++) {
    const shown = scene.surface.filter(t => t.beats.includes('read') && t.id.startsWith('spine') && t.id.endsWith(`-${c}`)).map(t => (typeof t.text === 'function' ? t.text(s) : t.text)).filter(Boolean)
    assert.deepEqual(shown, [FIELDS[c]], `exactly one spine label for ${FIELDS[c]}`)
  }
})

test('columns: copy and labels are derived from the data module', () => {
  const read = scene.callouts.find(c => c.id === 'chart')!
  const s = stateAt(2)
  assert.equal((read.sub as (s: ColumnsState) => string)(s), '28.6% of bytes · 1 column')
  assert.equal(scene.controls[0].announce!(s), 'Reading revenue: 28.6% of bytes, 1 of 8 columns')
  assert.equal(codeLine(6), 'SELECT sum(revenue) FROM orders;  -- reads 1 of 8 columns', 'the round-1 code line is unchanged')
  assert.equal(aggregate(6).value, '5.05M')
  assert.equal(aggregate(4).value, '79.5k')
  for (let c = 0; c < N_COLS; c++) assert.ok(codeLine(c).length <= 63, codeLine(c))
  for (const b of scene.beats) assert.ok(b.caption.split(/\s+/).length <= 6, b.caption)
})

test('columns: picks and controls read a column; hover names field, Arrow type and byte share', () => {
  assert.deepEqual(scene.pick!.click!('col-3', stateAt(2)), { read: 3 })
  assert.equal(scene.pick!.click!('col-6', stateAt(2)), null, 'clicking the column already read does nothing')
  assert.equal(scene.pick!.click!('readout', stateAt(2)), null)
  const h = scene.pick!.hover!('col-1', stateAt(2))!
  assert.equal(h.text, 'region · dictionary<uint8>')
  assert.equal(h.sub, '1 B per value · 3.6% of bytes')
  assert.equal(scene.pick!.hover!('col-6', stateAt(2))!.sub, 'being read · 28.6% of bytes')
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
  for (let i = 1; i < cams.length; i++) for (const m of [false, true]) {
    const a = m ? { ...cams[i - 1], ...cams[i - 1].mobile } : cams[i - 1], b = m ? { ...cams[i], ...cams[i].mobile } : cams[i]
    assert.ok(Math.abs(b.az - a.az) <= 0.45 + 1e-9, `az step ${i}${m ? ' (mobile)' : ''}`)
    assert.ok(Math.abs(b.el - a.el) <= 0.35 + 1e-9, `el step ${i}${m ? ' (mobile)' : ''}`)
  }
})
